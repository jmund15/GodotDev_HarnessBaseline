#!/usr/bin/env python3
"""The main check: proves each range landed on `main` and records the verdict as rows on the `main-check-state` branch.

    python3 .claude/scripts/main_check.py close    [--run-id ID] [--dispatched] [--cap-minutes M]
    python3 .claude/scripts/main_check.py gate     [--run-id ID] [--cap-minutes M]
    python3 .claude/scripts/main_check.py finalize [--run-id ID]
    python3 .claude/scripts/main_check.py --rows [--session ID] [--repo PATH]
    python3 .claude/scripts/main_check.py --status [--repo PATH]

`.github/workflows/main_check.yml` runs the three stages as one job on the self-hosted runner, in the detached worktree
`.claude/worktrees/main-check`. Each stage keeps its records under `.claude/scratch/main_check/<run-id>/`, so
`finalize` reads only its own run's records. A stage ends 0 when it recorded what it found (a red is data); it ends
nonzero when it could not do its job, and `finalize` ends nonzero when the run reached no verdict.

close     Fetches `origin/main` and the state branch, resets the worktree to the tip, updates its submodules to the
          tip's gitlinks, writes the run's start to the state, and runs `harness_tests.py --close --now --range-start
          <checkedTo> --also <this platform's open proof rows>`. The close runs every proof it selects and every
          retry, so a red never stops another proof and the open rows run once more in the pool. The close gets
          HARNESS_DEADLINE at 90% of the cap so it ends itself with a record.
gate      Runs the project's `gate_cadence.main` command (`tools/adaptation.py`; absent: no gate, never owed) when the
          range owes it (`gate_owed`) or the run was dispatched. Exit 0 passes; exit 1 fails and names each failed
          test as a `FAILED_TEST=<id>` stdout line, and a fail naming none is no verdict; an exit in `retry_exits`
          is rerun once before it is recorded; any other exit is no verdict. A fail is never rerun.
finalize  Moves `checkedTo` to the tip on every close that finished, green or red, once the reds and `cannotRun`
          entries have their open rows, and when an owed gate passed or failed naming its failed tests. Only a
          close that did not finish (killed, superseded, a checkout or submodule fault) or an owed gate with no
          verdict leaves it, and `run.outcome` says so. It also reads the baseline repo's Linux lanes for the
          commit `baseline.lock.json`'s `synced_commit` names, and records each red proof as a `platform: linux`
          row. It then writes rows and pushes `main_check.json` to `main-check-state` with `--force-with-lease`,
          rereading the remote state and reapplying the result once when the lease is lost.
--rows    Prints the open rows of the local `origin/main-check-state` ref (fetch it first), no network, grouped by
          `cause` (a row without one is unclassified).
--status  Prints the health line of that ref (`status()`), or nothing when it is clean and current.

State file `main_check.json` (version 1): `checkedTo`; `run` (id, start, liveUntil, end, stage, outcome, detail,
wall); `sources.baselineLinux` (readAt, status ok, pending, unreadable or none, reason, pendingSha); `rows` (name,
kind proof or gate, platform windows, linux or macos, commit, session, firstLine, firstSeen, clearedAt, clearedBy,
cause). A linux row's `commit` is the baseline commit its run proved, and it clears only when a later baseline run
lists that proof as passed, never by its absence from the red set. A `cause` proves only what it names: a
`timeout` row is a proof that timed out again alone (or whose own subprocess did), so no bisect ran and its
`commit` is null; an `uncovered` row names a run whose close could not name a proof. A row with no cause is
unclassified until the fixer or S7r classifies it. Times are UTC ISO-8601 seconds. `outcome` is green, red or
no-verdict. A row is open while `clearedAt` is null and stays 30 days after it clears.
"""
import argparse
import calendar
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "hooks"))
from _bounded_run import ProcessTree  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "tools"))
import adaptation  # noqa: E402

REMOTE = "origin"
STATE_BRANCH = "main-check-state"
STATE_FILE = "main_check.json"
STATE_REF = "refs/remotes/%s/%s" % (REMOTE, STATE_BRANCH)
ORIGIN_MAIN = "refs/remotes/%s/main" % REMOTE
WORKFLOW = ".github/workflows/main_check.yml"
DEFAULT_CAP_MINUTES = 360
DEADLINE_SHARE, KILL_SHARE = 0.90, 0.97
GATE_ATTEMPTS = 2
ROW_KEEP_DAYS = 30
RUN_DIR_KEEP_DAYS = 14
RETRY_SLEEP_S = 3
FIRST_LINE_MAX = 300
BASELINE_WORKFLOW = "baseline.yml"
GH_READ_TIMEOUT_S = 60

HARNESS_PATH = re.compile(r"^\.claude/|^\.github/|\.md$|^\.gitignore$|^\.searchignore$", re.IGNORECASE)
FAILURE_MARK = re.compile(r"^(FAIL|not ok)\b|Error|Exception", re.IGNORECASE)
CLOSE_ECHO = re.compile(r"^(FAIL|FIRST RED|FLAKY|NEEDS|harness_tests: |    \| )")  # `    | `: a red's output excerpt
GATE_STREAM = ("STAGE", "FAILED_TEST", "VERDICT", "BUSY", "WAIT")
GATE_PROJECT_STREAM = ("BUILD", "IMPORT_GATE", "SUITE", "BATCH", "DIAGNOSE", "DETAIL", "GATE_ERROR", "TREE_CHANGED", "MANIFEST")
GATE_ECHO = re.compile("^(%s)" % "|".join(GATE_STREAM + GATE_PROJECT_STREAM))
FAILED_TEST_LINE = re.compile(r"^FAILED_TEST=(.+)$", re.MULTILINE)
CAUSE_LINE = re.compile(r"INCOMPLETE|incomplete|not passed or failed|superseded")
PROOF_FAIL_LINE = re.compile(r"^FAIL \d+(?:\.\d+)?s (\S.*)$")
PROOF_PASS_LINE = re.compile(r"^OK \d+(?:\.\d+)?s (\S.*)$")
GHA_LINE_PREFIX = re.compile("^[^\\t]*\\t[^\\t]*\\t\\ufeff?\\d{4}-\\d\\d-\\d\\dT[\\d:.]+Z ?")


