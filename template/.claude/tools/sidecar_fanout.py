#!/usr/bin/env python3
"""Fan a job list across sidecar launchers -- the cross-transport `dispatch.js`.

WHY THIS EXISTS. `Workflow` and `Agent` run on the session's own endpoint, so a provider seat
reaches every other model through a sidecar Bash call, one job per call. Hand-rolling that loop is
what makes a seat quietly downgrade itself to whatever is in-transport: the correct row is one hop
away and the hop costs a bespoke script every time. This makes the hop cost what an in-harness
fan-out costs. `model_registry.py for-role <tier>` names the row; this dispatches to it.

WHAT IT IS NOT. It writes no ledger and needs no reader change: every launcher's `-R` already
appends to the shared spend ledger, and `tools/orchestration_metrics.py collect_sidecar()` already
reads it keyed on `-l`. Passing `-R` and `-l` per job is the whole attribution story -- which is why
`-l` is REQUIRED here: an unlabelled row is skipped by that reader, so a job without one is spend
you cannot account for.

IT NEVER AUTO-RETRIES. Every job is a billed call. A failed run is REPORTED -- with its resume id
when the launcher persisted one -- and the caller decides. An automatic retry doubles the bill on
exactly the failures (rate limit, quota, context) that a second immediate attempt cannot fix.

USAGE
    python3 .claude/tools/sidecar_fanout.py <jobs.json> [--max-parallel N] [--dry-run]
                                            [--out-dir DIR] [--authorize]
    python3 .claude/tools/sidecar_fanout.py --status <out-dir>

JOBS FILE -- a JSON list. Per job:
    label       required  attribution key; becomes `-l` and names the output files
    alias       required  registry alias or id, e.g. "muse", "opus"
    promptFile  required  path to the prompt; NEVER argv (Windows caps argv near 8K)
    effort                vendor rung -> `-e`
    disclosure            bare|pointer|full -> `-D`
    shape                 any|survey|review|author -> `-G`
    contextFiles          list of paths -> repeated `-C`
    schemaFile            path -> `-S`
    workdir               -> `-d`
    transport             override; defaults to the alias's own registry row
    extraArgs             list, appended verbatim, for a flag this wrapper does not model

EXIT: 0 every job exited 0; 1 at least one did not; 2 the jobs file or a job is unusable.
"""
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent          # .claude/
REPO = ROOT.parent
sys.path.insert(0, str(ROOT / "tools"))
import model_registry  # noqa: E402
import sidecar_launch  # noqa: E402  -- owns the shell choice; see plan_job's argv[0]
import sidecar_resume_check as stream_check  # noqa: E402  -- owns stream-event classification

# A PROCESS cap, chosen here rather than read from the registry's `limits.concurrency`: that field
# is a vendor API/token ceiling (or null), not a count of child CLIs. The default rests on one
# 7-wide codex fan-out that died 403 at a single instant, cause unverified
# (`gotcha_luna_sidecar_concurrency_cap`). That is a hypothesis, not a measurement, so the number
# is a flag rather than a rule.
DEFAULT_MAX_PARALLEL = 4

# The review deliverable's shape, shipped so `-S` needs no caller decision. Structurally identical
# to `review_fanout.js` FINDINGS_SCHEMA: both engines return one object, so a consumer never has to
# know which produced it.
DEFAULT_REVIEW_SCHEMA = ROOT / "schemas" / "review_findings.json"

# Exit codes the launchers share (sidecar_common.sh header). Reported by name, never renumbered.
LAUNCHER_EXITS = {
    0: "ok",
    1: "the child CLI failed or stopped early",
    2: "bad usage, unresolvable model, or unusable registry",
    3: "credential missing",
    4: "child CLI missing",
    5: "band gate refusal (pass --authorize to override)",
    6: "balance floor unmet (NOT overridable)",
    7: "model unavailable in the registry",
    8: "provider quota-band ceiling exceeded (pass --authorize to override)",
    9: "stall watchdog killed a child with no work event (resume deliberately with -r)",
    10: "provider usage limit: the run stopped, or the provider's exhausted marker refused the "
        "launch (NOT overridable; route to another provider)",
}
USAGE_LIMIT_EXIT = 10

# How often `_wait_detached` re-checks a detached job's `<record>.exit`. No timeout gates this
# loop (Design §3, sidecar-detach.md: "The fan-out has no timeout today, so it gets none") -- only
# the job's own exit file, or its detached pid dying with none written, ends the wait. Overridable
# so a proof can poll fast without the real interval flaking a multi-second fake job's timing.
POLL_INTERVAL_SEC = float(os.environ.get("SIDECAR_FANOUT_POLL_SEC", "0.5"))


class JobError(Exception):
    """A job that cannot be dispatched. Raised before anything is spent."""


# Which paths a comparison's freeze must actually cover. Engine-generated churn outside these
# (engine `.import` sidecars, asset caches) is expected in any fresh worktree and is not a thaw.
FROZEN_PATHS = (".claude",)


