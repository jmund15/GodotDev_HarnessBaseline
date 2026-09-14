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
import subprocess
import sys
import threading
import uuid
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent          # .claude/
REPO = ROOT.parent
sys.path.insert(0, str(ROOT / "tools"))
import model_registry  # noqa: E402
import sidecar_launch  # noqa: E402  -- owns the shell choice; see plan_job's argv[0]

# A PROCESS cap, chosen here rather than read from the registry's `limits.concurrency`: that field
# carries 2500 (flash), 500 (pro) and null (luna/terra/sol) -- an API/token ceiling, not a count of
# child CLIs. The default rests on one luna 7-wide fan-out that died 403 at a single instant, cause
# unverified (`gotcha_luna_sidecar_concurrency_cap`). That is a hypothesis, not a measurement, so
# the number is a flag rather than a rule.
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
}


class JobError(Exception):
    """A job that cannot be dispatched. Raised before anything is spent."""


# Which paths a comparison's freeze must actually cover. Engine-generated churn outside these
# (engine import sidecars, asset caches) is expected in any fresh worktree and is not a thaw.
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
    # Dirt in the COMPARED INPUT only. A fresh worktree is never globally clean: an engine rewrites
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
    path collapsed to `C:Users<user>Project...` and every job died exit 127 before the launcher ran.
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
    # resolves a bare `bash` to the System32 WSL shim, a different filesystem root whose $HOME
    # holds none of this user's credentials. Measured here 2026-09-08 -- the codex jobs exited 3
    # ("run claude-code-proxy codex auth login") on a machine that is logged in, and the opencode
    # jobs died on a litellm proxy started under /mnt/c. Both symptoms name the launcher, not the
    # shell. sidecar_launch owns this choice for every Python caller.
    argv = [sidecar_launch.git_bash(), _posix(launcher), "-m", alias, "-f", _posix(prompt),
            "-l", label,                                   # attribution: the metrics reader keys on
            "-R", _posix(out_dir / f"{label}.record.json")]  # this pair; unlabelled rows are skipped
    for flag, key in (("-e", "effort"), ("-D", "disclosure"), ("-G", "shape"),
                      ("-S", "schemaFile"), ("-d", "workdir")):
        if job.get(key):
            argv += [flag, _posix(job[key])]
    for cf in job.get("contextFiles") or []:
        if not os.path.exists(cf):
            raise JobError(f"job {label}: contextFile not found: {cf}")
        argv += ["-C", _posix(cf)]
    # The ladder pins this to the EFFORT rung, not the shape: a `max` run compacts and returns
    # prose without `-P` (gpt-5.6-luna row; the work-shape table lists a luna arm without -S/-P
    # under `never`). Schema and review shape stay as additional triggers.
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
        record_path = str(Path(out_dir) / f"{label}.record.json")
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
            try:
                with open(out_path, "w", encoding="utf-8") as so, \
                        open(err_path, "w", encoding="utf-8") as se:
                    code = subprocess.call(argv, stdout=so, stderr=se, cwd=str(REPO), env=child_env)
            except Exception as err:
                results[label] = {**identity, "exit": None, "error": str(err),
                                  "frozenInput": {"before": before, "after": before, "held": True}}
                return
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


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fan a job list across sidecar launchers.")
    ap.add_argument("jobs", help="path to the jobs JSON file")
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
    args = ap.parse_args(argv)

    if args.max_parallel < 1:
        print("--max-parallel must be at least 1", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir or (Path(args.jobs).resolve().parent / "fanout"))
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        jobs = load_jobs(args.jobs)
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

    failed = []
    print("\n--- results ---")
    for label, _, _ in planned:
        r = results.get(label) or {"exit": None, "error": "never ran"}
        if r.get("exit") == 0:
            print(f"ok    {label}  -> {r['stdout']}")
            continue
        failed.append(label)
        print(f"FAIL  {label}  exit={r.get('exit')} ({r.get('meaning') or r.get('error')})")
        print(f"        stderr: {r.get('stderr')}")
        if r.get("resumeWith"):
            # The launcher persisted a session. Resuming is the caller's call, deliberately: this
            # tool never re-bills a run on its own.
            print(f"        resumable: re-run that job's launcher with -r {r['resumeWith']}")
    # An arm whose input moved under it read different bytes from its peers, so a defect it
    # reports absent is indistinguishable from one the edit removed. Exit non-zero under --compare:
    # the numbers are the deliverable there, and wrong numbers beat no numbers only in appearance.
    moved = [l for l, _, _ in planned
             if (results.get(l) or {}).get("frozenInput", {}).get("held") is False]
    if moved:
        print("\nFROZEN INPUT MOVED under %d arm(s): %s" % (len(moved), ", ".join(moved)))
        print("  Their findings are not comparable with the arms that finished before the write.")
        print("  Restore the worktree and re-run those labels; `git -C <workdir> status` names it.")

    print(f"\n{len(planned) - len(failed)}/{len(planned)} exited 0"
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    if moved and args.compare:
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