class StateError(RuntimeError):
    """The state branch is missing, unreadable or malformed."""


class GitError(RuntimeError):
    pass


def say(message):
    print("main_check: " + message, flush=True)


def iso(seconds=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if seconds is None else seconds))


def parse_iso(text):
    return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))


def platform_name():
    return {"win32": "windows", "linux": "linux", "darwin": "macos"}.get(sys.platform, sys.platform)


def git_try(repo, *args, input=None):
    """The CompletedProcess of a git call, text decoded. `input` goes in as bytes: a text-mode pipe would turn its
    newlines into CRLF on Windows, which `git mktree` reads as part of the file name."""
    result = subprocess.run(["git", "-C", repo, *args], input=None if input is None else input.encode("utf-8"),
                            capture_output=True, stdin=None if input is not None else subprocess.DEVNULL)
    return subprocess.CompletedProcess(result.args, result.returncode, result.stdout.decode("utf-8", "replace"),
                                       result.stderr.decode("utf-8", "replace"))


def git(repo, *args, input=None):
    """The stdout of a git call that must succeed; GitError names the call and its stderr otherwise."""
    result = git_try(repo, *args, input=input)
    if result.returncode != 0:
        raise GitError("git %s failed (%d): %s" % (args[0], result.returncode, (result.stderr or result.stdout).strip()))
    return result.stdout.strip()


def remote_git(repo, *args, tries=3):
    """A call that reaches the remote: a dropped connection (exit 128) is retried; a rejection (exit 1) is returned."""
    for attempt in range(1, tries + 1):
        result = git_try(repo, *args)
        text = result.stderr + result.stdout
        gone = "couldn't find remote ref" in text or "did not contain" in text
        if result.returncode in (0, 1) or attempt == tries or gone:
            return result
        say("retry git %s (try %d of %d)" % (args[0], attempt + 1, tries))
        time.sleep(RETRY_SLEEP_S)


def write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")


def read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def repo_root(arg):
    return os.path.abspath(arg) if arg else git(os.getcwd(), "rev-parse", "--show-toplevel")


def runs_root(repo):
    return os.path.join(repo, ".claude", "scratch", "main_check")


def run_dir(repo, run_id):
    return os.path.join(runs_root(repo), run_id)


def record(repo, run_id, name):
    return read_json(os.path.join(run_dir(repo, run_id), name + ".json"))


def put_record(repo, run_id, name, doc):
    write_json(os.path.join(run_dir(repo, run_id), name + ".json"), dict(doc, runId=run_id))


def sweep_run_dirs(repo, keep):
    root = runs_root(repo)
    if not os.path.isdir(root):
        return
    cutoff = time.time() - RUN_DIR_KEEP_DAYS * 86400
    for entry in os.scandir(root):
        if entry.is_dir() and entry.name != keep and entry.stat().st_mtime < cutoff:
            shutil.rmtree(entry.path, ignore_errors=True)


def settings_env(repo):
    """The environment the runner service lacks: the `env` blocks of the user, project and local Claude settings
    (later overriding earlier) and the HOME that Git Bash sets, as the old land runner gave its close."""
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or repo
    env = dict(os.environ)
    for path in (os.path.join(home, ".claude", "settings.json"), os.path.join(repo, ".claude", "settings.json"),
                 os.path.join(repo, ".claude", "settings.local.json")):
        doc = read_json(path)
        block = doc.get("env") if isinstance(doc, dict) else None
        if isinstance(block, dict):
            env.update({key: str(value) for key, value in block.items()})
    env.setdefault("HOME", home)
    return env


def run_logged(cmd, *, cwd, env, log_path, echo, cap_seconds):
    """(returncode or None when the cap ended it, output). Every line of the log that `echo` matches streams to
    stdout as it appears; the process tree ends with the cap."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    shown = 0

    def stream(final=False):
        nonlocal shown
        with open(log_path, "rb") as fh:
            fh.seek(shown)
            chunk = fh.read()
        end = len(chunk) if final else chunk.rfind(b"\n") + 1
        for line in chunk[:end].decode("utf-8", "replace").splitlines():
            if echo.match(line):
                print("  " + line, flush=True)
        shown += end

    with open(log_path, "wb") as sink:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=sink, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=(os.name != "nt"))
        tree = ProcessTree(proc)
        began = time.monotonic()
        try:
            while True:
                try:
                    code = proc.wait(timeout=2)
                    break
                except subprocess.TimeoutExpired:
                    stream()
                    if time.monotonic() - began >= cap_seconds:
                        code = None
                        break
        finally:
            tree.end()
    stream(final=True)
    with open(log_path, encoding="utf-8", errors="replace") as fh:
        return code, fh.read()


def stamp_path(repo):
    return os.environ.get("HARNESS_TEST_STAMP") or os.path.join(repo, ".claude", "logs", "harness_tests_stamp.json")


def parse_job_minutes(text):
    """The job-level `timeout-minutes` of the workflow text (four-space indent), else GitHub's job default."""
    found = re.search(r"^    timeout-minutes:\s*(\d+)\s*$", text, re.MULTILINE)
    return int(found.group(1)) if found else DEFAULT_CAP_MINUTES