def frozen_input_error(workdir):
    """None if `workdir` is a frozen input, else why it is not.

    A COMPARISON run's input is a control. If it moves between arms, a later arm reading an applied
    fix reports the defect ABSENT -- byte-identical to a genuine miss -- so the comparison scores the
    orchestrator's own edit as that model's blind spot. Nothing downstream can detect this: the run
    record's `harnessSession` sha does not move for uncommitted edits, so every arm reports the same
    provenance while having read different bytes (`orchestration` section 0).

    A LINKED worktree at a DETACHED head is the structural answer: edits to the main checkout cannot
    reach it, so the freeze does not depend on anyone remembering the rule.
    """
    if not workdir:
        return ("no `workdir` -- a comparison arm must read a frozen worktree, not the live tree")
    if not os.path.isdir(workdir):
        return f"workdir does not exist: {workdir}"

    def git(*a):
        r = subprocess.run(["git", "-C", workdir, *a], capture_output=True, text=True)
        return r.returncode, (r.stdout or "").strip()

    rc, gitdir = git("rev-parse", "--absolute-git-dir")
    if rc != 0:
        return f"workdir is not a git tree: {workdir}"
    if "worktrees" not in gitdir.replace("\\", "/").split("/"):
        return (f"workdir is the MAIN checkout, which you can edit mid-run. Use a linked worktree: "
                f"git worktree add --detach <path> <commit>")
    if git("symbolic-ref", "-q", "HEAD")[0] == 0:
        return ("worktree HEAD is on a BRANCH -- a commit or checkout moves it under the arms. "
                "Re-create it with `git worktree add --detach`.")
    # Dirt in the COMPARED INPUT only. A fresh worktree is never globally clean: the engine rewrites
    # every `.import` sidecar on first open (`gotcha_fresh_worktree_import_cache_mass_false_red`),
    # and refusing on that would make the guard fire on every correct setup -- a guard that matches
    # the noun instead of the action, which gets disabled rather than obeyed.
    rc, dirty = git("status", "--porcelain", "--", *FROZEN_PATHS)
    # `rc != 0` is UNKNOWN, not clean. Treating a failed status as frozen made the one command that
    # can detect a thaw fail open: the arms would run, the comparison would look controlled, and
    # nothing downstream can tell that afterwards.
    if rc != 0:
        return ("cannot read the worktree's status, so the input cannot be shown frozen: "
                "`git -C %s status --porcelain` failed" % workdir)
    if dirty:
        listed = "\n  ".join(dirty.splitlines()[:5])
        return ("the worktree's compared input is dirty, so it is not frozen:\n  " + listed
                + "\n  (checked: " + ", ".join(FROZEN_PATHS) + ")")
    return None


def load_jobs(path):
    try:
        with open(path, encoding="utf-8") as fh:
            jobs = json.load(fh)
    except Exception as err:
        raise JobError(f"cannot read jobs file {path}: {err}")
    if not isinstance(jobs, list) or not jobs:
        raise JobError(f"{path} must hold a non-empty JSON list of jobs")
    return jobs


def _posix(p):
    """argv for a BASH child, so every path is POSIX -- always, not only when it looks wrong.

    `str(Path)` on Windows yields backslashes, and bash reads a backslash as an escape: the launcher
    path collapsed to `C:UsersjmundGame_Dev...` and every job died exit 127 before the launcher ran.
    The registry stores `launcher` repo-relative already, and cwd is the repo, so nothing here needs
    an absolute path.
    """
    return str(p).replace("\\", "/")