def job_minutes(repo, tip):
    return parse_job_minutes(git_try(repo, "show", "%s:%s" % (tip, WORKFLOW)).stdout)


# ---- the gate-owed predicate (ported from the old land runner's `LAND stage=gate` branch) --------------------------

def gate_declaration(repo):
    """(`gate_cadence.main` of the repo's adaptation files, why it is unusable): (None, None) when the project declares
    no gate, (None, reason) when it declares one the validator rejects."""
    main = adaptation.get(os.path.join(repo, ".claude"), "gate_cadence").get("main")
    if main is None:
        return None, None
    errors = adaptation.gate_cadence_errors({"main": main})
    return (None, "gate_cadence." + "; ".join(errors)) if errors else (main, None)


def gate_outputs(repo):
    main, _problem = gate_declaration(repo)
    return tuple(p.lower() for p in (main or {}).get("outputs", []))


def paths_owe_gate(changed, attribute_lines, outputs=()):
    """True when a changed path outside the harness set (`.claude/`, `.github/`, Markdown, the root `.gitignore` and
    `.searchignore`, which no build or test reads) and outside the gate's declared outputs exists. A `.gitattributes` edit
    whose every changed line names a `.claude/` pattern changes no byte a build checks out."""
    attrs_harness_only = all(line.startswith(".claude/") for line in attribute_lines)
    for path in changed:
        if HARNESS_PATH.search(path) or path.lower() in outputs:
            continue
        if path == ".gitattributes" and attrs_harness_only:
            continue
        return True
    return False


def diff_owes_gate(repo, older, newer):
    changed = [p for p in git(repo, "diff", "--name-only", "--no-renames", "-z", older, newer).split("\0") if p]
    attrs = []
    if ".gitattributes" in changed:
        for line in git(repo, "diff", "-U0", older, newer, "--", ".gitattributes").splitlines():
            if line[:1] in "+-" and not line.startswith(("+++", "---")):
                body = line[1:].strip()
                if body and not body.startswith("#"):
                    attrs.append(body)
    return paths_owe_gate(changed, attrs, gate_outputs(repo))


def gate_owed(repo, range_start, tip, dispatched=False):
    """(owed, reason): a dispatched run owes the gate on any tree; else the range changes a path the gate covers."""
    if dispatched:
        return True, "dispatched run"
    if diff_owes_gate(repo, range_start, tip):
        return True, "range changes a path outside the harness set"
    return False, "range changes only harness paths and the gate's declared outputs"


def non_harness_commit(repo, range_start, tip):
    """The range's one commit that owes the gate, else None."""
    owing = []
    for commit in git(repo, "rev-list", "--first-parent", "%s..%s" % (range_start, tip)).split():
        parent = git_try(repo, "rev-parse", commit + "^1").stdout.strip()
        if parent and diff_owes_gate(repo, parent, commit):
            owing.append(commit)
    return owing[0] if len(owing) == 1 else None


def session_of(repo, commit):
    if not commit:
        return None
    out = git_try(repo, "show", "-s", "--format=%(trailers:key=Claude-Session,valueonly)", commit).stdout
    return next((line.strip() for line in out.splitlines() if line.strip()), None)


# ---- the state branch --------------------------------------------------------------------------------------------

def fetch_branch(repo, branch):
    """The remote branch's sha, or None when the remote has no such branch."""
    result = remote_git(repo, "fetch", "-q", REMOTE, "+refs/heads/%s:refs/remotes/%s/%s" % (branch, REMOTE, branch))
    if result.returncode == 0:
        return git(repo, "rev-parse", "refs/remotes/%s/%s" % (REMOTE, branch))
    if "couldn't find remote ref" in result.stderr:
        return None
    raise GitError("git fetch %s failed: %s" % (branch, result.stderr.strip()))


def parse_state(text):
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise StateError("unreadable: %s" % exc)
    if not (isinstance(doc, dict) and isinstance(doc.get("checkedTo"), str) and isinstance(doc.get("rows"), list)):
        raise StateError("unreadable: no checkedTo or rows")
    return doc


def load_state(repo):
    """(sha, doc) of the remote state branch after a fetch; StateError when it is missing or malformed."""
    sha = fetch_branch(repo, STATE_BRANCH)
    if sha is None:
        raise StateError("no state branch %s on %s: S1m seeds it" % (STATE_BRANCH, REMOTE))
    return sha, parse_state(git(repo, "show", "%s:%s" % (sha, STATE_FILE)))


def update_state(repo, mutate):
    """Applies `mutate` (a pure function of the state) to the remote state and pushes it with a lease on the sha it
    read. A lost lease rereads the remote state and reapplies once more; a second loss raises."""
    for attempt in (1, 2):
        old, doc = load_state(repo)
        new = mutate(copy.deepcopy(doc))
        blob = git(repo, "hash-object", "-w", "--stdin", input=json.dumps(new, indent=1) + "\n")
        tree = git(repo, "mktree", input="100644 blob %s\t%s\n" % (blob, STATE_FILE))
        commit = git(repo, "-c", "user.name=main-check", "-c", "user.email=main-check@localhost", "commit-tree", tree,
                     "-p", old, "-m", "main check state")
        result = remote_git(repo, "push", "-q", "--force-with-lease=refs/heads/%s:%s" % (STATE_BRANCH, old), REMOTE,
                            "%s:refs/heads/%s" % (commit, STATE_BRANCH))
        if result.returncode == 0:
            return new
        say("state push rejected (try %d of 2): %s" % (attempt, " ".join(result.stderr.split())[-200:]))
    raise StateError("the state push lost its lease twice")


def open_row(row):
    return row.get("clearedAt") is None


def has_open_row(rows, name, platform, first_line=None):
    """True when an open row of this platform names `name`; with `first_line`, only one whose line matches."""
    return any(open_row(r) and r.get("name") == name and r.get("platform") == platform
               and (first_line is None or r.get("firstLine") == first_line) for r in rows)


# ---- stage close -------------------------------------------------------------------------------------------------

def tracked_proofs(repo, tip):
    found = {}
    for path in git(repo, "ls-tree", "-r", "--name-only", tip, "--", ".claude/tests").splitlines():
        found.setdefault(path.rsplit("/", 1)[-1], path)
    return found


def run_close(repo, run_id, dispatched=False, cap_minutes=DEFAULT_CAP_MINUTES):
    began = time.time()
    sweep_run_dirs(repo, run_id)
    fetch = remote_git(repo, "fetch", "-q", REMOTE, "+refs/heads/main:refs/remotes/%s/main" % REMOTE)
    if fetch.returncode != 0:
        say("git fetch of main failed: " + fetch.stderr.strip())
        return 1
    tip = git(repo, "rev-parse", "refs/remotes/%s/main" % REMOTE)
    try:
        _sha, state = load_state(repo)
    except StateError as exc:
        say("state branch: %s" % exc)
        return 3
    range_start = state["checkedTo"]
    live_until = began + job_minutes(repo, tip) * 60
    put_record(repo, run_id, "start", {"tip": tip, "rangeStart": range_start, "dispatched": dispatched, "startedAt": began,
                                        "liveUntil": live_until})
    run = {"id": run_id, "start": iso(began), "liveUntil": iso(live_until), "end": None, "stage": "close",
           "outcome": None, "detail": None, "wall": None}
    try:
        update_state(repo, lambda doc: dict(doc, run=run))
    except (StateError, GitError) as exc:
        say("start write failed, the run goes on: %s" % exc)

    problem = None
    try:
        git(repo, "checkout", "-q", "--force", "--detach", tip)
        result = remote_git(repo, "submodule", "update", "-q", "--init", "--recursive")
        if result.returncode != 0:
            problem = "submodule: git submodule update failed: " + (result.stderr or result.stdout).strip()
        elif git_try(repo, "merge-base", "--is-ancestor", range_start, tip).returncode != 0:
            problem = "checkedTo %s is not an ancestor of origin/main %s" % (range_start[:12], tip[:12])
    except GitError as exc:
        problem = "checkout: %s" % exc
    if problem:
        say(problem)
        put_record(repo, run_id, "close", {"error": problem, "startedAt": time.time(), "endedAt": time.time(), "wall": 0})
        return 1

    env = settings_env(repo)
    env["HARNESS_DEADLINE"] = str(began + cap_minutes * 60 * DEADLINE_SHARE)
    runner = os.path.join(repo, ".claude", "scripts", "harness_tests.py")
    closed = time.time()
    also = open_proof_rows(repo, tip, state)
    say("close %s..%s; %d open proof row(s) join the selection" % (range_start[:12], tip[:12], len(also)))
    command = [sys.executable, runner, "--close", "--now", "--range-start", range_start]
    if also:
        command += ["--also"] + also
    code, text = run_logged(command, cwd=repo, env=env,
                            log_path=os.path.join(run_dir(repo, run_id), "close.log"), echo=CLOSE_ECHO,
                            cap_seconds=cap_minutes * 60 * KILL_SHARE)
    stamped = (read_json(stamp_path(repo)) or {}).get("close")
    valid = (isinstance(stamped, dict) and stamped.get("head") == tip and stamped.get("base") == range_start
             and isinstance(stamped.get("ts"), (int, float)) and stamped["ts"] >= closed - 1)
    lines = [l for l in text.splitlines() if l.strip()]
    cause = next((l for l in reversed(lines) if CAUSE_LINE.search(l)), lines[-1] if lines else "no output")
    ended = time.time()
    put_record(repo, run_id, "close", {"exit": code, "timedOut": code is None, "startedAt": closed, "endedAt": ended,
                                       "wall": ended - closed, "record": stamped if valid else None, "cause": cause})
    return 0


def open_proof_rows(repo, tip, state):
    """This platform's open `proof` rows whose proof the tip still has, as the close's `--also` paths."""
    present = tracked_proofs(repo, tip)
    return sorted({present[row["name"]] for row in state["rows"]
                   if open_row(row) and row.get("kind") == "proof" and row.get("platform") == platform_name()
                   and row.get("name") in present})


def settled_by(record_):
    """Names of the proofs the close ran to a pass, and of those it ended red or excused."""
    if not record_:
        return set(), set()
    red = {r.get("proof") for r in record_.get("red") or []}
    other = red | set(record_.get("preExisting") or []) | set(record_.get("cannotRun") or []) | set(
        record_.get("uncommittedProofRed") or [])
    passed = set(record_.get("proofs") or []) - other
    return passed, red | set(record_.get("preExisting") or [])