def plan_job(job, data, out_dir, authorize):
    """One job -> (label, argv, stdout path). Validates everything BEFORE any process starts.

    Every refusal happens in this pass, so a bad job in a 10-job file costs nothing rather than
    nine paid runs and one error.
    """
    if not isinstance(job, dict):
        raise JobError(f"each job must be an object, got {type(job).__name__}")
    label = job.get("label")
    alias = job.get("alias")
    prompt = job.get("promptFile")
    for name, val in (("label", label), ("alias", alias), ("promptFile", prompt)):
        if not val:
            raise JobError(f"job {label or '<unlabelled>'}: `{name}` is required")
    if not os.path.exists(prompt):
        raise JobError(f"job {label}: promptFile not found: {prompt}")
    # `label` names four output files AND keys the spend ledger, so it must be a bare filename:
    # a `/` or `..` writes outside --out-dir, or dies as a raw OSError inside a worker thread --
    # after this function's docstring promised every refusal happens before anything starts.
    if str(label) in (".", "..") or str(label) != os.path.basename(str(label).replace("\\", "/")):
        raise JobError(f"job {label!r}: `label` names this job's output files and its ledger row -- "
                       f"it must be a bare filename, with no path separator and no `..`")

    try:
        entry = model_registry.resolve(alias, data)
    except Exception as err:
        raise JobError(f"job {label}: {err}")

    # A `transport` override selects the LAUNCHER; the alias resolves against a registry row that
    # already names its own. Pairing them freely sends one transport's alias to another's launcher,
    # which authenticates, bills, and then fails on a model it has never heard of. Keep the key --
    # a job naming its transport is self-documenting -- but let it only AGREE.
    transport = job.get("transport") or entry["transport"]
    if transport != entry["transport"]:
        raise JobError(f"job {label}: transport {transport!r} does not own {alias} -- the registry "
                       f"places it on {entry['transport']!r}, and a row's transport names the "
                       f"launcher that speaks its API. Drop the override, or pick an alias on "
                       f"{transport}.")
    # A bare string here iterates per CHARACTER, so a malformed job passes planning and dispatches a
    # billed launcher with argv full of single letters.
    for key in ("contextFiles", "extraArgs"):
        if job.get(key) is not None and not isinstance(job[key], list):
            raise JobError(f"job {label}: `{key}` must be a list, not "
                           f"{type(job[key]).__name__} -- a string iterates per character.")
    # An excluded row is out of the set a dispatcher chooses from -- name the roster rather than
    # substituting one, because choosing the replacement needs the task's shape and the budget.
    if not model_registry.model_available(entry["id"], data):
        why = model_registry.unavailable_reasons(data).get(entry["id"], "excluded")
        roster = ", ".join(sorted(m["alias"] for m in model_registry.available_models(data)
                                  if m["transport"] == transport)) or "none"
        raise JobError(f"job {label}: {alias} is unavailable ({why}). "
                       f"Available on {transport}: {roster}. Re-select under the ladder.")

    launcher = (model_registry.transport_meta(transport, data) or {}).get("launcher")
    if not launcher:
        raise JobError(f"job {label}: transport {transport} registers no launcher -- cannot hop")

    out_dir = Path(out_dir)
    # argv[0] comes from sidecar_launch.git_bash(), never the literal "bash": Windows Python
    # resolves a bare `bash` to the System32 WSL shim, a different filesystem view whose $HOME
    # holds none of this user's credentials. Measured here 2026-09-08 -- the codex jobs exited 3
    # ("run claude-code-proxy codex auth login") on a machine that is logged in, and the opencode
    # jobs died on a litellm proxy started under /mnt/c. Both symptoms name the launcher, not the
    # shell. sidecar_launch owns this choice for every Python caller.
    argv = [sidecar_launch.git_bash(), _posix(launcher), "-m", alias, "-f", _posix(prompt),
            "-l", label,                                   # attribution: the metrics reader keys on
            "-X",                                          # every fan-out child runs detached
            "-R", _posix(attempt_record_path(out_dir, label))]  # unlabelled rows are skipped by it
    for flag, key in (("-e", "effort"), ("-D", "disclosure"), ("-G", "shape"),
                      ("-S", "schemaFile"), ("-d", "workdir")):
        if job.get(key):
            argv += [flag, _posix(job[key])]
    for cf in job.get("contextFiles") or []:
        if not os.path.exists(cf):
            raise JobError(f"job {label}: contextFile not found: {cf}")
        argv += ["-C", _posix(cf)]
    # The ladder pins this to the EFFORT rung, not the shape: a `max` run compacts and returns
    # prose without `-P` (model_ladder_evidence.md, §Role guidance). Schema and review shape stay as
    # additional triggers.
    # -P is also the stall watchdog's switch (sc_run_watched watches this stream), so EVERY job
    # gets one: an unattended fan-out child is the exact case the watchdog exists for.
    argv += ["-P", _posix(out_dir / f"{label}.stream")]
    # `-P` alone only lets the child RESUME after a compaction; `-S` is what makes it resume into a
    # STRUCTURED deliverable rather than handing back the compaction summary as prose. A review
    # lens therefore gets the canonical schema by default -- remembering the pair is exactly what
    # does not happen, and the prose that came back from a fan-out missing it was read as the
    # model's ceiling rather than as the dispatch error it was.
    if job.get("shape") == "review" and not job.get("schemaFile"):
        if DEFAULT_REVIEW_SCHEMA.is_file():
            argv += ["-S", _posix(DEFAULT_REVIEW_SCHEMA)]
        else:
            raise JobError(f"job {label}: shape `review` needs a schema and the default is missing "
                           f"({DEFAULT_REVIEW_SCHEMA}). Pass `schemaFile`, or restore that file -- "
                           f"a review lens without one returns prose on any compaction.")
    if authorize:
        argv.append("-A")
    argv += [str(a) for a in (job.get("extraArgs") or [])]
    return label, argv, _posix(out_dir / f"{label}.out.json")