# ---- stage gate --------------------------------------------------------------------------------------------------

def run_gate(repo, run_id, cap_minutes=DEFAULT_CAP_MINUTES):
    start = record(repo, run_id, "start")
    if not start:
        say("no start record for run %s: nothing to gate" % run_id)
        return 1
    main, problem = gate_declaration(repo)
    if main is None and not problem:
        put_record(repo, run_id, "gate", {"owed": False, "reason": "the project declares no gate_cadence.main", "wall": None})
        say("gate not owed: the project declares no gate_cadence.main")
        return 0
    owed, reason = gate_owed(repo, start["rangeStart"], start["tip"], start.get("dispatched", False))
    if not owed:
        put_record(repo, run_id, "gate", {"owed": False, "reason": reason, "wall": None})
        say("gate not owed: " + reason)
        return 0
    closed = record(repo, run_id, "close")
    skip = None
    if problem:
        skip = problem
    elif not closed or closed.get("error") or git_try(repo, "rev-parse", "HEAD").stdout.strip() != start["tip"]:
        skip = "the close stage left no checkout at the tip"
    if skip:
        put_record(repo, run_id, "gate", {"owed": True, "reason": reason, "skipped": skip, "wall": None})
        say("gate skipped: " + skip)
        return 1
    env = settings_env(repo)
    gate = list(main["command"])
    gate[0] = shutil.which(gate[0]) or gate[0]
    retry_exits = set(main.get("retry_exits", []))
    attempts, began = [], time.time()
    for attempt in range(1, GATE_ATTEMPTS + 1):
        say("gate attempt %d" % attempt)
        t0 = time.time()
        code, text = run_logged(gate, cwd=repo, env=env, log_path=os.path.join(run_dir(repo, run_id), "gate_%d.log" % attempt),
                                echo=GATE_ECHO, cap_seconds=max(cap_minutes * 60 * KILL_SHARE - (t0 - began), 1))
        attempts.append({"exit": code, "failedTests": [m.strip() for m in FAILED_TEST_LINE.findall(text)],
                         "seconds": time.time() - t0, "timedOut": code is None})
        if code is None or code not in retry_exits:
            break
    last = attempts[-1]
    ended = time.time()
    put_record(repo, run_id, "gate", {"owed": True, "reason": reason, "attempts": attempts, "exit": last["exit"],
                                      "failedTests": last["failedTests"], "timedOut": last["timedOut"], "startedAt": began,
                                      "endedAt": ended, "wall": ended - began})
    return 0


# ---- the linux row source ----------------------------------------------------------------------------------------

def repo_slug(url):
    text = (url or "").strip().rstrip("/")
    if text.endswith(".git"):
        text = text[:-4]
    parts = [p for p in text.replace("\\", "/").split("/") if p]
    return "/".join(parts[-2:]) if len(parts) >= 2 else ""


def gh_read(args, timeout=GH_READ_TIMEOUT_S):
    """(returncode or None, stdout, stderr) of a bounded `gh` call; a missing gh, a spawn fault or the timeout is
    None, so an unreadable source never stops the main check."""
    exe = shutil.which("gh")
    if not exe:
        return None, "", "gh is not on PATH"
    cmd = [exe, *args]
    try:
        if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
            done = subprocess.run(subprocess.list2cmdline(cmd), shell=True, capture_output=True, timeout=timeout)
        else:
            done = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "", str(exc)
    return (done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace"))


def proof_failures(log_text):
    """{proof file: its first case line} from a failed-step log: the proof-level `FAIL <seconds>s <path>` lines
    only, never the `| FAIL` case lines a proof prints under it."""
    found, current = {}, None
    for raw in log_text.splitlines():
        line = GHA_LINE_PREFIX.sub("", raw)
        match = PROOF_FAIL_LINE.match(line)
        if match:
            current = match.group(1).strip().rsplit("/", 1)[-1]
            found.setdefault(current, line.strip())
            continue
        if current and line.startswith("    | ") and found[current].startswith("FAIL "):
            found[current] = line.strip()
    return found


def proof_passes(log_text):
    """{proof file} of the proof-level `OK <seconds>s <path>` lines a verbose close prints. A proof a lane never
    ran has no line either way, so absence from this set is never a pass."""
    found = set()
    for raw in log_text.splitlines():
        match = PROOF_PASS_LINE.match(GHA_LINE_PREFIX.sub("", raw))
        if match:
            found.add(match.group(1).strip().rsplit("/", 1)[-1])
    return found


def baseline_session(slug, sha):
    code, out, _err = gh_read(["api", "repos/%s/commits/%s" % (slug, sha), "--jq", ".commit.message"])
    if code != 0:
        return None
    for line in out.splitlines():
        key, _, value = line.partition(":")
        if key.strip().lower() == "claude-session" and value.strip():
            return value.strip()
    return None


def read_baseline_source(repo, tip):
    """(source, rows) for the baseline's Linux lanes at the pin `tip` carries.

    `source` is `{readAt, status, reason, pendingSha, sha, failed, passed}`: `failed` is the run's red proof set and
    `passed` the proofs its log lists as passed, which is the only thing that clears an older row (a run red for a
    non-proof step proves nothing, and a lane that never ran its proofs lists neither). `rows` are the fresh
    `platform: linux` proof rows of a run that failed, for the proofs `tip` tracks: a proof the project lacks is a row
    nobody there can fix."""
    now = iso()
    source = {"readAt": now, "status": "unreadable", "reason": "", "pendingSha": None, "sha": None, "failed": None,
              "passed": None}

    def unreadable(reason):
        source["reason"] = reason
        return source, []

    shown = git_try(repo, "show", "%s:.claude/baseline.lock.json" % tip)
    if shown.returncode != 0:
        source.update(status="none", reason="no baseline.lock.json at the tip")
        return source, []
    try:
        lock = json.loads(shown.stdout)
    except ValueError:
        return unreadable("baseline.lock.json is not JSON")
    sha, slug = lock.get("synced_commit"), repo_slug(lock.get("baseline_repo"))
    if not sha or not slug:
        return unreadable("baseline.lock.json names no synced_commit or baseline_repo")

    code, out, err = gh_read(["run", "list", "--repo", slug, "--workflow", BASELINE_WORKFLOW, "--branch", "main",
                              "--limit", "30", "--json", "databaseId,headSha,status,conclusion,event"])
    try:
        runs = json.loads(out) if code == 0 else None
    except ValueError:
        runs = None
    if not isinstance(runs, list):
        return unreadable("gh run list: " + " ".join(err.split())[-200:])
    mine = [r for r in runs if r.get("headSha") == sha and r.get("event") == "push"]
    if not mine:
        source.update(status="pending", reason="no baseline push run for " + sha[:8] + " yet", pendingSha=sha)
        return source, []
    run = max(mine, key=lambda r: int(r.get("databaseId") or 0))
    if run.get("status") != "completed":
        source.update(status="pending", reason="baseline run %s is %s" % (run.get("databaseId"), run.get("status")),
                      pendingSha=sha)
        return source, []

    source.update(status="ok", sha=sha)
    code, out, err = gh_read(["run", "view", str(run.get("databaseId")), "--repo", slug, "--log"])
    if code != 0:
        return unreadable("gh run view %s: %s" % (run.get("databaseId"), " ".join(err.split())[-200:]))
    source["passed"] = proof_passes(out)
    if run.get("conclusion") == "success":
        source.update(failed=set(), reason="baseline run %s passed" % run.get("databaseId"))
        return source, []
    failed = proof_failures(out)
    if not failed:
        source["reason"] = "baseline run %s is %s with no proof-level FAIL line" % (
            run.get("databaseId"), run.get("conclusion"))
        return source, []
    session = baseline_session(slug, sha)
    present = tracked_proofs(repo, tip)
    rows = [new_row(name, "proof", sha, session, line, now, platform="linux")
            for name, line in sorted(failed.items()) if name in present]
    source.update(failed=set(failed), reason="baseline run %s %s" % (run.get("databaseId"), run.get("conclusion")))
    return source, rows


# ---- stage finalize ----------------------------------------------------------------------------------------------

def failed_test_ids(ids):
    """The failing test ids of a gate's `FAILED_TEST=` lines: a bare name that a full id in the list ends with is its
    duplicate (the gate lists both forms) and collapses into the full id."""
    ids = [i for i in dict.fromkeys(ids or []) if isinstance(i, str) and i]
    return [i for i in ids if not any(other != i and other.endswith("." + i) for other in ids)]


def judge_close(start, closed):
    """(complete, reason, record): whether the run's close finished, why not, and the runner's record when it is
    this run's own. A close that ran to a record finished, green or red: its reds and `cannotRun` entries become
    rows, never a reason to leave `checkedTo` behind. A killed, superseded or faulted close leaves no record."""
    if not closed:
        return False, "close: no record of this run", None
    if closed.get("error"):
        return False, "close: " + closed["error"], None
    if closed.get("timedOut"):
        return False, "close: killed by its cap", None
    rec = closed.get("record")
    if not rec:
        return False, "close: no close record from the runner (exit %s): %s" % (closed.get("exit"), closed.get("cause")), None
    if rec.get("head") != start["tip"] or rec.get("base") != start["rangeStart"]:
        return False, "close: the runner's record is not this run's range", None
    return True, None, rec


def judge_gate(gate, owed):
    """(ok, reason, failed ids, passed): an owed gate is a verdict only as exit 0 or as exit 1 naming its failed
    tests. The gate stage's own `owed: false` stands, so a stage and finalize never disagree on a project with no gate."""
    if not owed or (gate or {}).get("owed") is False:
        return True, None, [], False
    if not gate:
        return False, "gate: no record of this run", [], False
    if gate.get("skipped"):
        return False, "gate: not run: " + gate["skipped"], [], False
    if gate.get("timedOut"):
        return False, "gate: killed by its cap", [], False
    code = gate.get("exit")
    if code == 0:
        return True, None, [], True
    if code == 1:
        ids = failed_test_ids(gate.get("failedTests"))
        if ids:
            return True, None, ids, False
        return False, "gate: failed naming no failed test", [], False
    return False, "gate: exit %s is no verdict" % code, [], False


def first_failure_line(detail):
    lines = [l.strip() for l in (detail or "").splitlines() if l.strip()]
    line = next((l for l in lines if FAILURE_MARK.search(l)), lines[0] if lines else "")
    return line[:FIRST_LINE_MAX]


def new_row(name, kind, commit, session, first_line, now, cause=None, platform=None):
    return {"name": name, "kind": kind, "platform": platform or platform_name(), "commit": commit, "session": session,
            "firstLine": first_line, "firstSeen": now, "clearedAt": None, "clearedBy": None, "cause": cause}


def apply_result(doc, res):
    """The state after one run's result: this platform's rows clear or open, `checkedTo` and `run` update.
    A fresh red whose first failing line differs from an open row's is a second row, so a new break never
    hides behind an old one. Off Linux, a Linux row is the baseline lane's and clears only when a later baseline run's
    log lists that proof as passed (`passed`): a lane that never ran its proofs lists nothing, and absence is not a
    pass. A main check that runs on Linux proves Linux itself, so its Linux rows clear like any own-platform row."""
    now, tip, platform = res["now"], res["tip"], platform_name()
    linux = res.get("linux") or {}
    rows = doc["rows"]
    for row in rows:
        if not open_row(row):
            continue
        if row.get("platform") == "linux" and platform != "linux":
            if (linux.get("passed") is not None and row.get("kind") == "proof"
                    and row.get("commit") != linux.get("sha") and row["name"] in linux["passed"]):
                row["clearedAt"], row["clearedBy"] = now, linux["sha"]
            continue
        if row.get("platform") != platform:
            continue
        if (row.get("kind") == "proof" and (row["name"] not in res["present"] or row["name"] in res["passed"])) or (
                row.get("kind") == "gate" and res["gate_pass"]):
            row["clearedAt"], row["clearedBy"] = now, tip
    for fresh in res["rows"]:
        line = fresh.get("firstLine") if fresh["name"] in res["new_reds"] else None
        if not has_open_row(rows, fresh["name"], platform, line):
            rows.append(fresh)
    for fresh in res.get("linux_rows") or []:
        if not has_open_row(rows, fresh["name"], "linux"):
            rows.append(fresh)
    cutoff = parse_iso(now) - ROW_KEEP_DAYS * 86400
    doc["rows"] = [r for r in rows if open_row(r) or parse_iso(r["clearedAt"]) >= cutoff]
    if res["advance"]:
        doc["checkedTo"] = tip
    doc["run"] = dict(res["run"], end=now, stage="finalize")
    doc.setdefault("sources", {"baselineLinux": {"readAt": None, "status": "pending", "reason": "not read yet", "pendingSha": None}})
    if linux:
        doc["sources"]["baselineLinux"] = {key: linux.get(key) for key in ("readAt", "status", "reason", "pendingSha")}
    return doc


def run_finalize(repo, run_id):
    began = time.time()
    start = record(repo, run_id, "start")
    if not start:
        say("no start record for run %s: nothing to record" % run_id)
        return 1
    tip, range_start = start["tip"], start["rangeStart"]
    closed, gate = record(repo, run_id, "close"), record(repo, run_id, "gate")
    owed, _why = gate_owed(repo, range_start, tip, start.get("dispatched", False))
    close_ok, close_why, rec = judge_close(start, closed)
    gate_ok, gate_why, failed, gate_pass = judge_gate(gate, owed)
    now = iso()
    passed, _failing = settled_by(rec)
    present = tracked_proofs(repo, tip)
    rows, new_reds = [], set()
    if rec:
        for red in rec.get("red") or []:
            name, detail = red.get("proof"), red.get("detail")
            cause = red.get("cause")  # the runner classifies a timeout red and runs no bisect on it
            commit = None if cause else red.get("commit")
            rows.append(new_row(name, "proof", commit, session_of(repo, commit), first_failure_line(detail), now,
                                cause=cause))
            if cause is None:
                new_reds.add(name)
        for name in rec.get("cannotRun") or []:
            if name == "<behavior claims>":
                rows.append(new_row(name, "proof", None, None, "could not name the proof in run " + run_id, now,
                                    cause="uncovered"))
            else:
                rows.append(new_row(name, "proof", None, None, "could not run in the close", now))
    if close_ok:
        for name in rec.get("preExisting") or []:
            rows.append(new_row(name, "proof", None, None, "fails at the range start", now))
    if failed:
        commit = non_harness_commit(repo, range_start, tip)
        for test_id in failed:
            rows.append(new_row(test_id, "gate", commit, session_of(repo, commit), "gate FAIL: " + test_id, now))
    advance = close_ok and gate_ok
    reasons = [why for why in (close_why, gate_why) if why]
    outcome = "no-verdict" if not advance else "red" if (rec.get("red") or rec.get("cannotRun") or failed) else "green"
    wall = {"close": (closed or {}).get("wall"), "gate": (gate or {}).get("wall"), "finalize": None}
    linux, linux_rows = read_baseline_source(repo, tip)
    res = {"now": now, "tip": tip, "advance": advance, "rows": rows, "passed": passed, "gate_pass": gate_pass,
           "present": set(present), "new_reds": new_reds, "linux": linux, "linux_rows": linux_rows}
    wall["finalize"] = time.time() - began
    res["run"] = {"id": run_id, "start": iso(start["startedAt"]), "liveUntil": iso(start["liveUntil"]), "outcome": outcome,
                  "detail": "; ".join(reasons) or None, "wall": wall}
    try:
        update_state(repo, lambda doc: apply_result(doc, res))
    except (StateError, GitError) as exc:
        say("state not written: %s" % exc)
        return 1
    say("run %s: %s%s; checkedTo %s" % (run_id, outcome, " (" + res["run"]["detail"] + ")" if res["run"]["detail"] else "",
                                         "moved to " + tip[:12] if advance else "stays"))
    return 0 if advance else 1


# ---- --rows ------------------------------------------------------------------------------------------------------

def print_rows(repo, session):
    """Open rows of the local state ref; a missing or malformed ref prints nothing and ends 2."""
    shown = git_try(repo, "show", "%s:%s" % (STATE_REF, STATE_FILE))
    if shown.returncode != 0:
        print("main_check: %s is not readable here: fetch it first (git fetch %s %s)" % (STATE_BRANCH, REMOTE, STATE_BRANCH),
              file=sys.stderr)
        return 2
    try:
        doc = parse_state(shown.stdout)
    except StateError as exc:
        print("main_check: %s: %s" % (STATE_BRANCH, exc), file=sys.stderr)
        return 2
    groups = {}
    for row in doc["rows"]:
        if open_row(row) and (not session or row.get("session") == session):
            groups.setdefault(row.get("cause") or "unclassified", []).append(row)
    for cause in sorted(groups):
        print("cause=%s" % cause)
        for row in groups[cause]:
            print("  %s %s kind=%s commit=%s session=%s %s" % (
                row.get("name"), row.get("platform"), row.get("kind"), (row.get("commit") or "-")[:8],
                row.get("session") or "-", row.get("firstLine") or ""))
    return 0


def _unchecked(repo, checked):
    """(commits on origin/main past `checked`, the committer time of the oldest one). Either is None when git cannot
    read the range, which the health line counts as overdue."""
    counted = git_try(repo, "rev-list", "--count", "%s..%s" % (checked, ORIGIN_MAIN))
    if counted.returncode != 0 or not counted.stdout.strip().isdigit():
        return None, None
    count = int(counted.stdout)
    if count == 0:
        return 0, None
    listed = git_try(repo, "rev-list", "%s..%s" % (checked, ORIGIN_MAIN))
    oldest = listed.stdout.split()[-1] if listed.returncode == 0 and listed.stdout.split() else None
    when = git_try(repo, "show", "-s", "--format=%ct", oldest).stdout.strip() if oldest else ""
    return count, int(when) if when.isdigit() else None


def run_cap(run):
    """The last run's cap in seconds: its job's timeout, from its start and liveUntil."""
    try:
        return parse_iso(run["liveUntil"]) - parse_iso(run["start"])
    except (KeyError, TypeError, ValueError):
        return DEFAULT_CAP_MINUTES * 60


def status(repo, now=None):
    """The one health line `/session_end` prints (plan §5), or None when the state is clean and current. Reads the
    local state ref only: the caller fetches the branch first."""
    now = time.time() if now is None else now
    shown = git_try(repo, "show", "%s:%s" % (STATE_REF, STATE_FILE))
    if shown.returncode != 0:
        return "main: never checked"
    try:
        doc = parse_state(shown.stdout)
    except StateError as exc:
        return "main: check state unreadable (%s)" % str(exc).replace("unreadable: ", "", 1)
    run = doc.get("run") or {}
    unended = run.get("end") is None and bool(run.get("liveUntil"))
    if unended and now > parse_iso(run["liveUntil"]):
        return "main: check stopped at %s" % run.get("stage")
    checked = doc["checkedTo"]
    count, oldest = _unchecked(repo, checked)
    overdue = count is None or (oldest is not None and now - oldest > run_cap(run))
    if not unended and count != 0 and (run.get("outcome") == "no-verdict" or overdue):
        behind = "unknown" if count is None else str(count)
        return "main: checked to %s, %s behind origin/main; last run %s" % (checked[:8], behind, run.get("outcome") or "none")
    source = (doc.get("sources") or {}).get("baselineLinux") or {}
    if source.get("status") == "unreadable":
        return "main: linux lanes unreadable (%s)" % (source.get("reason") or "no reason")
    return None


def build_parser():
    parser = argparse.ArgumentParser(description="Prove the landed range on main and record the verdict as state rows.")
    parser.add_argument("stage", nargs="?", choices=("close", "gate", "finalize"))
    parser.add_argument("--repo", help="the main-check worktree (default: the current repository)")
    parser.add_argument("--run-id", help="keys this run's records (default: a timestamp)")
    parser.add_argument("--dispatched", action="store_true", help="close: a manual run, which owes the gate on any tree")
    parser.add_argument("--cap-minutes", type=float, default=DEFAULT_CAP_MINUTES, help="the step's timeout-minutes")
    parser.add_argument("--rows", action="store_true", help="print the open rows of the local state ref")
    parser.add_argument("--session", help="with --rows, only this session's rows")
    parser.add_argument("--status", action="store_true", help="print the health line of the local state ref, if any")
    return parser


def main(argv=None):
    # The runner's console may be cp1252 while the gate's log is UTF-8: an echoed test title must never crash the run.
    for channel in (sys.stdout, sys.stderr):
        channel.reconfigure(errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.session and not args.rows:
        parser.error("--session requires --rows")
    if sum([bool(args.stage), args.rows, args.status]) != 1:
        parser.error("pass a stage (close, gate, finalize), --rows or --status")
    repo = repo_root(args.repo)
    if args.status:
        line = status(repo)
        if line:
            print(line)
        return 0
    if args.rows:
        return print_rows(repo, args.session)
    run_id = args.run_id or time.strftime("%Y%m%d-%H%M%S")
    if args.stage == "close":
        return run_close(repo, run_id, args.dispatched, args.cap_minutes)
    if args.stage == "gate":
        return run_gate(repo, run_id, args.cap_minutes)
    return run_finalize(repo, run_id)


if __name__ == "__main__":
    sys.exit(main())