def attempt_record_path(out_dir, label):
    """The next unused attempt's record path for `label` under `out_dir`.

    Attempt 1 keeps the label's plain `<label>.record.json` name, so a job's first run writes the
    same path this tool always has (an existing consumer sees no change). `-X` refuses a used
    record path, checked against its `.out`/`.err`/`.exit`/`.pid` siblings -- so relaunching the
    same jobs file into the same --out-dir, the only way this tool re-plans a label it already
    ran, steps the attempt number until it finds one none of those four siblings claim. A prior
    attempt's files are never touched: this only ever picks a path, never deletes or renames one.
    """
    out_dir = Path(out_dir)
    n = 1
    while True:
        name = f"{label}.record.json" if n == 1 else f"{label}.attempt{n}.record.json"
        record = out_dir / name
        if not any(Path(str(record) + suffix).exists()
                   for suffix in (".out", ".err", ".exit", ".pid")):
            return record
        n += 1


def latest_attempt_record_path(out_dir, label):
    """The highest-numbered attempt's record path for `label` that some run has actually touched.

    Mirrors attempt_record_path's own numbering so the two can never disagree on what "attempt N"
    means. "Touched" means at least one `.out`/`.err`/`.exit`/`.pid` sibling exists; an untouched
    candidate is only where the NEXT attempt would land, not one anyone dispatched. None if the
    label has never been planned into this out_dir at all.
    """
    out_dir = Path(out_dir)
    best = None
    n = 1
    while True:
        name = f"{label}.record.json" if n == 1 else f"{label}.attempt{n}.record.json"
        record = out_dir / name
        touched = any(Path(str(record) + suffix).exists()
                      for suffix in (".out", ".err", ".exit", ".pid"))
        if not touched:
            return best
        best = record
        n += 1


def resume_id(record_path):
    """The launcher's persisted session id, so a failure can be resumed DELIBERATELY."""
    try:
        with open(record_path, encoding="utf-8") as fh:
            rec = json.load(fh)
        return rec.get("sessionId") or rec.get("session_id")
    except Exception:
        return None


def freeze_fingerprint(workdir, extra_paths=()):
    """Digest of the compared input: the worktree HEAD, the CONTENT of every dirty path under
    FROZEN_PATHS, and the content of each caller-named `extra_paths` entry.

    Taken on both sides of each child so a mid-run write is attributable to the arms that were still
    running when it landed, rather than invisible to all of them.
    """
    if not workdir:
        return None
    def git(*a):
        r = subprocess.run(["git", "-C", workdir, *a], capture_output=True, text=True)
        return (r.stdout or "") if r.returncode == 0 else "?"

    status = git("status", "--porcelain", "--", *FROZEN_PATHS)
    parts = [git("rev-parse", "HEAD"), status]

    # Status output names the changed paths, never their CONTENT -- so once a path is dirty, every
    # later edit to it produces the same line and moves nothing. Between two arms that is invisible:
    # arm A reads version 1, an edit lands, arm B reads version 2, and both report the same
    # fingerprint. Hash the bytes of each dirty path, so a second write to an already-dirty file is
    # as visible as the first.
    for line in status.splitlines():
        rel = line[3:].strip().strip('"')
        rel = rel.split(" -> ")[-1]                      # renames report `old -> new`
        parts.append(rel)
        parts.append(_content_digest(os.path.join(workdir, rel)))

    # The compared input is whatever the arms READ, and FROZEN_PATHS covers `.claude` alone. A
    # promptFile, a context file or a reviewed source tree outside it moves without touching any of
    # the above, so the caller names those paths and they are hashed by content too -- tracked or
    # not, since a gitignored brief is still an input.
    for extra in sorted(set(extra_paths or ())):
        parts.append(str(extra))
        parts.append(_content_digest(extra if os.path.isabs(str(extra))
                                     else os.path.join(workdir, str(extra))))

    return hashlib.sha1("\0".join(parts).encode("utf-8", "replace")).hexdigest()[:12]


def _content_digest(path):
    """sha1 of a file's bytes, or a marker naming why there are none.

    A missing or unreadable path returns a DISTINCT marker rather than a constant: `absent` and
    `unreadable` must not collide, or a file deleted mid-run fingerprints the same as one that was
    never there. Directories hash by their sorted entry names -- enough to catch an added or removed
    file without walking a tree of unknown size on every fingerprint.
    """
    try:
        if os.path.isdir(path):
            return "dir:" + hashlib.sha1(
                "\0".join(sorted(os.listdir(path))).encode("utf-8", "replace")).hexdigest()[:12]
        with open(path, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()[:12]
    except FileNotFoundError:
        return "absent"
    except OSError as err:
        return "unreadable:%s" % err.__class__.__name__


def inputs_of(argv):
    """Every file the child READS: the `-f` prompt, each `-C` context file, the `-S` schema.

    Read off argv rather than the job dict for the same reason as `workdir_of` -- these are what the
    child is actually handed. `-P` and `-R` are outputs and are deliberately absent: they differ per
    arm by design, and including them would make every comparison look confounded.
    """
    found = []
    for i, tok in enumerate(argv):
        if tok in ("-f", "-C", "-S") and i + 1 < len(argv):
            found.append(argv[i + 1])
    return sorted(set(found))


def workdir_of(argv):
    """The `-d` operand: what the child actually reads, not what the job dict claimed."""
    try:
        return argv[argv.index("-d") + 1]
    except (ValueError, IndexError):
        return None


def progress_of(argv):
    """The `-P` operand: the live progress stream this job's launcher tees to, if any."""
    try:
        return argv[argv.index("-P") + 1]
    except (ValueError, IndexError):
        return None


def record_path_of(argv):
    """The `-R` operand this child actually received, or None with no `-R` at all.

    Read off argv, matching workdir_of/progress_of/inputs_of -- a relaunch's record path is an
    ATTEMPT-numbered path chosen once at plan time (attempt_record_path), so reconstructing
    `<out_dir>/<label>.record.json` here instead would silently read attempt 1 forever.
    """
    try:
        return argv[argv.index("-R") + 1]
    except (ValueError, IndexError):
        return None


def _pid_alive(pid):
    """Best-effort PID-existence check: on Windows, MSYS `/proc/<pid>` through Git Bash (the
    launchers' pids are MSYS pids), else OpenProcess (query-only); `os.kill(pid, 0)` on POSIX.

    Existence-only: it cannot tell a live detached job from an unrelated process that later reused
    the same PID. That matches `_wait_detached`'s own tolerance -- a stale "alive" reading costs
    one more poll, never a hang, and a stale "dead" reading is exactly what its one-more-poll rule
    is for.
    """
    if pid is None:
        return False
    if platform.system() == "Windows":
        # Launchers record `$BASHPID`/`$!`: an MSYS pid, which Git Bash numbers apart from Windows
        # pids, so OpenProcess cannot see it. MSYS's own /proc is the authority when bash runs.
        bash = sidecar_launch.git_bash()
        if bash and os.path.isabs(bash):
            try:
                return subprocess.run([bash, "-c", "test -e /proc/%d" % int(pid)],
                                      capture_output=True, timeout=10).returncode == 0
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _detached_pid(record_path):
    """The most recently recorded pid in `<record>.pid`, or None.

    `sc_detach_launch` appends one line per claim/spawn step -- the claiming process first, then
    the actual detached job's own pid -- so the LAST `pid=<N>` line is the one that runs the job.
    """
    try:
        with open(str(record_path) + ".pid", encoding="utf-8", errors="replace") as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
    except OSError:
        return None
    for line in reversed(lines):
        if "pid=" in line:
            try:
                return int(line.rsplit("pid=", 1)[1].strip())
            except ValueError:
                continue
    return None


def _wait_detached(record_path, poll_interval=None):
    """Block until `<record>.exit` appears, or the detached pid dies with none written.

    Returns (exit_code, died_without_exit). No timeout: Design §3 gives this loop none, so a
    genuinely long job only ever ends via its own exit file. The single exception is the
    detached job's own process dying without writing one -- a crash, not a long run -- caught by
    giving the pid's death exactly one more poll before believing it: a transient race between
    "the pid just exited" and "the file just landed" costs one extra sleep, never a false "died".
    """
    poll_interval = POLL_INTERVAL_SEC if poll_interval is None else poll_interval
    exitf = str(record_path) + ".exit"
    grace_used = False
    while True:
        if os.path.isfile(exitf):
            try:
                with open(exitf, encoding="utf-8", errors="replace") as fh:
                    code = fh.read().strip()
                # An empty file is a write caught between open and flush: poll again.
                if code:
                    return int(code), False
            except OSError:
                pass
            except ValueError:
                return None, False
        pid = _detached_pid(record_path)
        if pid is not None and not _pid_alive(pid):
            if grace_used:
                return None, True
            grace_used = True
        else:
            grace_used = False
        time.sleep(poll_interval)


def bound_finished_stream(stream_path, out_path):
    """Replace a finished byte-identical progress stream with a pointer to stdout.

    A resumed launcher appends every segment to `-P` but replaces captured stdout on each resume.
    When those files differ, the progress stream is the only full record and must stay intact.
    """
    if not stream_path or not os.path.isfile(stream_path):
        return
    try:
        with open(stream_path, "rb") as stream, open(out_path, "rb") as output:
            if stream.read() != output.read():
                return
    except OSError:
        return
    pointer = {"seeAlso": os.path.basename(str(out_path)),
               "note": "progress stream bounded after the run completed; its bytes match "
                       "the sibling .out.json"}
    try:
        with open(stream_path, "w", encoding="utf-8") as fh:
            json.dump(pointer, fh)
    except OSError:
        pass  # bounding is a disk-space cleanup, never worth failing a completed run over


def run_jobs(planned, max_parallel, out_dir):
    # A bounded pool: `max_parallel` workers draining a queue. The previous shape started one OS
    # thread per job and used the semaphore only to cap concurrent SUBPROCESSES, so a 200-job file
    # spawned 200 threads to run 4 at a time -- and N is caller-controlled from the jobs JSON.
    results = {}
    if not planned:
        return results
    parent_session = os.environ.get("CLAUDE_CODE_SESSION_ID") or None
    fanout_id = str(uuid.uuid4())
    inventory_path = Path(out_dir) / ("fanout-" + fanout_id + ".inventory.json")
    allocated = []
    for label, argv, out_path in planned:
        launch_id = str(uuid.uuid4())
        # Read off argv, never reconstructed from (out_dir, label): an attempt-numbered relaunch's
        # `-R` is not `<label>.record.json`, and reconstructing it here would silently poll attempt
        # 1's files forever. A job with no `-R` at all (a caller invoking run_jobs directly, never
        # through plan_job) falls back to the legacy guess so it keeps whatever behavior it had.
        record_path = record_path_of(argv) or str(Path(out_dir) / f"{label}.record.json")
        allocated.append((label, argv, out_path, launch_id, record_path))
    inventory = {"schemaVersion": 1, "fanoutId": fanout_id,
                 "parentSessionId": parent_session,
                 "jobs": [{"label": label, "launchId": launch_id, "recordPath": record_path}
                          for label, _, _, launch_id, record_path in allocated]}
    try:
        with open(inventory_path, "x", encoding="utf-8") as fh:
            json.dump(inventory, fh, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
    except Exception as err:
        raise RuntimeError(f"cannot persist sidecar inventory {inventory_path}: {err}") from err
    pending = allocated
    lock = threading.Lock()
    threads = []

    def one(label, argv, out_path, launch_id, record_path):
        child_env = dict(os.environ, SIDECAR_LAUNCH_ID=launch_id)
        identity = {"launchId": launch_id, "parentSessionId": parent_session,
                    "inventoryPath": str(inventory_path)}
        if True:
            print(f"[dispatch] {label}: {' '.join(argv[:6])} ...", flush=True)
            # Built from the same (out_dir, label) pair as `out_path`, not by substring surgery on
            # it: `.replace` rewrites EVERY `.out.json` in the path, so an out-dir carrying that
            # name in a parent segment silently redirected stderr outside the run.
            err_path = str(Path(out_dir) / f"{label}.err")
            wd, reads = workdir_of(argv), inputs_of(argv)
            before = freeze_fingerprint(wd, reads)
            detached = "-X" in argv
            try:
                with open(out_path, "w", encoding="utf-8") as so, \
                        open(err_path, "w", encoding="utf-8") as se:
                    code = subprocess.call(argv, stdout=so, stderr=se, cwd=str(REPO), env=child_env)
            except Exception as err:
                results[label] = {**identity, "exit": None, "error": str(err),
                                  "frozenInput": {"before": before, "after": before, "held": True}}
                return

            if detached and code == 0:
                # `-X` handed off within seconds and already exited -- `code` here is the HANDOFF's
                # own exit, never the job's. The job runs on, detached, under the pid its record's
                # `.pid` file names; wait on ITS exit file, never on a live child of this process,
                # because there no longer is one (Design §3: this is what makes a kill of the
                # fan-out's own process powerless to touch the job).
                job_code, died = _wait_detached(record_path)
                detached_out, detached_err = record_path + ".out", record_path + ".err"
                if os.path.isfile(detached_out):
                    try:
                        shutil.copyfile(detached_out, out_path)
                    except OSError:
                        pass
                if os.path.isfile(detached_err):
                    try:
                        shutil.copyfile(detached_err, err_path)
                    except OSError:
                        pass
                if died:
                    after = freeze_fingerprint(wd, reads)
                    results[label] = {
                        **identity, "exit": None,
                        "error": f"detached pid died without writing {record_path}.exit",
                        "stdout": out_path, "stderr": err_path, "record": None, "resumeWith": None,
                        "frozenInput": {"before": before, "after": after, "held": before == after},
                    }
                    return
                code = job_code
            # Collapse `-P` only when it duplicates stdout. Resumed runs append to `-P` while
            # stdout holds the last segment, so a differing progress file is the full archive.
            # A usage-limit stream is the salvage source the summary names; keep it whole.
            if code != USAGE_LIMIT_EXIT:
                bound_finished_stream(progress_of(argv), out_path)
            after = freeze_fingerprint(wd, reads)
            rec = record_path
            try:
                with open(rec, encoding="utf-8") as fh:
                    record = json.load(fh)
                if not isinstance(record, dict) or record.get("launchId") != launch_id or record.get("parentSessionId") != parent_session:
                    record = None
            except (OSError, ValueError):
                record = None
            results[label] = {
                **identity,
                "exit": code,
                "meaning": LAUNCHER_EXITS.get(code, "unrecognised launcher exit"),
                "stdout": out_path,
                "stderr": err_path,
                "stream": progress_of(argv),
                "record": rec if record is not None else None,
                # Reported, never acted on. A retry here would re-bill a call the caller has not
                # seen the failure of.
                "resumeWith": (record.get("sessionId") or record.get("session_id")) if code != 0 and record is not None else None,
                "frozenInput": {"before": before, "after": after, "held": before == after},
            }

    def worker():
        while True:
            with lock:
                if not pending:
                    return
                job = pending.pop(0)
            one(*job)

    for _ in range(min(max_parallel, len(planned))):
        t = threading.Thread(target=worker, daemon=False)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    return results


def summarize(planned, results, compare):
    """-> (results text, exit code). A usage-limit stop is its own outcome, not a failure."""
    lines, failed, limited = ["", "--- results ---"], [], []
    for label, _, _ in planned:
        r = results.get(label) or {"exit": None, "error": "never ran"}
        if r.get("exit") == 0:
            lines.append(f"ok    {label}  -> {r['stdout']}")
            continue
        if r.get("exit") == USAGE_LIMIT_EXIT:
            limited.append(label)
            lines.append(f"usage-limit  {label}  exit={USAGE_LIMIT_EXIT} ({r.get('meaning')})")
            lines.append(f"        salvage from: {r.get('stream') or '(no -P stream)'}")
            if r.get("resumeWith"):
                lines.append(f"        resumable after the reset: re-run that job's launcher with -r {r['resumeWith']}")
            continue
        failed.append(label)
        lines.append(f"FAIL  {label}  exit={r.get('exit')} ({r.get('meaning') or r.get('error')})")
        lines.append(f"        stderr: {r.get('stderr')}")
        if r.get("resumeWith"):
            # The launcher persisted a session. Resuming is the caller's call, deliberately: this
            # tool never re-bills a run on its own.
            lines.append(f"        resumable: re-run that job's launcher with -r {r['resumeWith']}")
    # An arm whose input moved under it read different bytes from its peers, so a defect it
    # reports absent is indistinguishable from one the edit removed. Exit non-zero under --compare:
    # the numbers are the deliverable there, and wrong numbers beat no numbers only in appearance.
    moved = [l for l, _, _ in planned
             if (results.get(l) or {}).get("frozenInput", {}).get("held") is False]
    if moved and compare:
        lines.append("\nCOMPARISON INVALID: inputs changed under %d arm(s): %s" % (len(moved), ", ".join(moved)))
        lines.append("  Preserve the artifacts and identify the changed inputs before deciding whether to repeat the comparison.")
    elif moved:
        lines.append("\nInputs changed during %d job(s): %s" % (len(moved), ", ".join(moved)))
        lines.append("  Expected for authoring; verify relevant source hashes before accepting read-only claims. No automatic rerun.")

    done = len(planned) - len(failed) - len(limited)
    lines.append(f"\n{done}/{len(planned)} exited 0"
                 + (f"; usage-limit: {', '.join(limited)}" if limited else "")
                 + (f"; failed: {', '.join(failed)}" if failed else ""))
    return "\n".join(lines), 1 if (moved and compare) or failed or limited else 0


def _status_labels(out_dir):
    """Every label with an attempt record sibling or a `-P` stream under `out_dir`."""
    labels = set()
    for path in out_dir.iterdir():
        name = path.name
        if name.endswith(".stream"):
            labels.add(name[:-len(".stream")])
            continue
        head, sep, _ = name.partition(".record.json.")
        if not sep:
            continue
        base, dot, number = head.rpartition(".attempt")
        labels.add(base if dot and number.isdigit() else head)
    return sorted(labels)


def _attempt_state(record):
    """(state, detail) for one attempt from `<record>.exit` and `<record>.pid`, the same two files
    `_wait_detached` polls: finished, running, died (pid gone, no exit file), pending (no pid yet),
    or no-attempt when `record` is None."""
    if record is None:
        return "no-attempt", "no attempt record"
    where = _posix(record)
    try:
        with open(str(record) + ".exit", encoding="utf-8") as fh:
            return "finished", f"exit={fh.read().strip()}  {where}"
    except OSError:
        pass
    pid = _detached_pid(record)
    if pid is None:
        return "pending", f"no pid recorded yet  {where}"
    if _pid_alive(pid):
        return "running", f"pid {pid}  {where}"
    return "died", f"pid {pid} gone, no exit file  {where}"


def print_status(out_dir):
    """One `<label> <state> <detail>` line per job under `out_dir`. The LATEST attempt's `.exit`
    and `.pid` say whether it finished or died, never attempt 1 once a relaunch exists; its `-P`
    stream's latest events say what a live or unattempted job is doing; its record's stopReason
    says how a finished one stopped. Returns 2 when `out_dir` holds no job, else 0."""
    out_dir = Path(out_dir)
    labels = _status_labels(out_dir) if out_dir.is_dir() else []
    if not labels:
        print(f"no job records or streams in {out_dir}", file=sys.stderr)
        return 2
    now = __import__("time").time()
    stall_sec = int(os.environ.get("SIDECAR_STALL_SEC") or 900)
    limit = int(os.environ.get("SIDECAR_USAGE_LIMIT_RETRIES") or 10)
    for label in labels:
        record = latest_attempt_record_path(out_dir, label)
        state, detail = _attempt_state(record)
        stream = out_dir / f"{label}.stream"
        if state in ("no-attempt", "pending", "running") and stream.is_file():
            live, live_detail = stream_check.stream_status(str(stream), now, stall_sec, limit)
            state, detail = live, f"{live_detail}  {detail}"
        if record is not None:
            try:
                with open(record, encoding="utf-8") as fh:
                    stop = (json.load(fh) or {}).get("stopReason")
                state = {"provider-usage-limit": "usage-limit", "stall": "stalled"}.get(stop, "finished")
                detail = f"record stopReason {stop}  {detail}"
            except (OSError, ValueError, AttributeError):
                pass
        print(f"{label:<28} {state:<11} {detail}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fan a job list across sidecar launchers.")
    ap.add_argument("jobs", nargs="?", help="path to the jobs JSON file")
    ap.add_argument("--max-parallel", type=int, default=DEFAULT_MAX_PARALLEL,
                    help=f"concurrent child launchers (default {DEFAULT_MAX_PARALLEL})")
    ap.add_argument("--out-dir", default=None,
                    help="where per-job stdout/record/stream land (default: beside the jobs file)")
    ap.add_argument("--dry-run", action="store_true",
                    help="plan and print every argv, spend nothing")
    ap.add_argument("--authorize", action="store_true",
                    help="pass -A to every job, overriding the band gates (states the spend)")
    ap.add_argument("--compare", action="store_true",
                    help="these arms will be COMPARED (ladder evidence, pin A/B): refuse to "
                         "dispatch unless every job reads a frozen detached worktree")
    ap.add_argument("--status", metavar="OUT_DIR", default=None,
                    help="print one line per job in OUT_DIR from its latest attempt; "
                         "dispatches nothing")
    args = ap.parse_args(argv)

    if args.status:
        return print_status(args.status)
    if not args.jobs:
        print("a jobs file is required unless --status is given", file=sys.stderr)
        return 2
    if args.max_parallel < 1:
        print("--max-parallel must be at least 1", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir or (Path(args.jobs).resolve().parent / "fanout"))
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        jobs = load_jobs(args.jobs)
    except JobError as err:
        print(str(err), file=sys.stderr)
        return 2

    try:
        data = model_registry.load()
        planned = [plan_job(j, data, out_dir, args.authorize) for j in jobs]
    except JobError as err:
        print(str(err), file=sys.stderr)
        return 2

    seen = set()
    for label, _, _ in planned:
        if label in seen:
            print(f"duplicate label {label!r}: outputs and ledger rows would overwrite each other",
                  file=sys.stderr)
            return 2
        seen.add(label)

    if args.compare:
        # Refuse BEFORE spending: an unfrozen comparison produces confident numbers that measure
        # nothing, which is worse than no run at all.
        bad = []
        for label, a, _out in planned:
            why = frozen_input_error(workdir_of(a))
            if why:
                bad.append(f"  {label}: {why}")
        if bad:
            print("--compare: these arms would not read a frozen input, so the comparison would "
                  "measure nothing:\n" + "\n".join(bad)
                  + "\n\nFreeze it:  git worktree add --detach .claude/worktrees/<slug> HEAD"
                  + "\n(orchestration section 0 -- arms that will be compared read a frozen input)",
                  file=sys.stderr)
            return 2

        # Each arm being frozen is not the same as the arms sharing ONE input. Two detached
        # worktrees at different commits each pass the check above, and the run then measures the
        # code difference between them as if it were a difference between models -- the exact
        # failure `--compare` exists to prevent, arrived at from the other side.
        prints = {}
        for label, a, _out in planned:
            wd, ins = workdir_of(a), inputs_of(a)
            prints.setdefault(freeze_fingerprint(wd, ins), []).append(
                "%s\n      workdir %s\n      reads   %s" % (label, wd, ", ".join(ins) or "(none)"))
        if len(prints) > 1:
            listed = "\n".join("  %s\n    %s" % (fp, "\n    ".join(labels))
                               for fp, labels in sorted(prints.items()))
            print("--compare: the arms do NOT share one input, so any difference between them is "
                  "confounded with the difference between their inputs:\n" + listed
                  + "\n\nEvery arm needs the SAME detached worktree AND the same prompt, context "
                    "and schema files. The groups above differ in at least one of those.",
                  file=sys.stderr)
            return 2

    if args.dry_run:
        for label, a, out_path in planned:
            print(f"{label}\n  {' '.join(a)}\n  -> {out_path}")
        print(f"\n{len(planned)} job(s), {args.max_parallel} at a time. Nothing dispatched.")
        return 0

    results = run_jobs(planned, args.max_parallel, out_dir)
    text, code = summarize(planned, results, args.compare)
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
