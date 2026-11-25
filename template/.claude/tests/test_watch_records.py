"""End-to-end proof of one watcher event per record and durable watcher state.

Two modes: the Monitor stream (`<record> ...`, unchanged) and the session wake route
(`--session --wake`), which is what the Stop-hook guard arms. Every session case plants a record
set in a temp dir and drives the real watcher, reading its stdout FILE (a backgrounded watcher's
stdout arrives only at exit) so a mid-run assertion can see the arm-time lines.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
import _hook_state  # noqa: E402
# The machine's live usage-limit markers must not reach any case: each case plants its own.
NO_MARKERS = tempfile.mkdtemp(prefix="exhausted_none_")
os.environ["SIDECAR_EXHAUSTED_DIR"] = NO_MARKERS
# wake_digest waits DELIVERABLE_GRACE_SEC (3 s) for a fan-out job's deliverable before it reads none: every
# DONE line of a case with no deliverable paid it. test_wake_digest.py owns that grace; here every watcher
# child starts with it at 0.2 s (a sitecustomize behind the read recorder, which stays first on PYTHONPATH).
PLANT = tempfile.mkdtemp(prefix="watch_plant_")
# The session's transcript root: an empty planted login, not the machine's ~/.claude/projects, which the watcher
# globs on every wake (about a second per watcher on a real store, and a live peer's transcript could answer).
os.environ["CLAUDE_CONFIG_DIR"] = str(Path(PLANT) / "login")
Path(PLANT, "login", "projects").mkdir(parents=True)
Path(PLANT, "sitecustomize.py").write_text(
    "import sys\nsys.path.append(%r)\ntry:\n    import wake_digest\n    wake_digest.DELIVERABLE_GRACE_SEC = 0.2\n"
    "except Exception:\n    pass\n" % str(ROOT / "tools"), encoding="utf-8")
_recorder = os.environ.get("HARNESS_READ_RECORDER") if os.environ.get("HARNESS_READ_LOG") else None
_rest = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p and p != _recorder]
os.environ["PYTHONPATH"] = os.pathsep.join(([_recorder] if _recorder else []) + [PLANT] + _rest)
WATCH = Path(os.environ.get("WATCH_RECORDS_UNDER_TEST", ROOT / "tools" / "watch_records.py"))
SID = "watchproof-0000-0000-0000-000000000000"
PASS = 0
# A hang bound, never a wait: every wait here is for an observable event, and one poll of a live watcher
# under a 12-wide proof pool can take tens of seconds (the roster and pid probes spawn Git Bash and `ps`).
HANG_BOUND = 120


def check(condition, label, detail=""):
    global PASS
    if not condition:
        raise AssertionError("%s: %s" % (label, detail))
    PASS += 1
    print("PASS", label)


def line(pid):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + (
        "winpid=%d " % pid if os.name == "nt" else "") + "pid=%d" % pid


def start(records, state):
    env = dict(os.environ, CLAUDE_CODE_SESSION_ID=SID, HARNESS_HOOK_STATE_DIR=str(state),
               WATCH_RECORDS_POLL_SEC="0.05", WATCH_RECORDS_POLL_MARK=str(Path(state).parent / "polls.log"))
    Path(Path(state).parent / "polls.log").unlink(missing_ok=True)
    return subprocess.Popen([sys.executable, str(WATCH), *map(str, records)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)


def until(predicate, seconds=HANG_BOUND, step=0.05):
    """Wait for an observable event. `seconds` only bounds a hang: a loaded machine slows startup, never the verdict."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


def poll_count(state):
    """The polls a watcher on `state` has finished: the watcher appends one line per poll to the file
    WATCH_RECORDS_POLL_MARK names."""
    try:
        return len((Path(state).parent / "polls.log").read_text(encoding="utf-8").splitlines())
    except OSError:
        return 0


def wait_polls(state, count, proc=None, seconds=HANG_BOUND):
    """True once the watcher has finished `count` polls; False early when `proc` exits first. `seconds` bounds a hang."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if poll_count(state) >= count:
            return True
        if proc is not None and proc.poll() is not None:
            return False
        time.sleep(0.02)
    return poll_count(state) >= count


def polled(state, polls=1, proc=None, seconds=HANG_BOUND):
    """Wait until a watcher has registered in `state` and finished `polls` polls. A case that reads silence waits
    for the poll that made the stay decision: an exit written before the first poll is an event present at arm."""
    path = Path(state) / (SID[:8] + ".json")
    deadline = time.time() + seconds
    while time.time() < deadline:
        # The watcher renames a new copy over this file as it registers, and an open during the rename is refused:
        # the hooks' reader returns {} then, and the next pass reads the new copy.
        if _hook_state.read_json_salvage(str(path)).get("sidecar_watchers"):
            break
        time.sleep(0.05)
    return wait_polls(state, polls, proc=proc, seconds=seconds)


def polled_after(state, stage, polls=2, proc=None):
    """Run `stage` between polls, then wait for `polls` polls to finish: one may already be in flight across the
    write, so the next poll is the one certain to have read it."""
    before = poll_count(state)
    stage()
    return wait_polls(state, before + polls, proc=proc)


def collect(process):
    try:
        return process.communicate(timeout=HANG_BOUND)
    except subprocess.TimeoutExpired:
        for record in process.args[2:]:
            Path(record + ".exit").write_text("1")
        process.communicate(timeout=HANG_BOUND)
        raise AssertionError("watcher did not exit before rescue files")


# ---------------------------------------------------------------- Monitor stream
with tempfile.TemporaryDirectory(prefix="watch_records_") as temp:
    root = Path(temp)
    state = root / "state"
    state.mkdir()
    done = root / "done.record.json"
    (root / "done.record.json.out").write_text("readable")
    (root / "done.record.json.exit").write_text("3")
    proc = start([done], state)
    output, error = collect(proc)
    check(proc.returncode == 0 and "DONE done exit=3" in output and "ALL REPORTED" in output
          and (root / "done.record.json.out").read_text() == "readable", "DONE has readable output and final line")

    died = root / "died.record.json"
    (root / "died.record.json.pid").write_text(line(99999999) + "\n")
    proc = start([died], state)
    output, error = collect(proc)
    check("DIED died" in output and output.count("DIED died") == 1, "dead launcher reports once")

    queued = root / "queued.record.json"
    inventory = root / "fanout-one.inventory.json"
    inventory.write_text(json.dumps({"fanoutProcess": line(os.getpid()),
                                     "jobs": [{"recordPath": str(queued)}]}))
    proc = start([queued], state)
    state_file = state / (SID[:8] + ".json")
    check(polled(state, polls=2, proc=proc), "the watcher polled while the fan-out was live")
    registration = _hook_state.read_json_salvage(str(state_file)).get("sidecar_watchers")
    check(bool(registration) and str(queued) in registration[0]["records"], "watcher registers its records")
    check(proc.poll() is None, "live fan-out keeps a no-pid job queued")
    (root / "queued.record.json.pid").write_text(line(os.getpid()) + "\n")
    (root / "queued.record.json.out").write_text("ready")
    (root / "queued.record.json.exit").write_text("0")
    output, error = collect(proc)
    check("DONE queued exit=0" in output and "DIED" not in output, "queue becomes a running result")
    check(not _hook_state.read_json_salvage(str(state_file)).get("sidecar_watchers"),
          "watcher deregisters on exit")

    unknown = root / "unknown.record.json"
    proc = start([unknown], state)
    check(polled(state, polls=2, proc=proc), "the watcher polled an unidentifiable record")
    check(proc.poll() is None, "no identity stays watched")
    (root / "unknown.record.json.exit").write_text("0")
    output, error = collect(proc)
    check(output.count("NO-IDENTITY unknown") == 1 and "DONE unknown exit=0" in output,
          "unknown warns once then finishes")

    dead_queue = root / "dead_queue.record.json"
    inventory.write_text(json.dumps({"fanoutProcess": line(99999999),
                                     "jobs": [{"recordPath": str(dead_queue)}]}))
    proc = start([dead_queue], state)
    output, error = collect(proc)
    check("DIED dead_queue" in output, "dead fan-out identifies never-started record")

    claim = root / "claim.record.json"
    (root / "claim.record.json.pid").write_text(line(os.getpid()) + "\n")
    proc = start([claim], state)
    check(polled(state, polls=2, proc=proc), "the watcher polled a live detached claim")
    check(proc.poll() is None, "live detached first claim remains watched")
    (root / "claim.record.json.exit").write_text("0")
    output, error = collect(proc)
    check("DONE claim exit=0" in output and "DIED" not in output, "detached claim finishes normally")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import watch_records
import wake_digest
wake_digest.DELIVERABLE_GRACE_SEC = 0.2


# The window is defined here, so the call-open check needs nothing from `dispatch_table`, its consumer.
_saved_dispatch_table = sys.modules.get("dispatch_table")
sys.modules["dispatch_table"] = None  # an import of it raises ImportError
try:
    _window = getattr(watch_records, "FANOUT_PREFLIGHT_S", None)
    check(isinstance(_window, int), "watch_records defines FANOUT_PREFLIGHT_S", _window)
    _now = time.time()
    check(watch_records.call_open({"issued_at": _now - _window + 30}, now=_now) is True,
          "a call inside the preflight window is open without importing dispatch_table")
    check(watch_records.call_open({"issued_at": _now - _window - 30}, now=_now) is False,
          "a call past the preflight window is closed without importing dispatch_table")
finally:
    if _saved_dispatch_table is None:
        sys.modules.pop("dispatch_table", None)
    else:
        sys.modules["dispatch_table"] = _saved_dispatch_table

# The fan-out saves an inventory by renaming a temp file over it, so a poll can be refused mid-rename. Its
# `writtenAt` is the attempt's creation stamp; a read that fails must not fall back to the moved file's mtime,
# which lands after the attempt's own finish row and hides its refusal.
with tempfile.TemporaryDirectory(prefix="inventory_written_") as temp:
    read_inventory = Path(temp) / "fanout-read.inventory.json"
    read_inventory.write_text(json.dumps({"writtenAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(1000)), "jobs": []}), encoding="utf-8")
    unseen_inventory = Path(temp) / "fanout-unseen.inventory.json"
    unseen_inventory.write_text(json.dumps({"writtenAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(2000)), "jobs": []}), encoding="utf-8")
    real_open = open

    def refused(path, *args, **kwargs):
        if os.path.basename(str(path)) in (read_inventory.name, unseen_inventory.name) and refused.left:
            refused.left -= 1
            raise PermissionError(13, "the fan-out is renaming this inventory")
        return real_open(path, *args, **kwargs)

    refused.left = 0
    watch_records.open = refused
    try:
        seen = watch_records.inventory_written({"inventory": str(read_inventory)})
        check(seen == 1000, "an inventory is read once and its writtenAt is the attempt stamp", seen)
        refused.left = 3  # every read of the rename is refused
        kept = watch_records.inventory_written({"inventory": str(read_inventory)})
        check(kept == 1000, "a read refused mid-rename keeps the last successful writtenAt, never the moved mtime",
              kept)
        refused.left = 3
        unseen = watch_records.inventory_written({"inventory": str(unseen_inventory)})
        check(unseen is None, "an inventory no poll could read is no evidence, never its moved mtime", unseen)
    finally:
        del watch_records.open


def test_finished_fanout_resolves_fold_flag_without_inventory_path():
    with tempfile.TemporaryDirectory(prefix="fold_inventory_") as temp:
        root = Path(temp)
        record = root / "seat.record.json"
        Path(str(record) + ".exit").write_text("0")
        entry = {"record": str(record), "fanout_dir": str(root), "writes": True}
        for folds in (True, None, False):
            data = {"fanoutProcess": line(os.getpid()), "jobs": [{"recordPath": str(record)}]}
            if folds is not None:
                data["afterSeats"] = folds
            (root / "fanout-proof.inventory.json").write_text(json.dumps(data))
            assert watch_records.writer_launcher_running(entry) is (folds is not False), folds


with tempfile.TemporaryDirectory(prefix="running_writers_") as temp:
    import dispatch_table
    root = Path(temp)
    session = root / "session"
    entry = {"kind": "workflow", "id": "writer", "name": "fold",
             "run_dir": str(session / "subagents" / "workflows" / "writer"),
             "writes": True, "launched_at": time.time() - 10,
             "jobs": [{"label": "fix", "model": "sonnet"}]}
    survey = dict(entry, id="survey", writes=False)
    saved_read = dispatch_table.read_table
    try:
        dispatch_table.read_table = lambda sid: {"dispatch_table_pending": [entry, survey]}
        writers = watch_records.running_writers(SID)
        check(len(writers) == 1 and writers[0]["name"] == "fold"
              and writers[0]["labels"] == ["fix"] and writers[0]["elapsed_s"] >= 10,
              "writer with absent host record runs; non-writer omitted", writers)
        (session / "workflows").mkdir(parents=True)
        (session / "workflows" / "writer.json").write_text(json.dumps({"status": "completed"}))
        check(watch_records.running_writers(SID) == [], "completed writer omitted")
        check(watch_records.running_writers(None) == [], "no session lists no writer")
        record = root / "fix.record.json"
        Path(str(record) + ".pid").write_text(line(os.getpid()))
        sidecar = {"kind": "sidecar", "id": "fix", "writes": True,
                   "record": str(record), "jobs": [{"label": "fix", "model": "sonnet"}]}
        dispatch_table.read_table = lambda sid: {"dispatch_table_pending": [sidecar]}
        check(len(watch_records.running_writers(SID)) == 1, "live sidecar writer listed")
        Path(str(record) + ".exit").write_text("0")
        check(watch_records.running_writers(SID) == [], "exited sidecar writer omitted")
        inventory = root / "fanout-writer.inventory.json"
        inventory.write_text(json.dumps({"fanoutProcess": line(os.getpid())}))
        sidecar.update(fanout_dir=str(root), inventory=str(inventory), issued_at=time.time())
        check(len(watch_records.running_writers(SID)) == 1,
              "fold fanout writer remains live after its review seat exits")
        for folds in (True, None, False):
            payload = {"fanoutProcess": line(os.getpid()),
                       "jobs": [{"recordPath": str(record)}]}
            if folds is not None:
                payload["afterSeats"] = folds
            inventory.write_text(json.dumps(payload))
            check(watch_records.writer_launcher_running(sidecar) is (folds is not False),
                  "finished writer waits only for folding or legacy fanout: %r" % folds)
            check(bool(watch_records.running_writers(SID)) is (folds is not False),
                  "close waits only for folding or legacy fanout: %r" % folds)
        inventory.write_text(json.dumps({"fanoutProcess": line(os.getpid())}))
        here, elsewhere = root / "checkout_a", root / "checkout_b"
        here.mkdir(); elsewhere.mkdir()
        jobs_file = root / "jobs.json"
        jobs_file.write_text(json.dumps({"jobs": [{"label": "fix", "workdir": str(elsewhere)}]}))
        sidecar["jobs"] = [{"label": "fix", "model": "sonnet", "jobs_path": str(jobs_file)}]
        check(watch_records.running_writers(SID, repo=str(here)) == [],
              "a writer whose jobs write in another checkout does not hold this checkout's close")
        check(len(watch_records.running_writers(SID, repo=str(elsewhere))) == 1,
              "a writer whose jobs write in this checkout holds its close")
        # A detached fan-out's per-job entry carries no jobs file; its inventory names each job's workdir.
        inventory.write_text(json.dumps({"fanoutProcess": line(os.getpid()),
                                         "jobs": [{"label": "fix", "workdir": str(elsewhere)}]}))
        sidecar["jobs"] = [{"label": "fix", "model": "sonnet", "jobs_path": None}]
        check(watch_records.running_writers(SID, repo=watch_records.PROJECT_ROOT) == [],
              "a detached fan-out job whose inventory names a worktree does not hold the project checkout's close")
        check(len(watch_records.running_writers(SID, repo=str(elsewhere))) == 1,
              "a detached fan-out job whose inventory names this workdir holds its close")
        sidecar["jobs"] = [{"label": "fix", "model": "sonnet", "jobs_path": str(jobs_file)}]
        jobs_file.write_text(json.dumps({"jobs": [{"label": "fix"}]}))
        inventory.write_text(json.dumps({"fanoutProcess": line(os.getpid())}))
        check(len(watch_records.running_writers(SID, repo=watch_records.PROJECT_ROOT)) == 1,
              "a job with no workdir writes in the project checkout")
        own = root / "own.record.json"
        own.write_text(json.dumps({"launchId": "own-launch"}))
        Path(str(own) + ".pid").write_text(line(os.getpid()))
        dispatch_table.read_table = lambda sid: {"dispatch_table_pending": [
            {"kind": "sidecar", "id": "own", "writes": True, "record": str(own),
             "jobs": [{"label": "fix", "model": "fixture-model"}]}]}
        check(len(watch_records.running_writers(SID)) == 1, "live sidecar writer listed without an own launch")
        check(watch_records.running_writers(SID, own_launch="own-launch") == [],
              "a caller's own dispatch (its launch id) is not a writer it waits on")
        check(len(watch_records.running_writers(SID, own_launch="other-launch")) == 1,
              "a different launch id leaves the writer listed")
    finally:
        dispatch_table.read_table = saved_read

real_root, rel_root = watch_records.PROJECT_ROOT, tempfile.mkdtemp(prefix="rel_root_")
watch_records.PROJECT_ROOT = rel_root
try:
    rel_dir = Path(rel_root) / "run"
    rel_dir.mkdir()
    (rel_dir / "fanout-rel.inventory.json").write_text(json.dumps({
        "fanoutProcess": line(os.getpid()), "jobs": [{"recordPath": str(rel_dir / "rel.record.json")}]}))
    check(bool(watch_records.inventory_process(os.path.join("run", "rel.record.json"))),
          "a relative -R record resolves its process from an absolute inventory recordPath")
finally:
    watch_records.PROJECT_ROOT = real_root
    import shutil
    shutil.rmtree(rel_root, ignore_errors=True)

# A check-in names an inventory-only job QUEUED, and a job's last write never comes from a longer sibling label.
ck_root = Path(tempfile.mkdtemp(prefix="checkin_"))
try:
    ck_x, ck_q, ck_x2 = (ck_root / (n + ".record.json") for n in ("x", "q", "x-2"))
    (ck_root / "fanout-ck.inventory.json").write_text(json.dumps({
        "fanoutProcess": line(os.getpid()), "jobs": [{"recordPath": str(r)} for r in (ck_x, ck_q, ck_x2)]}))
    for r in (ck_x, ck_x2):
        Path(str(r) + ".pid").write_text(line(os.getpid()) + "\n")
    (ck_root / "x.stream").write_text("{}\n")
    stale = time.time() - 1500
    os.utime(ck_root / "x.stream", (stale, stale))
    (ck_root / "x-2.stream").write_text("{}\n")
    lines = watch_records.checkin_lines({"u": {"records": [("x", str(ck_x)), ("q", str(ck_q)), ("x-2", str(ck_x2))]}})
    check("QUEUED q" in lines and not any(t.startswith("RUNNING q") for t in lines),
          "a check-in names an inventory-only queued job QUEUED, never RUNNING", lines)
    check(any(t.startswith("RUNNING x for") and "last write 25 min ago" in t for t in lines),
          "a longer sibling label's file is never the job's last write", lines)
finally:
    shutil.rmtree(ck_root, ignore_errors=True)

# ---------------------------------------------------------------- session wake route


def wake_env(root, extra=None):
    env = dict(os.environ, CLAUDE_CODE_SESSION_ID=SID, HARNESS_HOOK_STATE_DIR=str(root / "state"),
               WATCH_RECORDS_POLL_SEC="0.05", CLAUDE_PROJECT_DIR=str(root),
               WATCH_RECORDS_POLL_MARK=str(root / "polls.log"))
    env.update(extra or {})
    return env


def put_state(root, entries):
    """Replace the session's hook state under its lock, as every hook writes it: the live watcher reads and
    rewrites this file, so a plain write is refused while it holds the file, or lands inside its update."""
    _hook_state.write_json_atomic(str(root / "state" / (SID[:8] + ".json")), {"dispatch_table_pending": entries},
                                  raising=True)


def put_json(path, doc):
    """Write a document the live watcher polls the way the fan-out saves an inventory: a temp file, then a
    rename that waits out a reader. A truncate-then-write lets one poll read an empty file as no inventory."""
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(doc))
    _hook_state.replace_path(str(tmp), str(path))


def inventory_stamp(seconds):
    """The stamp form a fan-out inventory's `writtenAt` carries, for an epoch time."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def journal_row(record, **row):
    """Append one launcher journal row, in the launcher's shape: every row carries ts, schema and identity."""
    row = dict({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "schema": 1, "launchId": "L",
                "record": str(record), "session": None, "label": "proof", "call": None}, **row)
    with open(str(record) + ".journal.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def entry(label, record, fanout=None, inventory=None):
    return {"kind": "sidecar",
            "id": ("%s:%s:call" % (fanout, label)) if fanout else str(record),
            "record": str(record) if record else None,
            "fanout_dir": str(fanout) if fanout else None,
            "inventory": str(inventory) if inventory else None,
            "launched_at": time.time(), "issued_at": time.time() - 1,
            "jobs": [{"label": label, "launcher": "proof"}]}


def write_inventory(path, jobs, pid, written=None):
    """`jobs` are (label, record) or (label, record, wakeWith): a job with a group wakes only when the last
    member of its group ends (watch_records.group_holds); one with none is its own unit. `written` is the
    fan-out's creation stamp, `inventory_stamp()`'s form; a later save keeps it."""
    rows = []
    for label, record, *group in jobs:
        row = {"label": label, "recordPath": str(record)}
        if group and group[0]:
            row["wakeWith"] = group[0]
        rows.append(row)
    # `afterSeats: False` is what run_jobs writes: no stage follows the seats, so a seat's clean end is
    # actionable alone and the fan-out's live driver does not hold it.
    put_json(path, {"parentSessionId": SID, "fanoutId": "proof", "afterSeats": False,
                    "fanoutProcess": line(pid), "writtenAt": written or inventory_stamp(time.time()), "jobs": rows})


def fanout_exits(path, jobs):
    """Rewrite a running fan-out's inventory after its launcher exited, as `save_inventory` does: the same
    document is saved again, so `writtenAt` stays as the fan-out first wrote it and the file's mtime moves."""
    written = json.loads(path.read_text(encoding="utf-8"))["writtenAt"]
    write_inventory(path, jobs, 99999999, written=written)


def sleeper():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


class Session:
    def __init__(self, name):
        self.root = Path(tempfile.mkdtemp(prefix="wake_%s_" % name))
        (self.root / "state").mkdir()
        self.out = self.root / "watcher.out"
        self.err = self.root / "watcher.err"

    def arm(self, extra=None, flags=()):
        (self.root / "polls.log").unlink(missing_ok=True)
        self.proc = subprocess.Popen([sys.executable, str(WATCH), "--session", "--wake", *flags],
                                     stdout=open(self.out, "w"), stderr=open(self.err, "w"),
                                     text=True, env=wake_env(self.root, extra))
        return self.proc

    def text(self):
        return self.out.read_text(encoding="utf-8", errors="replace")

    def exits(self, seconds=HANG_BOUND):
        try:
            self.proc.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            return False
        return True


def session_state(root):
    path = root / "state" / (SID[:8] + ".json")
    return json.loads(path.read_text()) if path.exists() else {}


def under_test(code, env=None):
    """Run `code` in a fresh interpreter with the module under test importable as `watch_records`."""
    prelude = ("import sys; sys.path.insert(0, %r); sys.path.insert(0, %r); import watch_records as w; "
               % (str(ROOT / "hooks"), str(Path(WATCH).resolve().parent)))
    return subprocess.run([sys.executable, "-c", prelude + code], capture_output=True, text=True,
                          timeout=60, env=dict(os.environ, **(env or {})))


def arm_observed(s, condition, extra=None, flags=()):
    """Arm the real watcher with a probe on its source: `s.seen` appears once `condition` (an expression over `units`, the
    poll's result, and `polls`, the polls begun) holds, so a test acts on what the watcher has done, not on elapsed time."""
    s.seen = s.root / "watcher.seen"
    wrapper = s.root / "arm_observed.py"
    wrapper.write_text("""
import sys
sys.path.insert(0, %r)
import watch_records as w
real = w._session_source


def wrapped(session_id, *rest):
    poll = real(session_id, *rest)
    state = {"polls": 0}

    def observed():
        state["polls"] += 1
        polls, units = state["polls"], poll()
        if %s:
            open(%r, "w").close()
        return units
    observed.shells = poll.shells
    return observed


w._session_source = wrapped
sys.exit(w.main(["--session", "--wake", *%r]))
""" % (str(Path(WATCH).resolve().parent), condition, str(s.seen), list(flags)), encoding="utf-8")
    (s.root / "polls.log").unlink(missing_ok=True)
    s.proc = subprocess.Popen([sys.executable, str(wrapper)], stdout=open(s.out, "w"), stderr=open(s.err, "w"),
                              text=True, env=wake_env(s.root, extra or getattr(s, "extra", None)))
    return until(s.seen.exists)


def killed(process):
    process.kill()
    process.wait()


NO_GRACE = ["--await-launch=0"]


def still_watching(s):
    """True when two polls finished with the watcher still running (it has live work and no decision event); it is
    then stopped."""
    armed = polled(s.root / "state", polls=2, proc=s.proc)
    if armed:
        killed(s.proc)
    return armed


def exits_idle(s):
    """A session watch with nothing live exits `NONE`: it has no work left. True when it exited 0 with `NONE` last."""
    return s.exits() and s.proc.returncode == 0 and s.text().strip().splitlines()[-1:] == ["NONE"]


with tempfile.TemporaryDirectory(prefix="watch_wake_") as temp:
    base = Path(temp)

    # The marker text comes from the real admission owner, not a hand-written log fixture.
    s = Session("memory")
    record = s.root / "memory.record.json"
    Path(str(record) + ".pid").write_text(line(os.getpid()) + "\n")
    emitted = under_test("import launch_memory as lm; lm.wait_for_headroom(iter([(1, 2), (20, 10)]).__next__, "
                         "lambda _: None, lm._log, lm.Floors(12, 8, 1), 'memory', read_holders=lambda: '')")
    journal_row(record, event="waiting-memory", message=emitted.stderr.splitlines()[0])
    put_state(s.root, [entry("memory", record)])
    s.arm({"WATCH_RECORDS_CHECKIN_SEC": "0.3"})
    finished = s.exits()
    check(finished and "WAITING-MEMORY memory" in s.text() and "commit 1.00 GiB" in s.text(),
          "session watcher exposes admission wait with readings", s.text())

    # --- a serial fan-out: job 1 already ended at arm, job 2 queued, then job 2 ends
    s = Session("serial")
    fan = s.root / "fan"
    fan.mkdir()
    a1, a2 = fan / "a1.record.json", fan / "a2.record.json"
    inv = fan / "fanout-proof.inventory.json"
    write_inventory(inv, [("a1", a1, "lane"), ("a2", a2, "lane")], os.getpid())
    a1.write_text("{}")
    Path(str(a1) + ".exit").write_text("0")
    put_state(s.root, [entry("a1", a1, fan, inv), entry("a2", a2, fan, inv)])
    s.arm()
    check(until(lambda: "DONE a1 exit=0" in s.text()), "ended record at arm prints once", s.text())
    check(wait_polls(s.root / "state", 2, proc=s.proc), "the watcher finished its second poll")
    check(s.proc.poll() is None and "NONE" not in s.text() and "STILL RUNNING" not in s.text(),
          "an ended job beside a queued sibling wakes nobody", s.text())
    check(polled_after(s.root / "state", lambda: Path(str(a2) + ".pid").write_text(line(os.getpid()) + "\n"),
                       proc=s.proc), "the watcher polled after the queued sibling turned live")
    check(s.proc.poll() is None, "a queued sibling turning live still wakes nobody", s.text())
    Path(str(a2) + ".exit").write_text("0")
    fanout_exits(inv, [("a1", a1, "lane"), ("a2", a2, "lane")])
    check(s.exits() and s.proc.returncode == 0, "the last job ending wakes the session", s.text())
    check("DONE a2 exit=0" in s.text() and "STILL RUNNING 0" in s.text(), "the wake names both jobs", s.text())

    # --- a refused job relaunched by a process no hook saw: the call's later inventory names the label's
    # next attempt, so the watcher follows it (running, then done) instead of reporting the refusal
    s = Session("relaunch")
    fan = s.root / "fan"
    fan.mkdir()
    r1, r2 = fan / "regate.record.json", fan / "regate.attempt2.record.json"
    inv1, inv2 = fan / "fanout-first.inventory.json", fan / "fanout-relaunch.inventory.json"
    issued = time.time() - 60
    write_inventory(inv1, [("regate", r1)], os.getpid(), written=inventory_stamp(issued + 1))
    r1.write_text("")
    Path(str(r1) + ".exit").write_text("12")
    ledger = s.root / "ledger.jsonl"
    ledger.write_text(json.dumps({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(issued + 2)),
                                  "label": "regate", "exitCode": 12, "outcome": "ended-before-result",
                                  "parentSessionId": SID, "reason": "launch refused: not enough free memory"}) + "\n")
    write_inventory(inv2, [("regate", r2)], os.getpid(), written=inventory_stamp(issued + 30))
    Path(str(r2) + ".pid").write_text(line(os.getpid()) + "\n")
    put_state(s.root, [dict(entry("regate", r1, fan, inv1), issued_at=issued)])
    check(arm_observed(s, "polls >= 2", {"SIDECAR_LEDGER_PATH": str(ledger)}), "the watcher finished its first poll", s.text())
    check(wait_polls(s.root / "state", 3, proc=s.proc), "the watcher stayed through a poll after the first")
    check(s.proc.poll() is None and "ENDED" not in s.text() and "NONE" not in s.text(),
          "a refused job whose relaunch runs wakes nobody", s.text())
    r2.write_text("{}")
    Path(str(r2) + ".exit").write_text("0")
    fanout_exits(inv2, [("regate", r2)])
    check(s.exits() and "DONE regate exit=0" in s.text() and "exit=12" not in s.text(),
          "the relaunch's end reports the relaunch, not the refusal", s.text())

    # --- a fan-out rewrites its inventory while it runs (save_inventory): the later mtime must not move a
    # job's attempt start past the finish row of its own end, which would report the end as a clean DONE
    # and drop its reason.
    s = Session("rewrite")
    s.extra = {"WATCH_RECORDS_POLL_SEC": "1"}
    fan = s.root / "fan"
    fan.mkdir()
    w1, w2 = fan / "w1.record.json", fan / "w2.record.json"
    inv = fan / "fanout-proof.inventory.json"
    issued = time.time() - 60
    write_inventory(inv, [("w1", w1), ("w2", w2)], os.getpid(), written=inventory_stamp(issued + 1))
    ledger = s.root / "ledger.jsonl"
    ledger.write_text(json.dumps({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(issued + 5)),
                                  "label": "w1", "exitCode": 12, "outcome": "ended-before-result",
                                  "parentSessionId": SID, "reason": "launch refused: not enough free memory"}) + "\n")
    for record in (w1, w2):
        Path(str(record) + ".pid").write_text(line(os.getpid()) + "\n")
    put_state(s.root, [dict(entry("w1", w1, fan, inv), issued_at=issued),
                       dict(entry("w2", w2, fan, inv), issued_at=issued)])
    check(arm_observed(s, "polls >= 2", {"SIDECAR_LEDGER_PATH": str(ledger)}),
          "the watcher finished its first poll", s.text())
    w1.write_text("{}")
    Path(str(w1) + ".exit").write_text("12")
    fanout_exits(inv, [("w1", w1), ("w2", w2)])
    check(s.exits() and "ENDED w1 exit=12 launch refused: not enough free memory" in s.text(),
          "a job's end stays its own after its inventory is saved again", s.text())

    # --- a check-in: work still running when the check-in interval passes wakes the session with each
    # running job's age and last write, so the orchestrator reads progress while its prompt cache is warm
    s = Session("checkin")
    fan = s.root / "fan"
    fan.mkdir()
    c1 = fan / "c1.record.json"
    inv = fan / "fanout-proof.inventory.json"
    write_inventory(inv, [("c1", c1)], os.getpid())
    Path(str(c1) + ".pid").write_text(line(os.getpid()) + "\n")
    stream = fan / "c1.stream"
    stream.write_text("{}\n")
    old = time.time() - 1500
    os.utime(stream, (old, old))
    put_state(s.root, [entry("c1", c1, fan, inv)])
    s.arm({"WATCH_RECORDS_CHECKIN_SEC": "1"})
    check(s.exits() and s.proc.returncode == 0, "running work wakes the session at the check-in", s.text())
    check("CHECKIN" in s.text() and "STILL RUNNING 1" in s.text() and "c1" in s.text()
          and "last write 25 min ago" in s.text(), "the check-in names each running job's last write", s.text())
    s = Session("checkin-quiet")
    fan = s.root / "fan"
    fan.mkdir()
    q1 = fan / "q1.record.json"
    inv = fan / "fanout-proof.inventory.json"
    write_inventory(inv, [("q1", q1)], os.getpid())
    Path(str(q1) + ".pid").write_text(line(os.getpid()) + "\n")
    put_state(s.root, [entry("q1", q1, fan, inv)])
    s.arm()
    check(polled(s.root / "state", polls=2, proc=s.proc), "the watcher polled before the default check-in")
    check(s.proc.poll() is None and "CHECKIN" not in s.text(), "before the default check-in nothing wakes", s.text())
    s.proc.kill()

    # --- a non-zero exit wakes immediately, with the sibling still running
    s = Session("nonzero")
    fan = s.root / "fan"
    fan.mkdir()
    b1, b2 = fan / "b1.record.json", fan / "b2.record.json"
    inv = fan / "fanout-proof.inventory.json"
    write_inventory(inv, [("b1", b1), ("b2", b2)], os.getpid())
    for record in (b1, b2):
        Path(str(record) + ".pid").write_text(line(os.getpid()) + "\n")
    put_state(s.root, [entry("b1", b1, fan, inv), entry("b2", b2, fan, inv)])
    s.arm()
    check(polled_after(s.root / "state", lambda: Path(str(b1) + ".exit").write_text("3"), polls=1, proc=s.proc),
          "the watcher polled after the failure landed")
    check(s.exits() and "DONE b1 exit=3" in s.text() and "STILL RUNNING 1" in s.text(),
          "a non-zero exit wakes with the sibling still running", s.text())

    # --- a parallel pair that shares a wakeWith group wakes once, after both
    s = Session("parallel")
    # Both ends land inside one poll: the pair's wake is the contract here, and the 1 s poll interval
    # gives the fixture a window that is not a sleep.
    s.extra = {"WATCH_RECORDS_POLL_SEC": "1"}
    fan = s.root / "fan"
    fan.mkdir()
    c1, c2 = fan / "c1.record.json", fan / "c2.record.json"
    inv = fan / "fanout-proof.inventory.json"
    write_inventory(inv, [("c1", c1, "pair"), ("c2", c2, "pair")], os.getpid())
    for record in (c1, c2):
        Path(str(record) + ".pid").write_text(line(os.getpid()) + "\n")
    put_state(s.root, [entry("c1", c1, fan, inv), entry("c2", c2, fan, inv)])
    s.arm()
    # Both ends are written between polls (the 1 s poll interval leaves the window after a mark), so the pair
    # is judged in one wake. A group-held end waking nobody is the grouped case in test_watch_records_fanout_end.
    check(wait_polls(s.root / "state", 2, proc=s.proc), "the watcher finished two polls")

    def end_pair():
        Path(str(c1) + ".exit").write_text("0")
        Path(str(c2) + ".exit").write_text("0")
        fanout_exits(inv, [("c1", c1, "pair"), ("c2", c2, "pair")])

    end_pair()
    check(s.exits() and s.text().count("DONE") == 2 and "STILL RUNNING 0" in s.text(),
          "a parallel pair wakes once after both", s.text())

    # --- a launcher pid dying without an exit file wakes with DIED
    s = Session("died")
    child = sleeper()
    d1 = s.root / "d1.record.json"
    d1.write_text("{}")
    Path(str(d1) + ".pid").write_text(line(child.pid) + "\n")
    put_state(s.root, [entry("d1", d1)])
    s.arm()
    check(polled(s.root / "state", polls=2, proc=s.proc), "the watcher polled a live launcher pid")
    check(s.proc.poll() is None, "a live launcher pid keeps the wake armed", s.text())
    killed(child)
    check(until(lambda: "DIED d1" in s.text()) and s.exits(), "a launcher dying without .exit wakes", s.text())

    # --- an unseen standing stall wakes once; acknowledgment suppresses the re-arm
    s = Session("stall")
    fan = s.root / "fan"
    fan.mkdir()
    e1, e2 = fan / "e1.record.json", fan / "e2.record.json"
    inv = fan / "fanout-proof.inventory.json"
    write_inventory(inv, [("e1", e1), ("e2", e2)], os.getpid())
    for record in (e1, e2):
        Path(str(record) + ".pid").write_text(line(os.getpid()) + "\n")
    journal_row(e1, event="stalled", idle=900)
    put_state(s.root, [entry("e1", e1, fan, inv), entry("e2", e2, fan, inv)])
    s.arm()
    check(s.exits() and "STALLED e1 idle=900" in s.text() and "STILL RUNNING 2" in s.text(),
          "an unseen stall standing at arm wakes once", s.text())
    s.arm()
    check(until(lambda: "STALLED e1 idle=900" in s.text()), "an acknowledged stall prints at re-arm", s.text())
    check(wait_polls(s.root / "state", 2, proc=s.proc), "the re-armed watcher finished its second poll")
    check(s.proc.poll() is None and "STILL RUNNING" not in s.text(), "an acknowledged standing stall wakes nobody", s.text())
    journal_row(e2, event="stalled", idle=1200)
    check(s.exits() and "STALLED e2 idle=1200" in s.text() and "STILL RUNNING 2" in s.text(),
          "a fresh stall row wakes the session", s.text())

    # --- a usage-limit row is context: it prints at the next wake and never wakes by itself
    s = Session("usage")
    fan = s.root / "fan"
    fan.mkdir()
    u1 = fan / "u1.record.json"
    inv = fan / "fanout-proof.inventory.json"
    write_inventory(inv, [("u1", u1)], os.getpid())
    Path(str(u1) + ".pid").write_text(line(os.getpid()) + "\n")
    journal_row(u1, event="usage-limit", ts="2026-09-30T10:00:00Z", transport="codex", retries=1)
    put_state(s.root, [entry("u1", u1, fan, inv)])
    check(arm_observed(s, "polls >= 2"), "the watcher finished its first poll", s.text())
    check(wait_polls(s.root / "state", 3, proc=s.proc), "the watcher stayed through a poll after the first")
    check(s.proc.poll() is None, "a usage marker alone wakes nobody", s.text())
    Path(str(u1) + ".exit").write_text("1")
    check(s.exits() and "USAGE-LIMIT u1 codex resets unknown" in s.text() and "DONE u1 exit=1" in s.text(),
          "the job's DONE wakes and carries the usage reading as context", s.text())

    # --- a run whose stream ended on a result long ago while its launcher still runs wakes with LINGERING.
    # The result line is cut from a live claude -p stream whose child outlived its result, trimmed.
    result_line = json.dumps({"type": "result", "subtype": "success", "is_error": True, "num_turns": 103,
                              "result": "Credit balance is too low", "stop_reason": "stop_sequence",
                              "session_id": "d40986a3", "terminal_reason": "api_error", "api_error_status": 400})
    s = Session("linger")
    fan = s.root / "fan"
    fan.mkdir()
    l1, l2, l3 = fan / "l1.record.json", fan / "l2.record.json", fan / "l3.record.json"
    inv = fan / "fanout-proof.inventory.json"
    write_inventory(inv, [("l1", l1), ("l2", l2), ("l3", l3)], os.getpid())
    now = time.time()

    def plant_stream(path, age, task_after=False):
        rows = [json.dumps({"type": "assistant", "session_id": "d40986a3"}), result_line]
        if task_after:  # claude -p's background-task bookkeeping after the result is not a new turn
            rows.append(json.dumps({"type": "system", "subtype": "task_updated", "task_id": "b1"}))
        Path(path).write_text("\n".join(rows) + "\n", encoding="utf-8")
        os.utime(path, (now - age, now - age))

    for record, pid_age in ((l1, 900), (l2, 300), (l3, 900)):
        Path(str(record) + ".pid").write_text(line(os.getpid()) + "\n")
        os.utime(str(record) + ".pid", (now - pid_age, now - pid_age))
    plant_stream(fan / "l1.stream", 600, task_after=True)  # the fan-out's -P <label>.stream
    plant_stream(fan / "l2.stream", 600)  # older than l2's .pid: an earlier attempt's ending, not this run's
    plant_stream(str(l3) + ".stream", 5)  # the -R default <record>.stream, its result fresh
    put_state(s.root, [entry(n, r, fan, inv) for n, r in (("l1", l1), ("l2", l2), ("l3", l3))])
    s.arm()
    check(s.exits() and "LINGERING l1" in s.text() and "STILL RUNNING 3" in s.text(),
          "a child alive long past its stream's result wakes the session", s.text())
    check("LINGERING l2" not in s.text() and "LINGERING l3" not in s.text(),
          "an earlier attempt's result and a fresh result wake nobody", s.text())
    s.arm()
    check(until(lambda: "LINGERING l1" in s.text()), "an acknowledged lingering run prints at re-arm", s.text())
    check(wait_polls(s.root / "state", 2, proc=s.proc), "the re-armed watcher finished its second poll")
    check(s.proc.poll() is None, "an acknowledged lingering run wakes nobody", s.text())
    os.utime(str(l3) + ".stream", (now - 600, now - 600))
    check(s.exits() and "LINGERING l3" in s.text(),
          "a <record>.stream result that goes stale while the launcher lives wakes the session", s.text())

    # --- a fan-out process dying with a job still queued wakes with ORPHANED
    s = Session("orphan")
    fan = s.root / "fan"
    fan.mkdir()
    fanout = sleeper()
    f1, f2 = fan / "f1.record.json", fan / "f2.record.json"
    write_inventory(fan / "fanout-proof.inventory.json", [("f1", f1), ("f2", f2)], fanout.pid)
    Path(str(f1) + ".pid").write_text(line(os.getpid()) + "\n")
    put_state(s.root, [entry("f1", f1, fan, fan / "fanout-proof.inventory.json")])
    s.arm()
    check(polled(s.root / "state", polls=2, proc=s.proc), "the watcher polled a live fan-out process")
    check(s.proc.poll() is None, "a live fan-out process keeps the unit running", s.text())
    killed(fanout)
    check(until(lambda: "ORPHANED" in s.text()) and s.exits(),
          "a dead fan-out with a queued job wakes", s.text())
    check("ORPHANED f2" in s.text() and "STILL RUNNING 1" in s.text(),
          "ORPHANED names the job that will never start", s.text())

    # --- nothing left to wait for: ended lines then NONE
    s = Session("none")
    n1 = s.root / "n1.record.json"
    n1.write_text("{}")
    Path(str(n1) + ".exit").write_text("0")
    put_state(s.root, [entry("n1", n1)])
    s.arm()
    check(s.exits() and s.proc.returncode == 0 and "DONE n1 exit=0" in s.text()
          and "STILL RUNNING 0" in s.text(), "an unseen completed unit wakes at arm", s.text())
    s.arm(flags=NO_GRACE)
    check(exits_idle(s) and s.text().strip() == "NONE",
          "an acknowledged ended unit leaves the session watcher nothing to watch: it exits NONE", s.text())

    s = Session("empty")
    put_state(s.root, [])
    s.arm(flags=NO_GRACE)
    check(exits_idle(s) and s.text().strip() == "NONE",
          "no pending entry: the session watcher exits NONE", s.text())
    # --- --session without a session id is a usage error, never NONE
    unset = dict(os.environ, HARNESS_HOOK_STATE_DIR=str(base / "unused"))
    unset.pop("CLAUDE_CODE_SESSION_ID", None)
    noid = subprocess.run([sys.executable, str(WATCH), "--session", "--wake"], capture_output=True,
                          text=True, timeout=30, env=unset)
    check(noid.returncode == 2 and "NONE" not in noid.stdout and "CLAUDE_CODE_SESSION_ID" in noid.stderr,
          "--session without a session id exits 2", "rc=%s out=%r err=%r"
          % (noid.returncode, noid.stdout, noid.stderr))
    half = subprocess.run([sys.executable, str(WATCH), "--session"], capture_output=True,
                          text=True, timeout=30, env=dict(unset, CLAUDE_CODE_SESSION_ID=SID))
    check(half.returncode == 2 and "ALL REPORTED" not in half.stdout and "NONE" not in half.stdout,
          "--session without --wake exits 2 rather than reporting nothing to watch",
          "rc=%s out=%r" % (half.returncode, half.stdout))

    # --- an unknown flag is a usage error, never a record path to watch forever
    for flag in ("-h", "--help", "--wakee"):
        bad = subprocess.run([sys.executable, str(WATCH), flag], capture_output=True, text=True, timeout=30,
                             env=dict(unset, CLAUDE_CODE_SESSION_ID=SID))
        check(bad.returncode == 2 and "Usage" in bad.stderr, "%s exits 2 with the usage line" % flag,
              "rc=%s out=%r err=%r" % (bad.returncode, bad.stdout, bad.stderr))

    # --- a live anthropic-secondary seat (the launcher sidecar_fanout maps to `anthropic_secondary`) stays watched
    s = Session("anthropic_secondary")
    fan = s.root / "fan"
    fan.mkdir()
    sec = fan / "sec.record.json"
    inv = fan / "fanout-proof.inventory.json"
    write_inventory(inv, [("sec", sec)], os.getpid())
    sec_entry = entry("sec", sec, fan, inv)
    sec_entry["jobs"][0]["launcher"] = "anthropic_secondary"
    put_state(s.root, [sec_entry])
    s.arm()
    check(polled(s.root / "state", polls=2, proc=s.proc), "the watcher polled a live anthropic_secondary seat")
    check(s.proc.poll() is None and "NONE" not in s.text(),
          "a live anthropic_secondary seat keeps the session watcher armed", s.text())
    sec.write_text("{}")
    Path(str(sec) + ".exit").write_text("0")
    fanout_exits(inv, [("sec", sec)])
    check(s.exits() and "DONE sec exit=0" in s.text(), "the anthropic_secondary seat resolves when it ends", s.text())

    # --- an entry whose inventory does not exist yet stays watched, then resolves
    s = Session("late")
    fan = s.root / "fan"
    fan.mkdir()
    g1 = fan / "g1.record.json"
    inv = fan / "fanout-proof.inventory.json"
    put_state(s.root, [entry("g1", None, fan)])
    s.arm()
    check(polled(s.root / "state", polls=2, proc=s.proc), "the watcher polled an entry with no inventory yet")
    check(s.proc.poll() is None and "NONE" not in s.text(), "an entry with no inventory yet stays watched", s.text())
    write_inventory(inv, [("g1", g1)], os.getpid())
    g1.write_text("{}")
    Path(str(g1) + ".exit").write_text("0")
    fanout_exits(inv, [("g1", g1)])
    check(s.exits() and "DONE g1 exit=0" in s.text(), "the entry resolves from the inventory it waited for", s.text())

    # --- a launch that failed before allocating its record stops holding the watcher once its grace passes
    s = Session("failed_launch")
    fan = s.root / "fan"
    fan.mkdir()
    long_ago = time.time() - 3600
    put_state(s.root, [dict(entry("g2", None, fan), issued_at=long_ago, launched_at=long_ago)])
    s.arm()
    check(s.exits() and s.proc.returncode == 0 and "UNRESOLVED g2" in s.text() and "STILL RUNNING 0" in s.text(),
          "an entry unresolved past its grace is named UNRESOLVED and no longer keeps the watcher alive", s.text())

    # --- a label-only launch that ended before a result: its ledger finish row ends it, never UNRESOLVED
    s = Session("ended_launch")
    ledger = s.root / "ledger.jsonl"
    long_ago = time.time() - 3600
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(long_ago + 5))
    ledger.write_text(json.dumps({"timestamp": stamp, "parentSessionId": SID, "label": "e1", "transport": "codex",
                                  "exitCode": 12, "outcome": "ended-before-result",
                                  "reason": "REFUSED memory headroom for e1"}) + "\n")
    put_state(s.root, [dict(entry("e1", None), id="e1", issued_at=long_ago, launched_at=long_ago),
                       dict(entry("e2", None), id="e2", issued_at=time.time() - 1, launched_at=time.time())])
    s.arm({"SIDECAR_LEDGER_PATH": str(ledger)})
    check(s.exits() and "ENDED e1 exit=12" in s.text(), "an unseen ledger refusal wakes at first poll", s.text())
    s.arm({"SIDECAR_LEDGER_PATH": str(ledger)})
    check(polled(s.root / "state", polls=1, proc=s.proc), "the re-armed watcher polled")
    with ledger.open("a") as fh:
        fh.write(json.dumps({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 1)),
                             "parentSessionId": SID, "label": "e2", "transport": "codex", "exitCode": 13,
                             "outcome": "ended-before-result", "reason": "DUPLICATE LAUNCH refused (exit 13)"}) + "\n")
    check(s.exits() and "ENDED e1 exit=12 REFUSED memory headroom for e1" in s.text()
          and "ENDED e2 exit=13 DUPLICATE LAUNCH refused (exit 13)" in s.text()
          and "UNRESOLVED" not in s.text(),
          "a launch whose finish row says ended-before-result prints ENDED and wakes once, never UNRESOLVED", s.text())

    grace = ("import time; now = time.time(); "
             "mk = lambda t: w.units([{'kind': 'sidecar', 'id': 'x', 'record': None, 'fanout_dir': %r, "
             "'issued_at': t, 'launched_at': t, 'jobs': [{'label': 'g3'}]}]); "
             "lines, live, _ = w._wake(mk(now - w.UNRESOLVED_GRACE_SECONDS - 60), now); "
             "print(live, [(kind, text, triggers) for kind, _, text, triggers in lines]); "
             "lines, live, _ = w._wake(mk(now - 5), now); print(live, len(lines)); "
             "lines, live, _ = w._wake(w.units([{'kind': 'sidecar', 'id': 'y', 'record': None, "
             "'fanout_dir': %r, 'jobs': [{'label': 'g4'}]}]), now); print(live, len(lines))"
             % (str(base / "nofan"), str(base / "nofan")))
    grace_run = under_test(grace)
    grace_lines = grace_run.stdout.strip().splitlines()
    check(len(grace_lines) == 3 and grace_lines[0].startswith("0 [('unresolved', 'UNRESOLVED g3")
          and grace_lines[0].endswith(", True)]"),
          "past its grace an unresolved entry ends, and its wake line names it and wakes the session",
          grace_run.stdout + grace_run.stderr)
    check(grace_lines[1] == "1 0", "an unresolved entry within its grace still counts as live", grace_run.stdout)
    check(grace_lines[2] == "1 0", "an unresolved entry with no launch time stays live", grace_run.stdout)

    # --- a second launch into a spill dir an earlier, finished fan-out of this session used (session 202f5185):
    # its unresolved entry is its own unit, so the earlier unit's end neither hides it nor ends the watcher
    shared = base / "shared-spill"
    shared.mkdir(exist_ok=True)
    done = shared / "v1.record.json"
    done.write_text('{"result": "ok"}', encoding="utf-8")
    Path(str(done) + ".exit").write_text("0")
    reuse = ("import time; now = time.time(); "
             "es = [{'kind': 'sidecar', 'id': 'a', 'call': 'c1', 'record': %r, 'fanout_dir': %r, "
             "'issued_at': now - 900, 'launched_at': now - 900, 'jobs': [{'label': 'v1'}]}, "
             "{'kind': 'sidecar', 'id': 'b', 'call': 'c2', 'record': None, 'fanout_dir': %r, "
             "'issued_at': now - 5, 'launched_at': now - 5, 'jobs': [{'label': 'redo'}]}]; "
             "lines, live, _ = w._wake(w.units(es), now); "
             "print(live, len(w.units(es)), sorted(k for k, _, _, _ in lines))"
             % (str(done), str(shared), str(shared)))
    reuse_run = under_test(reuse)
    check(reuse_run.stdout.strip().startswith("1 2 "),
          "a new launch into a finished fan-out's spill dir is its own unit and counts live until its record lands",
          reuse_run.stdout + reuse_run.stderr)

    # --- a fan-out registered after a tool timeout (session 1f652996): `launched_at` trails the
    # inventory by 301 s and the entry carries no issue time; its call's transcript row names it
    def late_registered(s, labels, launcher_pid):
        fan = s.root / "inventory" / "out"
        fan.mkdir(parents=True)
        issued = time.time() - 900
        written, call = issued + 2.46, "toolu_timedout"
        projects = s.root / "projects" / "proj"
        projects.mkdir(parents=True)
        from datetime import datetime, timezone
        stamp = datetime.fromtimestamp(issued, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        (projects / (SID + ".jsonl")).write_text(json.dumps(
            {"type": "assistant", "timestamp": stamp, "message": {"content": [
                {"type": "tool_use", "id": call, "name": "Bash", "input": {"command": "sidecar_fanout.py"}}]}}) + "\n")
        records = [(label, fan / (label + ".record.json")) for label in labels]
        inv = fan / "fanout-d7d88dc8.inventory.json"
        write_inventory(inv, records, 99999999)
        os.utime(inv, (written, written))
        for _, record in records:
            record.write_text("{}")
            Path(str(record) + ".pid").write_text(line(launcher_pid) + "\n")
        put_state(s.root, [{"kind": "sidecar", "id": "%s:%s:%s" % (fan, label, call), "record": None,
                            "fanout_dir": str(fan), "task_id": None, "launched_at": written + 301,
                            "jobs": [{"label": label, "launcher": "proof"}]} for label in labels])
        return records, {"CLAUDE_PROJECTS_ROOT": str(s.root / "projects")}

    s = Session("timedout_ended")
    records, extra = late_registered(s, ["j1", "j2"], 99999999)
    for _, record in records:
        Path(str(record) + ".exit").write_text("1")
    s.arm(extra)
    ended = s.exits()
    if not ended:
        killed(s.proc)
    check(ended and "DONE j1 exit=1" in s.text() and "DONE j2 exit=1" in s.text() and "STILL RUNNING 0" in s.text(),
          "a late-registered fan-out whose jobs all ended is reported, not watched forever", s.text())

    s = Session("timedout_live")
    records, extra = late_registered(s, ["k1", "k2"], os.getpid())
    s.arm(extra)
    check(polled(s.root / "state", polls=2, proc=s.proc), "the watcher polled a late-registered fan-out")
    check(s.proc.poll() is None, "a late-registered fan-out with live launchers stays watched", s.text())
    Path(str(records[0][1]) + ".exit").write_text("1")
    woke = s.exits()
    if not woke:
        killed(s.proc)
    check(woke and "DONE k1 exit=1" in s.text() and "STILL RUNNING 1" in s.text(),
          "a late-registered fan-out's non-zero exit wakes the session", s.text())

    # --- a sidecar launched after arming is watched by the same watcher
    s = Session("launched")
    fan = s.root / "fan"
    fan.mkdir()
    h1 = fan / "h1.record.json"
    h2 = s.root / "h2.record.json"
    write_inventory(fan / "fanout-proof.inventory.json", [("h1", h1)], os.getpid())
    Path(str(h1) + ".pid").write_text(line(os.getpid()) + "\n")
    put_state(s.root, [entry("h1", h1, fan, fan / "fanout-proof.inventory.json")])
    s.arm()
    check(polled(s.root / "state", polls=1, proc=s.proc), "the watcher polled before the second sidecar launched")

    def launch_h2():
        h2.write_text("{}")
        Path(str(h2) + ".pid").write_text(line(os.getpid()) + "\n")
        put_state(s.root, [entry("h1", h1, fan, fan / "fanout-proof.inventory.json"), entry("h2", h2)])

    check(polled_after(s.root / "state", launch_h2, proc=s.proc),
          "the watcher polled the second sidecar as a running launch")
    Path(str(h2) + ".exit").write_text("0")
    check(until(lambda: "DONE h2 exit=0" in s.text()) and s.exits(),
          "a sidecar launched after arming is watched by the same watcher", s.text())

    # --- the watch is registered session-wide while it runs, and gone after it exits
    reg_root = Path(tempfile.mkdtemp(prefix="wake_reg_"))
    (reg_root / "state").mkdir()
    r1 = reg_root / "r1.record.json"
    r1.write_text("{}")
    Path(str(r1) + ".pid").write_text(line(os.getpid()) + "\n")
    put_state(reg_root, [entry("r1", r1)])
    reg_proc = subprocess.Popen([sys.executable, str(WATCH), "--session", "--wake"],
                                stdout=open(reg_root / "watcher.out", "w"), stderr=subprocess.PIPE,
                                text=True, env=wake_env(reg_root))
    check(until(lambda: bool(session_state(reg_root).get("sidecar_watchers"))),
          "a --session watcher registers while it runs")
    watchers = session_state(reg_root).get("sidecar_watchers") or []
    check(bool(watchers) and watchers[0].get("session") is True,
          "it registers as session-wide", watchers)
    check(under_test("print(w._process_line_gone(%r))" % watchers[0]["process"]).stdout.strip() == "False",
          "the registered identity is the live watcher")
    Path(str(r1) + ".exit").write_text("0")
    reg_proc.wait(timeout=HANG_BOUND)
    check(not session_state(reg_root).get("sidecar_watchers"), "a session watcher deregisters on exit")

    # --- coverage: a session watcher covers anything; a listed record covers its own path only
    rel_root = base / "proj"
    rel_root.mkdir()
    absolute = str(rel_root / "run.record.json")
    cover = under_test(
        "live = {'process': w.process_line(), 'records': []}; print(w._covered('run.record.json', [live])); "
        "named = {'process': w.process_line(), 'records': [r'%s']}; "
        "print(w._covered('run.record.json', [named])); "
        "print(w._covered('other.record.json', [named])); "
        "dead = {'process': '2026-01-01T00:00:00Z pid=99999999', 'records': [r'%s']}; "
        "print(w._covered('run.record.json', [dead])); "
        "session = {'process': w.process_line(), 'session': True}; "
        "print(w._covered('run.record.json', [session]))"
        % (absolute, absolute),
        env={"CLAUDE_PROJECT_DIR": str(rel_root)})
    check(cover.stdout.split() == ["False", "True", "False", "False", "True"],
          "a relative record is covered by a live watcher naming it or by a session watcher, "
          "never by a dead watcher or another record",
          "rc=%s out=%r err=%r" % (cover.returncode, cover.stdout, cover.stderr[-300:]))

    public = under_test(
        "live = {'process': w.process_line(), 'session': True}; "
        "print(w.session_watcher_live({'sidecar_watchers': [live]})); "
        "print(w.session_watcher_live({'sidecar_watchers': [{'process': w.process_line(), 'records': ['x']}]})); "
        "print(w.session_watcher_live({})); "
        "import dispatch_table as d; print(d.shown_model('zz-pin') == d._shown('zz-pin') == 'zz-pin')",
        env={"CLAUDE_PROJECT_DIR": str(rel_root)})
    check(public.stdout.split() == ["True", "False", "False", "True"],
          "session_watcher_live and shown_model are the public entries other modules call",
          "rc=%s out=%r err=%r" % (public.returncode, public.stdout, public.stderr[-300:]))

    # --- no import cycle: each module imports first in a fresh interpreter
    cycle = ("import sys; sys.path.insert(0, %r); sys.path.insert(0, %r); import %s"
             % (str(ROOT / "hooks"), str(ROOT / "tools"), "%s"))
    for module in ("watch_records", "dispatch_table", "sidecar_watch_rearm"):
        probe = subprocess.run([sys.executable, "-c", cycle % module], capture_output=True, text=True,
                               timeout=60, env=dict(os.environ, HARNESS_HOOK_STATE_DIR=str(base / "unused")))
        check(probe.returncode in (0, 2) and "Traceback" not in probe.stderr,
              "importing %s first raises no cycle" % module,
              "rc=%s err=%r" % (probe.returncode, probe.stderr))
    planted = base / "planted"
    planted.mkdir()
    source = WATCH.read_text(encoding="utf-8")
    anchor = "import _hook_state  # noqa: E402"
    check(anchor in source, "the fixture's plant anchor is still in watch_records.py")
    planted_mod = planted / "watch_records.py"
    planted_mod.write_text(re.sub(
        re.escape(anchor),
        anchor + "\nfrom dispatch_table import resolve_sidecar_record",
        source.replace("HERE = os.path.dirname(os.path.abspath(__file__))",
                       "HERE = %r" % str(ROOT / "tools"))))
    planted_probe = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, %r); sys.path.insert(0, %r); "
                               "import watch_records" % (str(ROOT / "hooks"), str(planted))],
        capture_output=True, text=True, timeout=60,
        env=dict(os.environ, HARNESS_HOOK_STATE_DIR=str(base / "unused")))
    check(planted_probe.returncode != 0 and "dispatch_table" in planted_probe.stderr,
          "the same check fails on a planted module-level import", "rc=%s err=%r"
          % (planted_probe.returncode, planted_probe.stderr[-300:]))

    # --- registration happens before the session path imports dispatch_table
    def barrier(name):
        # The copy blocks here until the test creates the release file: no wall-clock deadline decides the order.
        return ("    import os as _os, time as _t\n    while not _os.path.exists(%r) and _t.time() < %r:\n        _t.sleep(0.02)\n"
                % (str(base / name), time.time() + 600))

    delayed = base / "delayed"
    delayed.mkdir()
    (delayed / "watch_records.py").write_text(
        source.replace("HERE = os.path.dirname(os.path.abspath(__file__))", "HERE = %r" % str(ROOT / "tools"))
        .replace("    import dispatch_table\n", barrier("release1") + "    import dispatch_table\n"))
    root = Path(tempfile.mkdtemp(prefix="wake_order_"))
    (root / "state").mkdir()
    q1 = root / "q1.record.json"
    q1.write_text("{}")
    Path(str(q1) + ".pid").write_text(line(os.getpid()) + "\n")
    (root / "state" / (SID[:8] + ".json")).write_text(json.dumps({"dispatch_table_pending": [entry("q1", q1)]}))
    proc = subprocess.Popen([sys.executable, str(delayed / "watch_records.py"), "--session", "--wake"],
                            stdout=open(root / "out", "w"), stderr=subprocess.PIPE, text=True,
                            env=wake_env(root))
    check(until(lambda: bool(session_state(root).get("sidecar_watchers")), seconds=30, step=0.02),
          "registration lands before the session path's heavy import",
          "watchers=%r" % session_state(root).get("sidecar_watchers"))
    (base / "release1").write_text("go")
    Path(str(q1) + ".exit").write_text("0")
    proc.wait(timeout=HANG_BOUND)

    late_dir = base / "late_registration"
    late_dir.mkdir()
    (late_dir / "watch_records.py").write_text(
        source.replace("HERE = os.path.dirname(os.path.abspath(__file__))", "HERE = %r" % str(ROOT / "tools"))
        .replace("    path, identity, covering = registration(pending, session_wide=session_wide)",
                 barrier("release2") +
                 "    path, identity, covering = registration(pending, session_wide=session_wide)"))
    root2 = Path(tempfile.mkdtemp(prefix="wake_order2_"))
    (root2 / "state").mkdir()
    q2 = root2 / "q2.record.json"
    q2.write_text("{}")
    Path(str(q2) + ".pid").write_text(line(os.getpid()) + "\n")
    (root2 / "state" / (SID[:8] + ".json")).write_text(json.dumps({"dispatch_table_pending": [entry("q2", q2)]}))
    proc2 = subprocess.Popen([sys.executable, str(late_dir / "watch_records.py"), "--session", "--wake"],
                             stdout=open(root2 / "out", "w"), stderr=subprocess.PIPE, text=True,
                             env=wake_env(root2))
    check(not until(lambda: bool(session_state(root2).get("sidecar_watchers")), seconds=1.5, step=0.02),
          "the ordering check fails a registration planted after the import")
    (base / "release2").write_text("go")
    check(until(lambda: bool(session_state(root2).get("sidecar_watchers")), seconds=30),
          "set up: the late copy does register, once its delay has passed")
    Path(str(q2) + ".exit").write_text("0")
    proc2.wait(timeout=HANG_BOUND)

# ---------------------------------------------------------------- orphaned seat processes
# A record that ended (DIED, or DONE once its launcher is gone) or a fan-out gone, whose launch id a live process
# still carries, wakes with ORPHAN, even at arm.
import contextlib
import importlib.util
import io

spec = importlib.util.spec_from_file_location("watch_records_orphan", WATCH)
wr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wr)
real_carriers = getattr(wr, "live_carriers", None)


def with_launch(pid, launch):
    stamp, rest = line(pid).split(" ", 1)
    return "%s launch=%s %s\n" % (stamp, launch, rest)


def arm_once(source_map):
    """Run wake_loop over a fixed source; (code, stdout). A loop that does not return by its third
    poll is a watcher that would not have woken."""
    calls = []

    def source():
        calls.append(1)
        if len(calls) > 3:
            raise RuntimeError("no wake")
        return source_map

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            code = wr.wake_loop(source)
        except RuntimeError:
            code = None
    return code, out.getvalue()


with tempfile.TemporaryDirectory(prefix="watch_orphan_") as temp:
    root = Path(temp)
    carriers = {"L-live": [7001, 7002]}
    wr.live_carriers = lambda launch: carriers.get(launch, [])
    wr.POLL_SECONDS = 0.01
    seat = root / "seat.record.json"
    Path(str(seat) + ".pid").write_text(with_launch(99999999, "L-live"))
    unit = {"u": {"records": [("seat", str(seat))], "fanout_dir": None}}
    lines, _, _ = wr._wake(unit)
    orphans = [(text, trig) for kind, _, text, trig in lines if text.startswith("ORPHAN ")]
    check(orphans == [("ORPHAN seat launch=L-live pids=7001,7002", "always")],
          "a DIED record whose launch id a live process carries yields ORPHAN", repr(lines))
    code, out = arm_once(unit)
    check(code == 0 and "ORPHAN seat launch=L-live pids=7001,7002" in out and "DIED seat" in out,
          "ORPHAN wakes even when the record was already DIED at arm", repr(out))

    quiet = root / "quiet.record.json"
    Path(str(quiet) + ".pid").write_text(with_launch(99999999, "L-gone"))
    code, out = arm_once({"u": {"records": [("quiet", str(quiet))], "fanout_dir": None}})
    check(code == 0 and "ORPHAN" not in out and "STILL RUNNING 0" in out,
          "no ORPHAN when no live process carries the launch id", repr(out))

    by_json = root / "json.record.json"
    by_json.write_text(json.dumps({"launchId": "L-live"}))
    Path(str(by_json) + ".pid").write_text(line(99999999) + "\n")
    lines, _, _ = wr._wake({"u": {"records": [("json", str(by_json))], "fanout_dir": None}})
    check(any(text == "ORPHAN json launch=L-live pids=7001,7002" for _, _, text, _ in lines),
          "the launch id falls back to the record JSON", repr(lines))

    done = root / "done.record.json"
    Path(str(done) + ".pid").write_text(with_launch(99999999, "L-live"))
    Path(str(done) + ".exit").write_text("0")
    lines, _, _ = wr._wake({"u": {"records": [("done", str(done))], "fanout_dir": None}})
    check(any(text == "ORPHAN done launch=L-live pids=7001,7002" for _, _, text, _ in lines),
          "a run that ended cleanly but left a process carrying its launch id yields ORPHAN", repr(lines))

    lingering = root / "lingering.record.json"
    Path(str(lingering) + ".pid").write_text(with_launch(os.getpid(), "L-live"))
    Path(str(lingering) + ".exit").write_text("0")
    lines, _, _ = wr._wake({"u": {"records": [("lingering", str(lingering))], "fanout_dir": None}})
    check(not any(text.startswith("ORPHAN ") for _, _, text, _ in lines),
          "no ORPHAN while a finished run's launcher is still exiting", repr(lines))

    fan = root / "fan"
    fan.mkdir()
    job = fan / "j1.record.json"
    Path(str(job) + ".pid").write_text(line(99999999) + "\n")
    inv = fan / "fanout-gone.inventory.json"
    inv.write_text(json.dumps({"fanoutProcess": line(99999999).strip(),
                               "jobs": [{"label": "j1", "launchId": "L-live", "recordPath": str(job)}]}))
    lines, _, _ = wr._wake({str(fan): {"records": [], "inventories": [str(inv)], "fanout_dir": str(fan),
                                       "unresolved": [], "launched": None}})
    check(any(text == "ORPHAN j1 launch=L-live pids=7001,7002" for _, _, text, _ in lines),
          "a fan-out gone with a job's launch id still carried yields ORPHAN", repr(lines))

    folding = root / "folding"
    folding.mkdir()
    seat_done = folding / "s1.record.json"
    Path(str(seat_done) + ".exit").write_text("0")
    driver = folding / "fanout-live.inventory.json"
    driver.write_text(json.dumps({"fanoutProcess": line(os.getpid()).strip(),
                                  "jobs": [{"label": "s1", "recordPath": str(seat_done)}]}))
    folder = [dict(entry("s1", seat_done, folding, driver), writes=True)]
    lines, live, _ = wr._wake(wr.units(folder))
    check(live == 1 and not any(kind == "complete" for kind, _, _, _ in lines),
          "a writing fan-out whose seats ended while its launcher still folds stays live", repr((live, lines)))
    lines, live, _ = wr._wake(wr.units([dict(folder[0], writes=False)]))
    check(live == 1 and not any(kind == "complete" for kind, _, _, _ in lines),
          "a review-only fan-out stays live while its launcher still runs", repr((live, lines)))
    driver.write_text(json.dumps({"fanoutProcess": line(99999999).strip(),
                                  "jobs": [{"label": "s1", "recordPath": str(seat_done)}]}))
    lines, live, _ = wr._wake(wr.units(folder))
    check(live == 0 and any(kind == "complete" for kind, _, _, _ in lines),
          "the writing fan-out completes once its launcher exits", repr((live, lines)))

    # --- a review fan-out: both seats ended, its launcher then folds and checks
    review = root / "review"
    review.mkdir()
    seats = [review / "r1.record.json", review / "r2.record.json"]
    for seat in seats:
        Path(str(seat) + ".exit").write_text("0")
    review_driver = review / "fanout-review.inventory.json"

    def review_units(fanout_process):
        review_driver.write_text(json.dumps({"fanoutProcess": fanout_process,
                                             "jobs": [{"label": "r%d" % (i + 1), "recordPath": str(seat)}
                                                      for i, seat in enumerate(seats)]}))
        return wr.units([dict(entry("r%d" % (i + 1), seat, review, review_driver), writes=False)
                         for i, seat in enumerate(seats)])

    lines, live, _ = wr._wake(review_units(line(os.getpid()).strip()))
    check(live == 1 and not any(kind == "complete" for kind, _, _, _ in lines)
          and not any(trig for _, _, _, trig in lines),
          "a review fan-out whose seats ended stays live and wakes nobody while its launcher runs", repr((live, lines)))
    lines, live, _ = wr._wake(review_units(line(99999999).strip()))
    check(live == 0 and any(kind == "complete" and trig for kind, _, _, trig in lines),
          "a review fan-out whose launcher is gone still ends and wakes", repr((live, lines)))

    # --- a WAITING-MEMORY wait is context: the launcher resumes by itself
    waiting = root / "waiting.record.json"
    Path(str(waiting) + ".pid").write_text(line(os.getpid()) + "\n")
    journal_row(waiting, event="waiting-memory", message=emitted.stderr.splitlines()[0])
    lines, live, standing = wr._wake({"u": {"records": [("memory", str(waiting))], "fanout_dir": None}})
    memory = [(text, trig) for kind, _, text, trig in lines if kind == "memory"]
    check(len(memory) == 1 and memory[0][0].startswith("WAITING-MEMORY memory: ") and memory[0][1] is False
          and live == 1 and ("memory", str(waiting)) in standing,
          "a WAITING-MEMORY wait prints as context and never wakes", repr((live, lines, standing)))
    quota_wait = root / "quota_wait.record.json"
    Path(str(quota_wait) + ".pid").write_text(line(os.getpid()) + "\n")
    journal_row(quota_wait, event="waiting-quota", message="7200 window full")
    lines, live, _ = wr._wake({"u": {"records": [("q", str(quota_wait))], "fanout_dir": None}})
    texts = [text for kind, _, text, trig in lines if kind == "memory" and trig is False]
    check(texts and texts[0].startswith("WAITING-QUOTA q: ") and live == 1,
          "a quota-fit wait prints WAITING-QUOTA as context, never WAITING-MEMORY", repr((live, lines)))

    def direct(label, name, code, called=False):
        rec = root / (name + ".record.json")
        Path(str(rec) + ".exit").write_text(code)
        return {name: {"records": [(label, str(rec))], "fanout_dir": None, "unresolved": [], "launched": None,
                       "finished": [], "finish": {}, "bench": None, "called": called}}

    lines, _, _ = wr._wake(direct("daemon:ARM-T3-SELF-r1", "dm_ok", "0"))
    check(any(kind == "complete" and not trig for kind, _, _, trig in lines)
          and not any(trig for _, _, _, trig in lines),
          "a clean scoring-daemon child ending wakes nobody: its study reports its own end", repr(lines))
    lines, _, _ = wr._wake(direct("daemon:ARM-T3-SELF-r1", "dm_bad", "1"))
    check(any(kind == "end" and trig for kind, _, _, trig in lines),
          "a failed scoring-daemon child still wakes", repr(lines))
    # Exit 12 is the launcher refusing memory admission before anything ran; a background child's caller (the
    # scoring daemon, the grid) retries it, so the refusal is context, never a wake.
    lines, _, _ = wr._wake(direct("daemon:ARM-T3-SELF-r1", "dm_mem", "12"))
    check(any(kind == "end" for kind, _, _, _ in lines) and not any(trig for _, _, _, trig in lines),
          "a scoring-daemon child refused at the memory floor is context, not a wake", repr(lines))
    # Exit 15 is the launcher refusing to re-pay finished work; the scoring daemon holds that cell and reports it.
    lines, _, _ = wr._wake(direct("daemon:ARM-T3-SELF-r1", "dm_repay", "15"))
    check(any(kind == "end" for kind, _, _, _ in lines) and not any(trig for _, _, _, trig in lines),
          "a scoring-daemon child refused at the re-pay check is context, not a wake", repr(lines))
    # Exit 10 is a plan-capacity stop (dispatch floor, spent allowance, usage limit); the scoring daemon's quota
    # wait sleeps to the reset and retries the cell, so each child's refusal is context while the window is out.
    lines, _, _ = wr._wake(direct("daemon:ARM-T3-SELF-r1", "dm_quota", "10"))
    check(any(kind == "end" for kind, _, _, _ in lines) and not any(trig for _, _, _, trig in lines),
          "a scoring-daemon child refused at its plan's capacity floor is context, not a wake", repr(lines))
    lines, _, _ = wr._wake(direct("plain-quota", "plain_quota", "10", called=True))
    check(any(kind == "end" and trig for kind, _, _, trig in lines),
          "a capacity refusal of this session's own call still wakes", repr(lines))
    # A finish row no pending entry carries came from a launch this session never issued (a scoring daemon's seat
    # inherits the session id); its admission refusal is retried by that caller, so it is never an unreported end.
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    refused = {"timestamp": stamp, "parentSessionId": SID, "label": "daemon:ARM-T1D-X-r6", "exitCode": 12,
               "outcome": "ended-before-result", "reason": "REFUSED memory headroom for daemon:ARM-T1D-X-r6"}
    crashed = dict(refused, label="daemon:ARM-T1D-X-r7", exitCode=1, reason="the launcher exited 1 before a result")
    repaid = dict(refused, label="daemon:ARM-T1D-X-r8", exitCode=15, costUSD=0,
                  reason="[sidecar] REFUSED: the re-pay check found this work finished and paid")
    floored = dict(refused, label="daemon:ARM-T1D-X-r9", exitCode=10, costUSD=0,
                   reason="the launcher exited 10 before a result")
    ends = wr.unreported_ends([], [refused, crashed, repaid, floored], frozenset(), 3600)
    check([e["jobs"][0]["label"] for e in ends] == ["daemon:ARM-T1D-X-r7"],
          "an uncarried admission, re-pay or capacity refusal is no unreported end; an uncarried crash still is",
          repr(ends))
    # A queued job refused before spawn while its transport's usage-limit marker was live (zero cost, ended before
    # a result) repeats the job that hit the limit, which spent and woke; each repeat is context. A spent end, a
    # transport with no marker, and a marker that expired before the row still wake.
    exhausted = root / "exhausted"
    exhausted.mkdir(exist_ok=True)
    os.environ["SIDECAR_EXHAUSTED_DIR"] = str(exhausted)
    (exhausted / "codex.json").write_text(json.dumps({"expiresAt": time.time() + 600}))
    (exhausted / "go.json").write_text(json.dumps({"expiresAt": time.time() - 600}))
    held = root / "held.record.json"
    Path(str(held) + ".pid").write_text(line(os.getpid()) + "\n")

    def queued_end(label, transport, cost):
        row = {"timestamp": stamp, "parentSessionId": SID, "label": label, "transport": transport, "exitCode": 2,
               "outcome": "ended-before-result", "reason": "the launcher exited 2 before a result", "costUSD": cost}
        return {"fq": {"records": [("held", str(held))], "fanout_dir": str(root), "unresolved": [], "launched": None,
                       "finished": [(label, row)], "finish": {}, "bench": None, "called": True}}

    def end_trigger(grouped):
        return [trig for kind, _, _, trig in wr._wake(grouped)[0] if kind == "end"]

    check(end_trigger(queued_end("so-q1", "codex", 0)) == [False],
          "a zero-cost refusal while its transport's usage-limit marker is live is context, not a wake",
          repr(wr._wake(queued_end("so-q1", "codex", 0))[0]))
    # The same end reached through its record (a pending entry still listing the job, its `.exit` written).
    refused_rec = root / "so_q3.record.json"
    Path(str(refused_rec) + ".exit").write_text("2")
    q3 = queued_end("so-q3", "codex", 0)["fq"]["finished"][0][1]
    by_record = {"fr": {"records": [("so-q3", str(refused_rec)), ("held", str(held))], "fanout_dir": str(root),
                        "unresolved": [], "launched": None, "finished": [], "bench": None, "called": True,
                        "finish": {wr._norm_record(str(refused_rec)): q3}}}
    check(end_trigger(by_record) == [False],
          "the same refusal read through its record is context, not a wake", repr(wr._wake(by_record)[0]))
    check(end_trigger(queued_end("so-q2", "codex", 0.42)) == [True],
          "an end that spent on an exhausted transport still wakes")
    check(end_trigger(queued_end("ds-q1", "deepseek", 0)) == [True],
          "a zero-cost refusal on a transport with no marker still wakes")
    check(end_trigger(queued_end("go-q1", "go", 0)) == [True],
          "a zero-cost refusal after its transport's marker expired still wakes")
    os.environ["SIDECAR_EXHAUSTED_DIR"] = NO_MARKERS
    lines, _, _ = wr._wake(direct("plain-mem", "plain_mem", "12", called=True))
    check(any(kind == "end" and trig for kind, _, _, trig in lines),
          "a memory refusal of this session's own call still wakes", repr(lines))
    lines, _, _ = wr._wake(direct("plain-job", "plain_ok", "0", called=True))
    check(any(kind == "complete" and trig for kind, _, _, trig in lines),
          "a solo launch this session's own call issued, ending cleanly, still wakes", repr(lines))
    lines, _, _ = wr._wake(direct("spike:A", "spike_ok", "0"))
    check(any(kind == "complete" and not trig for kind, _, _, trig in lines)
          and not any(trig for _, _, _, trig in lines),
          "a clean solo child no call of this session issued (a delegate's or study's own launch) wakes nobody", repr(lines))
    lines, _, _ = wr._wake(direct("spike:A", "spike_bad", "1"))
    check(any(kind == "end" and trig for kind, _, _, trig in lines),
          "a failed child nobody here issued still wakes", repr(lines))
    grouped = wr.units([{"id": "i1", "record": str(root / "u1.record.json"), "fanout_dir": None,
                         "jobs": [{"label": "direct-job"}], "call": "toolu_x"},
                        {"id": "i2", "record": str(root / "u2.record.json"), "fanout_dir": None,
                         "jobs": [{"label": "spike:B"}]}])
    check(sorted(u["called"] for u in grouped.values()) == [False, True],
          "units() marks a unit called when its entry carries the issuing tool call", repr(grouped))

    sys.path.insert(0, str(ROOT / "tools"))
    import reap  # noqa: E402

    def block(launch):
        return b"PATH=/usr/bin\0SIDECAR_LAUNCH_ID=" + launch.encode() + b"\0"

    planted = {7101: ("claude.exe", r"C:\x\claude.exe -p --model m", "L9"),
               7102: ("python3.exe", r"C:\Python\python3.exe C:/x/.claude/scripts/lib/text_only_shim.py --listen 1", "L9"),
               7103: ("claude-code-proxy-0.1.42.exe", r"C:\x\claude-code-proxy-0.1.42.exe serve --port 2", "L9"),
               7104: ("claude.exe", r"C:\x\claude.exe -p --model m", "L8")}
    reap.process_table = lambda: {pid: {"pid": pid, "ppid": 1, "name": name, "cmd": cmd, "start": ""}
                                  for pid, (name, cmd, _) in planted.items()}
    reap.launch_environ = lambda pid: block(planted[pid][2]) if pid in planted else b""
    got = real_carriers("L9") if real_carriers else None
    check(got == [7101], "a live carrier is any process with the id except a dispatch server", repr(got))

# --- a stall during a turn the provider is still reasoning says so: the stream is silent, the proxy's traffic is not
with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    rec = root / "slow.record.json"
    (root / "slow.stream").write_text(json.dumps({"type": "system", "subtype": "init", "session_id": "sess-slow"}) + "\n")
    traffic = root / "traffic"
    events = traffic / "sess-slow" / "000007-codex-x" / "events"
    events.mkdir(parents=True)
    (events / "0001.json").write_text("{}")
    now = time.time()
    os.utime(events / "0001.json", (now - 30, now - 30))
    # no `.pid` yet: the launch has not written it, so the stream may be an earlier attempt's
    check(wr.upstream_age(str(rec), traffic_root=str(traffic), now=time.time()) is None,
          "a stream with no `.pid` beside it has no upstream age: the launch has not been recorded")
    check(wr.result_lingering(str(rec)) is None, "a stream with no `.pid` beside it is not lingering either")
    pid_stamp = time.time() - 600
    Path(str(rec) + ".pid").write_text("1")
    os.utime(str(rec) + ".pid", (pid_stamp, pid_stamp))
    age = wr.upstream_age(str(rec), traffic_root=str(traffic), now=now)
    check(age is not None and 29 <= age <= 31, "the upstream age reads the newest traffic file of the record's session", repr(age))
    check("still reasoning" in wr.stall_text("slow", "907", age), "a stall with fresh upstream traffic says the turn is still reasoning",
          wr.stall_text("slow", "907", age))
    os.utime(events / "0001.json", (now - 3600, now - 3600))
    stale = wr.upstream_age(str(rec), traffic_root=str(traffic), now=now)
    check(wr.stall_text("slow", "907", stale) == "STALLED slow idle=907", "a stall with stale upstream traffic stays a plain stall",
          wr.stall_text("slow", "907", stale))
    check(wr.upstream_age(str(root / "none.record.json"), traffic_root=str(traffic), now=now) is None,
          "a record with no stream has no upstream age")
    wr.upstream_age(str(rec), now=now)
    before = len(sys.path)
    wr.upstream_age(str(rec), now=now)
    check(len(sys.path) == before, "the default traffic root is resolved without growing sys.path per call",
          "%d -> %d" % (before, len(sys.path)))

    # a fan-out's attempts share one stream: the age follows the latest attempt's session, not the first's
    (root / "multi.stream").write_text("".join(json.dumps(row) + chr(10) for row in (
        {"type": "system", "subtype": "init", "session_id": "sess-first"},
        {"type": "result", "subtype": "success", "session_id": "sess-first"},
        {"type": "system", "subtype": "init", "session_id": "sess-second"},
        {"type": "assistant", "session_id": "sess-second"})))
    for name, ago in (("sess-first", 3600), ("sess-second", 30)):
        stamp = traffic / name / "000001-codex-x" / "events"
        stamp.mkdir(parents=True)
        (stamp / "0001.json").write_text("{}")
        os.utime(stamp / "0001.json", (now - ago, now - ago))
    multi_pid = root / "multi.attempt2.record.json.pid"
    multi_pid.write_text("1")
    os.utime(multi_pid, (pid_stamp, pid_stamp))
    age = wr.upstream_age(str(root / "multi.attempt2.record.json"), traffic_root=str(traffic), now=now)
    check(age is not None and 29 <= age <= 31, "a later attempt on a shared stream reads its own session's upstream traffic",
          repr(age))
    os.utime(multi_pid, (now + 60, now + 60))
    check(wr.upstream_age(str(root / "multi.attempt2.record.json"), traffic_root=str(traffic), now=now) is None,
          "a stream not written since this run's launch has no upstream age: its session is an earlier attempt's")

# --- a running Workflow run is in the session watcher's live set, ends as NONE, and never stacks a second timer
def workflow_entry(session_dir, run_id, jobs=None):
    return {"kind": "workflow", "id": run_id, "task_id": "t-" + run_id, "launched_at": time.time() - 125,
            "name": "proofflow", "run_dir": str(session_dir / "subagents" / "workflows" / run_id),
            "reported": [], "declared": {}, "jobs": jobs or []}


def plant_run(session_dir, run_id, rows):
    run_dir = session_dir / "subagents" / "workflows" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "journal.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def host_record(session_dir, run_id, status):
    (session_dir / "workflows").mkdir(parents=True, exist_ok=True)
    (session_dir / "workflows" / (run_id + ".json")).write_text(json.dumps({"runId": run_id, "status": status}))


pins = [{"label": "J-A", "model": "sonnet", "effort": "medium"}, {"label": "J-B", "model": "sonnet", "effort": "medium"},
        {"label": "J-C", "model": "opus", "effort": "high"}]
journal = [{"type": "launched"},
           {"type": "started", "key": "k1", "agentId": "a1", "label": "J-A", "phase": "P"},
           {"type": "started", "key": "k2", "agentId": "a2", "label": "J-B", "phase": "P"},
           {"type": "result", "key": "k1", "agentId": "a1", "result": "ok"}]

s = Session("wf_checkin")
sess = s.root / "sess"
plant_run(sess, "wf_one", journal)
put_state(s.root, [workflow_entry(sess, "wf_one", pins)])
s.arm({"WATCH_RECORDS_CHECKIN_SEC": "1"})
check(s.exits(), "a running Workflow run's check-in wakes the session and the watcher exits", s.text())
check(s.proc.returncode == 0 and "CHECKIN" in s.text() and "STILL RUNNING 1" in s.text(),
      "the Workflow check-in prints CHECKIN and STILL RUNNING", s.text())
check("RUNNING workflow proofflow wf_one" in s.text() and "agents 1 of 3 done" in s.text()
      and "sonnet" in s.text() and "×2" in s.text() and "min" in s.text(),
      "the Workflow check-in line names the run, its pins, its age and its progress", s.text())

s = Session("wf_nojobs")
sess = s.root / "sess"
plant_run(sess, "wf_two", journal)
put_state(s.root, [workflow_entry(sess, "wf_two")])
s.arm({"WATCH_RECORDS_CHECKIN_SEC": "1"})
check(s.exits() and "agents 1 done" in s.text() and " of " not in s.text().split("agents")[1].split("\n")[0],
      "an entry that lists no jobs prints agents <k> done without of <m>", s.text())

s = Session("wf_done")
sess = s.root / "sess"
plant_run(sess, "wf_three", journal)
host_record(sess, "wf_three", "completed")
put_state(s.root, [workflow_entry(sess, "wf_three", pins)])
s.arm({"WATCH_RECORDS_CHECKIN_SEC": "1"}, flags=NO_GRACE)
check(until(lambda: s.text().startswith("DONE workflow proofflow wf_three status=completed")) and exits_idle(s)
      and "CHECKIN" not in s.text(),
      "a Workflow run with a finished host record prints its DONE digest at arm, then the watcher exits NONE", s.text())

s = Session("wf_ends")
sess = s.root / "sess"
plant_run(sess, "wf_four", journal)
holder = sleeper()
dead = s.root / "dead.record.json"
dead.write_text("{}")
Path(str(dead) + ".pid").write_text(line(holder.pid) + "\n")
put_state(s.root, [workflow_entry(sess, "wf_four", pins), entry("dead", dead)])
check(arm_observed(s, "polls >= 1"), "the watcher finished its first poll")
check(wait_polls(s.root / "state", 2, proc=s.proc), "the watcher stayed through a second poll")
check(s.proc.poll() is None and s.text() == "",
      "a running Workflow run alone keeps the watcher armed and silent", s.text())
check(polled_after(s.root / "state", lambda: host_record(sess, "wf_four", "completed"), proc=s.proc),
      "the watcher polled after the Workflow run's host record ended")
check(s.proc.poll() is None and s.text() == "",
      "the Workflow run's end wakes nobody: the session watcher stays armed", s.text())
killed(holder)
check(until(lambda: s.proc.poll() is not None) and "DIED dead" in s.text(),
      "a decision event wakes the watcher", s.text())
check(s.text().count("DONE workflow proofflow wf_four status=completed") == 1,
      "the ended Workflow run's DONE digest prints once, at the next wake", s.text())

s = Session("wf_covered")
sess = s.root / "sess"
plant_run(sess, "wf_five", journal)
put_state(s.root, [workflow_entry(sess, "wf_five", pins)])
holder = sleeper()
state_path = s.root / "state" / (SID[:8] + ".json")
planted = json.loads(state_path.read_text())
planted["sidecar_watchers"] = [{"process": line(holder.pid), "records": [], "session": True}]
state_path.write_text(json.dumps(planted))
s.arm({"WATCH_RECORDS_CHECKIN_SEC": "1"})
until(lambda: s.proc.poll() is not None, seconds=0.6)
check(s.proc.poll() is None and s.text() == "",
      "a session watcher armed beside a live one waits to take over: no exit, no COVERED, nothing printed", s.text())
check(len(session_state(s.root).get("sidecar_watchers") or []) == 1, "a waiting arm registers nothing")
probe = subprocess.run([sys.executable, str(WATCH), "--session", "--covered"], capture_output=True, text=True,
                       timeout=30, env=wake_env(s.root, None))
check(probe.returncode == 2 and "Usage" in probe.stderr and probe.stdout == "",
      "the retired --covered is a usage error beside a live watcher: exit 2, nothing printed", repr(probe.stdout))
check("--session --wake" in probe.stderr and "retired" in probe.stderr,
      "the retired --covered usage error names the replacement", probe.stderr)
killed(holder)
until(lambda: holder.poll() is not None)
check(until(lambda: [w.get("process") for w in session_state(s.root).get("sidecar_watchers") or []]
            not in ([], [line(holder.pid)])) and s.proc.poll() is None,
      "the waiting arm takes over once the live watcher is gone, and keeps running", str(session_state(s.root)))
killed(s.proc)

s = Session("wf_stale_cover")
sess = s.root / "sess"
plant_run(sess, "wf_six", journal)
put_state(s.root, [workflow_entry(sess, "wf_six", pins)])
state_path = s.root / "state" / (SID[:8] + ".json")
planted = json.loads(state_path.read_text())
planted["sidecar_watchers"] = [{"process": line(99999999), "records": [], "session": True}]
state_path.write_text(json.dumps(planted))
s.arm({"WATCH_RECORDS_CHECKIN_SEC": "1"})
check(s.exits() and "CHECKIN" in s.text() and "COVERED" not in s.text(), "a dead session watcher's registration does not refuse the arm", s.text())

# --- --await-launch: an arm that finds nothing running waits for this session's launch entry
s = Session("await_none")
put_state(s.root, [])
started_at = time.time()
s.arm(flags=["--await-launch=1"])
check(exits_idle(s) and s.text().strip() == "NONE" and time.time() - started_at >= 1,
      "an arm with --await-launch and no launch inside the grace exits NONE once the grace ends", s.text())

s = Session("await_default")
put_state(s.root, [])
started_at = time.time()
s.arm({"DISPATCH_TABLE_LAUNCH_WAIT": "0.5", "DISPATCH_TABLE_UNRESOLVED_WAIT": "0.5"})
check(exits_idle(s) and time.time() - started_at >= 1,
      "a bare session arm waits launch_grace() for its launch entry before it exits NONE", s.text())

s = Session("await_launch")
put_state(s.root, [])
late = s.root / "late"
late.mkdir()
check(arm_observed(s, "polls >= 2", {"WATCH_RECORDS_CHECKIN_SEC": "2"}, ["--await-launch=60"]),
      "the watcher finished its first poll inside the grace", s.text())
check(wait_polls(s.root / "state", 3, proc=s.proc), "the watcher stayed through a poll after the first")
put_state(s.root, [entry("late", None, late)])
check(s.exits() and "NONE" not in s.text() and "STILL RUNNING 1" in s.text(),
      "a launch entry planted inside the grace is watched, not NONE", s.text())
bare = under_test("import dispatch_table as d; print(w.launch_grace(), d.launch_grace())",
                  {"DISPATCH_TABLE_LAUNCH_WAIT": "1.5", "DISPATCH_TABLE_UNRESOLVED_WAIT": "2"})
check(bare.returncode == 0 and bare.stdout.split() == ["3.5", "3.5"],
      "the bare --await-launch grace is dispatch_table's own launch_grace", bare.stderr)

# --- a unit that leaves the live set between polls still prints the DONE lines held for it before NONE
scripted = iter([([], 1, set()), ([("done", "j1-done", "DONE j1", None)], 1, set()), ([], 0, set())])
real_wake = wr._wake
wr._wake = lambda grouped: next(scripted)
try:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = wr.wake_loop(lambda: {})
finally:
    wr._wake = real_wake
check(code == 0 and out.getvalue().splitlines() == ["DONE j1", "NONE"],
      "a held DONE line prints before NONE when the live set empties", repr(out.getvalue()))

# --- 2026-10-19, session 30b7e19d: a WAITING-MEMORY line held for the next wake printed the first WAIT sample
# after the launcher's READY. A held line for a condition that cleared is dropped; one still standing prints its latest reading.
def held_memory_run(second, third):
    scripted = iter([([], 1, set()), second, third])
    real = wr._wake
    wr._wake = lambda grouped: next(scripted)
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = wr.wake_loop(lambda: {})
    finally:
        wr._wake = real
    return code, out.getvalue().splitlines()


waiting_id = ("memory", "r.record.json")
first_wait = ("memory", waiting_id, "WAITING-MEMORY r: WAIT first sample", False)
died = ("end", ("end", "x.record.json"), "DIED x", True)
code, printed = held_memory_run(([first_wait], 1, {waiting_id}), ([died], 1, set()))
check(code == 0 and printed[:2] == ["DIED x", "STILL RUNNING 1"] and printed[2].startswith("RE-ARM NOW:"),
      "a held WAITING-MEMORY line is dropped once the launcher's wait cleared", repr(printed))
code, printed = held_memory_run(([first_wait], 1, {waiting_id}),
                                ([("memory", waiting_id, "WAITING-MEMORY r: STILL WAITING 5 min", False), died],
                                 1, {waiting_id}))
check(code == 0 and printed[:3] == ["DIED x", "WAITING-MEMORY r: STILL WAITING 5 min", "STILL RUNNING 1"]
      and printed[3].startswith("RE-ARM NOW:"),
      "a held WAITING-MEMORY line still standing prints its latest reading", repr(printed))

# --- a stall whose provider traffic is fresh is a reasoning turn: it prints, and only a stale one wakes
saved = {n: getattr(wr, n) for n in ("record_state", "stall_idle", "upstream_age", "usage_line", "orphan_line",
                                     "gone_fanout_orphans")}
wr.record_state = lambda record: ("WATCH", None, "pid")
wr.stall_idle = lambda record: "900"
wr.usage_line = lambda record, label: None
wr.orphan_line = lambda unit: None
wr.gone_fanout_orphans = lambda unit: []
unit = {"records": [("slow", "slow.record.json")], "finished": [], "unresolved": [], "fanout_dir": None,
        "launched": None, "finish": {}}
try:
    triggers = {}
    for name, age in (("fresh", 30), ("stale", 3600), ("unknown", None)):
        wr.upstream_age = lambda record, age=age: age
        triggers[name] = [t for kind, _, _, t in wr._wake({"u": unit})[0] if kind == "stall"]
finally:
    for n, f in saved.items():
        setattr(wr, n, f)
check(triggers == {"fresh": [False], "stale": [True], "unknown": [True]},
      "a stall with fresh upstream traffic prints without waking; a stale or unknown one wakes", repr(triggers))

# --- a usage reading is context; the job's DONE is the one wake
saved = {n: getattr(wr, n) for n in ("record_state", "stall_idle", "usage_line", "orphan_line", "gone_fanout_orphans")}
wr.record_state = lambda record: ("WATCH", None, "pid")
wr.stall_idle = lambda record: None
wr.usage_line = lambda record, label: "USAGE-LIMIT %s codex resets unknown" % label
wr.orphan_line = lambda unit: None
wr.gone_fanout_orphans = lambda unit: []
unit = {"records": [("lim", "lim.record.json")], "finished": [], "unresolved": [], "fanout_dir": None,
        "launched": None, "finish": {}}
try:
    usage_triggers = [t for kind, _, _, t in wr._wake({"u": unit})[0] if kind == "usage"]
finally:
    for n, f in saved.items():
        setattr(wr, n, f)
check(usage_triggers == [False], "a usage reading prints without waking; the job's DONE is the wake", repr(usage_triggers))

# --- a file deleted between listing and stat (a fan-out drops its inventory, the proxy rotates traffic)
# is skipped, never a crash of the whole watcher
with tempfile.TemporaryDirectory(prefix="vanish_") as temp:
    fan = Path(temp)
    real = fan / "fanout-real.inventory.json"
    write_inventory(real, [("v1", fan / "v1.record.json")], os.getpid())
    gone = str(fan / "fanout-gone.inventory.json")
    saved_glob = wr.glob.glob
    wr.glob.glob = lambda pattern: [gone, str(real)]
    try:
        try:
            process, jobs = wr._inventory({"fanout_dir": str(fan)})
            survived = bool(jobs)
        except OSError as exc:
            survived = repr(exc)
    finally:
        wr.glob.glob = saved_glob
    check(survived is True, "an inventory deleted mid-scan is skipped, not a crash", str(survived))

    (fan / "v1.stream").write_text(json.dumps({"session_id": "sess"}) + "\n", encoding="utf-8")
    Path(str(fan / "v1.record.json") + ".pid").write_text("1")
    os.utime(str(fan / "v1.record.json") + ".pid", (time.time() - 600,) * 2)
    req = fan / "traffic" / "sess" / "001"
    req.mkdir(parents=True)
    (req / "kept.json").write_text("{}")
    saved_walk = wr.os.walk
    wr.os.walk = lambda top: iter([(str(req), [], ["vanished.json", "kept.json"])])
    try:
        try:
            age = wr.upstream_age(str(fan / "v1.record.json"), traffic_root=str(fan / "traffic"))
        except OSError as exc:
            age = repr(exc)
    finally:
        wr.os.walk = saved_walk
    check(isinstance(age, (int, float)), "a traffic file deleted mid-walk is skipped, not a crash", str(age))

# A fan-out whose jobs end is reported once: the wake prints every ended line, and a watcher armed in a later
# turn does not print or wake on them again.
s = Session("printed_once")
fan = s.root / "fan"
fan.mkdir()
p1, p2 = fan / "p1.record.json", fan / "p2.record.json"
inv = fan / "fanout-proof.inventory.json"
write_inventory(inv, [("p1", p1), ("p2", p2)], 99999999)  # both jobs ended; the launcher is gone
for record in (p1, p2):
    record.write_text("{}")
    Path(str(record) + ".exit").write_text("0")
put_state(s.root, [entry("p1", p1, fan, inv), entry("p2", p2, fan, inv)])
s.arm()
check(s.exits() and "DONE p1 exit=0" in s.text() and "DONE p2 exit=0" in s.text(),
      "the wake prints each ended job", s.text())
s.arm(flags=NO_GRACE)
check(exits_idle(s) and s.text().strip() == "NONE",
      "a watcher armed in a later turn does not reprint what a wake already printed: it exits NONE", s.text())

# A success deferred behind a sibling's stall is printed by that wake, so the next watcher omits it.
s = Session("deferred_once")
fan = s.root / "fan"
fan.mkdir()
q1, q2 = fan / "q1.record.json", fan / "q2.record.json"
inv = fan / "fanout-proof.inventory.json"
write_inventory(inv, [("q1", q1, "pair"), ("q2", q2, "pair")], os.getpid())  # one group: q1's success is held
for record in (q1, q2):
    Path(str(record) + ".pid").write_text(line(os.getpid()) + "\n")
put_state(s.root, [entry("q1", q1, fan, inv), entry("q2", q2, fan, inv)])
check(arm_observed(s, "polls >= 2"), "the watcher finished its first poll", s.text())

def end_q1():
    q1.write_text("{}")
    Path(str(q1) + ".exit").write_text("0")

check(polled_after(s.root / "state", end_q1, proc=s.proc), "the watcher held q1's clean end")
journal_row(q2, event="stalled", idle=900)
check(s.exits() and "DONE q1 exit=0" in s.text() and "STALLED q2 idle=900" in s.text(),
      "a stall wake prints the success held behind it", s.text())
s.arm()
check(still_watching(s) and "DONE q1" not in s.text() and "STILL RUNNING" not in s.text(),
      "the next watcher omits the success a wake already printed", s.text())

# The bench is a plugin: the watcher reaches it only through adaptation.plugin_entry, inside the entry's ENTRY_CALLS row.
# Planted ready, absent and unreadable plugins prove what each watcher call does with each state; the entry's own
# parsing (the grid hold, the detached registry) is proved in test_bench_entry_calls.py.
sys.path.insert(0, str(ROOT / "tests"))
import importlib.util  # noqa: E402
import _plugin_plant as plug  # noqa: E402

spec = importlib.util.spec_from_file_location("watch_records_inproc", WATCH)
wr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wr)
plant_root = Path(tempfile.mkdtemp(prefix="watch_plugin_"))
LIVE_ROW = {"name": "r1", "state": "LIVE", "last": "WAITING", "age": 1, "pid": 4242}
DETACHED_UNIT = {"roster:succession-7": {"roster": {"name": "succession-7", "state": "LIVE", "last": "STAGE", "age": 0},
                                         "records": [], "cells": []}}
ready = plug.plant(plant_root / "ready", {"campaign_of": "camp-2026-10-09", "grid_running": True, "rosters": [LIVE_ROW],
                                          "detached_units": DETACHED_UNIT, "running_cells": [["ARM-T1-X-r1", "x.progress.jsonl"]]})
absent = plug.plant_absent(plant_root / "absent")
broken = plug.plant_unreadable(plant_root / "broken")
cell_entry = {"fanout_dir": None, "jobs": [{"label": "anything"}]}
wr.process_session = lambda pid: SID
wr.ROSTER_RECHECK_S = 0


def at(claude):
    wr.CLAUDE_DIR = str(claude)


at(ready)
check(wr.bench_campaign(cell_entry) == "camp-2026-10-09" and wr.bench_grid_running("camp") is True,
      "a ready plugin answers the campaign of a cell label and its grid hold")
check(wr.bench_campaign({"fanout_dir": "x", "jobs": [{"label": "anything"}]}) is None,
      "a fan-out entry is never a bench campaign")
units = wr.session_rosters(SID)()
check(set(units) == {"roster:r1", "roster:succession-7"} and units["roster:r1"]["cells"] == [["ARM-T1-X-r1", "x.progress.jsonl"]],
      "a ready plugin's roster rows, detached units and running cells become the session's units", repr(units))
check(plug.within_row(ready), "the watcher touched only the entry's declared calls", repr(plug.accessed(ready)))
for label, claude in (("absent", absent), ("unreadable", broken)):
    at(claude)
    check(wr.bench_campaign(cell_entry) is None and wr.bench_grid_running("camp") is False,
          "an %s plugin has no campaign and no running grid, so the unit ends and wakes" % label)
at(absent)
check(wr.session_rosters(SID)() == {}, "an absent plugin has no rosters")
at(ready)
poll = wr.session_rosters(SID)
first = poll()
at(broken)
check(poll() == first and first, "an unreadable plugin keeps the roster poll's last answer")
at(broken)
check(wr.session_rosters(SID)() == {}, "an unreadable plugin on the first poll answers no rosters")

# A roster's end wakes unless another roster of this session still runs. `live` is a LIVE row; `ended` an ended one.

def roster_units(ended_last):
    status = ended_last.rsplit(" ", 1)[1]
    rows = [{"name": "ended", "state": "DONE" if status == "ok" else "HALTED", "last": ended_last, "age": 0,
             "terminal_status": status, "terminal_line": ended_last},
            {"name": "live", "state": "LIVE", "last": "WAITING for prior grid", "age": 0,
             "terminal_status": None, "terminal_line": None}]
    return {"roster:" + row["name"]: {"roster": row, "records": []} for row in rows}


def roster_end_lines(units):
    lines, live, _ = wr._wake(units)
    return [trig for kind, _, text, trig in lines if kind == "roster" and text.startswith("ROSTER ")
            and " ended: " in text], live


ok_trig, ok_live = roster_end_lines(roster_units("END ended ok"))
check(ok_trig == [False] and ok_live == 1,
      "a roster ended ok while another roster of this session is live wakes nobody", repr((ok_trig, ok_live)))
halt_trig, halt_live = roster_end_lines(roster_units("END ended halt"))
check(halt_trig == [True] and halt_live == 1,
      "a roster ended halt wakes the session even while another roster is live", repr((halt_trig, halt_live)))
solo = roster_units("END ended ok")
del solo["roster:live"]
solo_trig, solo_live = roster_end_lines(solo)
check(solo_trig == [True] and solo_live == 0,
      "a roster ended ok with no other roster live wakes the session", repr((solo_trig, solo_live)))

# A detached bench job (succession, probes, score) is one roster-shaped unit of the session that launched it, and it
# wakes on its state: the entry classifies the log (test_bench_entry_calls.py), the watcher renders the row.
import types  # noqa: E402

det_root = Path(tempfile.mkdtemp(prefix="watch_detached_"))
(det_root / "reg").mkdir()


def detached_units(state, last):
    row = {"name": "succession-4242", "state": state, "last": last, "age": 0, "terminal_status": None, "terminal_line": None}
    return {"roster:succession-4242": {"roster": row, "records": [], "cells": []}}


def detached_wakes(units):
    lines, live, _ = wr._wake(units)
    return [(text, trig) for kind, _, text, trig in lines if kind == "roster"], live


units = detached_units("LIVE", "SCORE ARM-T2 start")
check(wr._wake(units)[1] == 1 and detached_wakes(units)[0] == [],
      "a live detached succession is one live unit and wakes nobody before its END", repr(detached_wakes(units)))
check(wr.checkin_lines(units) and any("succession-4242" in l for l in wr.checkin_lines(units)),
      "a live detached job shows in the session's check-in lines", repr(wr.checkin_lines(units)))
for state, last, label in (("DONE", "END succession ok", "a detached succession's END ok line wakes the session once, with that line"),
                           ("HALTED", "END succession failed", "a detached job that ended failed wakes the session"),
                           ("DEAD", "STAGE probes", "a detached job whose pid died with no END wakes the session")):
    check(detached_wakes(detached_units(state, last)) == ([("ROSTER %s succession-4242: %s" % (state, last), True)], 0),
          label, repr(detached_wakes(detached_units(state, last))))
units = detached_units("LIVE", "WAIT memory headroom for daemon:ARM-T4-DECOY-r1: commit 8.57 GiB free")
check(detached_wakes(units) == ([], 1),
      "a detached job on a self-resuming WAIT line is live and wakes nobody", repr(detached_wakes(units)))

# A roster's running cell registers no record, so its compaction cycle is read from the cell's own progress stream.
# The stream rows are cut from ARM-T1R-HAIKU-r1 of cov-haiku-max-t1.
import shutil  # noqa: E402

cell_root = Path(tempfile.mkdtemp(prefix="watch_cells_"))
cell_logs, cell_camps, cell_results = cell_root / "logs", cell_root / "campaigns", cell_root / "results"
for folder in (cell_logs / "camp", cell_camps, cell_results / "camp-out" / "v13cells"):
    folder.mkdir(parents=True)
(cell_camps / "camp.env").write_text("BENCH_OUT_NAME=camp-out\n")
(cell_logs / "live.log").write_text("START live\ncampaign : camp (camp-out)\n")
(cell_logs / "live.pid").write_text(str(os.getpid()))
grid = cell_logs / "camp" / "grid.log"
grid.write_text("CELL t1s/haiku/r1 start effort=max 22:51:28\nCELL t1s/haiku/r1 rc=0 end 23:13:06\n"
                "CELL t1r/haiku/r1 start effort=max 23:13:11\n")
for cell in ("ARM-T1S-HAIKU-r1", "ARM-T1R-HAIKU-r1"):
    shutil.copy(ROOT / "tests" / "fixtures" / "watch" / "bench_cell_cycle.progress.jsonl",
                cell_results / "camp-out" / "v13cells" / (cell + ".progress.jsonl"))
running = [("ARM-T1R-HAIKU-r1", str(cell_results / "camp-out" / "v13cells" / "ARM-T1R-HAIKU-r1.progress.jsonl"))]
row = {"name": "live", "state": "LIVE", "last": "WAITING", "age": 0, "terminal_status": None, "terminal_line": None}
lines, _, _ = wr._wake({"roster:live": {"roster": row, "records": [], "cells": running}})
cycling = [(text, trig) for kind, _, text, trig in lines if kind == "compacting"]
check(cycling == [("COMPACTING roster live ARM-T1R-HAIKU-r1 compacted 2, re-read T1-addon-survey.md", True)],
      "a running cell that compacts again after re-reading a file wakes the session", repr(lines))
# A read-only cell re-reads its input after each compaction but still reaches new files: it advances, so no wake.
# The stream rows are cut from ARM-T11-INKLING-LOCAL-r1 of inkling-effort-high (last segment: 2 new files, 1 re-read).
advancing = cell_results / "camp-out" / "v13cells" / "ARM-T1R-HAIKU-r1.progress.jsonl"
shutil.copy(ROOT / "tests" / "fixtures" / "watch" / "bench_cell_progress_advancing.progress.jsonl", advancing)
lines, _, _ = wr._wake({"roster:live": {"roster": row, "records": [], "cells": running}})
check([l for l in lines if l[0] == "compacting"] == [],
      "a running cell whose last segment reached a new file is advancing and wakes nobody", repr(lines))
# The check-in judges a bench arm like any job: its running cell gets the progress clause its stream shows.
roster_checkin = wr.checkin_lines({"roster:live": {"roster": row, "records": [], "cells": running}})
cell_clause = [l for l in roster_checkin if l.startswith("  ARM-T1R-HAIKU-r1: progress")]
check(len(cell_clause) == 1 and cell_clause[0] == "  ARM-T1R-HAIKU-r1: " + wr.progress_clause(running[0][1], time.time()),
      "a roster's running cell carries its progress clause at check-in", repr(roster_checkin))

# ---------------------------------------------------------------- a job the session stopped with reap.py
# reap.py lists each launch id it killed under `sidecar_stopped_launches`. Its job's end is context, not a wake.
def put_session(root, entries, stopped=()):
    (root / "state" / (SID[:8] + ".json")).write_text(
        json.dumps({"dispatch_table_pending": entries, "sidecar_stopped_launches": list(stopped)}))


def job(folder, name, launch, code=None, pid=None):
    """A record whose `.pid` names `launch` under a launcher that is gone unless `pid` is live; `code` writes `.exit`."""
    record = folder / (name + ".record.json")
    record.write_text("{}")
    Path(str(record) + ".pid").write_text(with_launch(pid or 99999999, launch) + "\n")
    if code is not None:
        Path(str(record) + ".exit").write_text(code)
    return record


# 1. incident replay: a stopped job's exit 1 beside a running sibling wakes nobody; its end prints STOPPED later.
s = Session("stopped_replay")
sibling = sleeper()
a = job(s.root, "a", "L-stopped-1", code="1")
b = job(s.root, "b", "L-sibling", pid=sibling.pid)
put_session(s.root, [entry("a", a), entry("b", b)], stopped=["L-stopped-1"])
s.arm()
until(lambda: "STOPPED a" in s.text() or s.proc.poll() is not None)
check(wait_polls(s.root / "state", 2, proc=s.proc), "the watcher finished a second poll")
check(s.proc.poll() is None and "STILL RUNNING" not in s.text(),
      "incident replay: a job the session stopped, exiting 1, wakes nobody while a sibling runs", s.text())
Path(str(b) + ".exit").write_text("0")
check(s.exits() and "STOPPED a" in s.text() and "DONE b exit=0" in s.text(),
      "... and its end prints STOPPED with the sibling's wake", s.text())
killed(sibling)

# 2. control: the same exit 1 with no stop on record wakes the session.
s = Session("stopped_control")
sibling = sleeper()
a = job(s.root, "a", "L-stopped-1", code="1")
b = job(s.root, "b", "L-sibling", pid=sibling.pid)
put_session(s.root, [entry("a", a), entry("b", b)])
s.arm()
check(s.exits() and "DONE a exit=1" in s.text() and "STILL RUNNING 1" in s.text(),
      "control: the same exit 1 with no stop on record wakes the session", s.text())
killed(sibling)

# 3. a relaunch has a new launch id, so its failure wakes although the first attempt was stopped.
s = Session("stopped_relaunch")
sibling = sleeper()
old = job(s.root, "a-old", "L-stopped-1", code="1")
new = job(s.root, "a-new", "L-relaunch", code="1")
b = job(s.root, "b", "L-sibling", pid=sibling.pid)
put_session(s.root, [entry("a", old), entry("a", new), entry("b", b)], stopped=["L-stopped-1"])
s.arm()
check(s.exits() and "DONE a exit=1" in s.text() and "STOPPED a" in s.text(),
      "a relaunch that fails wakes though its stopped first attempt does not", s.text())
killed(sibling)

# 4. a dispatch unit: it wakes on its complete while any seat survived the stop, and wakes nobody once all were stopped.
s = Session("stopped_unit_survivor")
fan = s.root / "fan"
fan.mkdir()
inv = fan / "fanout-proof.inventory.json"
s1, s2 = job(fan, "s1", "L-seat-1", code="1"), job(fan, "s2", "L-seat-2", code="0")
write_inventory(inv, [("s1", s1), ("s2", s2)], 99999999)
put_session(s.root, [entry("s1", s1, fan, inv), entry("s2", s2, fan, inv)], stopped=["L-seat-1"])
s.arm()
check(s.exits() and "DONE s2 exit=0" in s.text() and "STILL RUNNING 0" in s.text(),
      "a unit with a seat the session did not stop still wakes on its complete", s.text())

s = Session("stopped_unit_all")
fan = s.root / "fan"
fan.mkdir()
inv = fan / "fanout-proof.inventory.json"
s1, s2 = job(fan, "s1", "L-seat-1", code="1"), job(fan, "s2", "L-seat-2", code="1")
write_inventory(inv, [("s1", s1), ("s2", s2)], 99999999)
put_session(s.root, [entry("s1", s1, fan, inv), entry("s2", s2, fan, inv)], stopped=["L-seat-1", "L-seat-2"])
s.arm(flags=NO_GRACE)
check(exits_idle(s) and "STOPPED s1" in s.text() and "STOPPED s2" in s.text() and "STILL RUNNING" not in s.text(),
      "a unit whose every seat the session stopped leaves nothing live: its STOPPED lines print, the watcher exits NONE",
      s.text())

# 5. a stopped run that DIED (no .exit, launcher gone) is context too; an unlisted launch id still wakes.
s = Session("stopped_died")
sibling = sleeper()
a = job(s.root, "a", "L-stopped-1")
b = job(s.root, "b", "L-sibling", pid=sibling.pid)
put_session(s.root, [entry("a", a), entry("b", b)], stopped=["L-stopped-1"])
s.arm()
until(lambda: "STOPPED a" in s.text() or s.proc.poll() is not None)
check(wait_polls(s.root / "state", 2, proc=s.proc), "the watcher finished a second poll")
check(s.proc.poll() is None and "STOPPED a" in s.text() and "DIED" not in s.text(),
      "a stopped run that died wakes nobody while a sibling runs", s.text())
killed(sibling)

s = Session("stopped_died_control")
sibling = sleeper()
a = job(s.root, "a", "L-unlisted")
b = job(s.root, "b", "L-sibling", pid=sibling.pid)
put_session(s.root, [entry("a", a), entry("b", b)], stopped=["L-stopped-1"])
s.arm()
check(s.exits() and "DIED a" in s.text(),
      "control: a run that died with an unlisted launch id wakes the session", s.text())
killed(sibling)

# --- a seat that compacts, re-reads what it read before and compacts again is reported, not only counted.
# Rows are the real transcript shapes trimmed (agent transcript: camelCase `compactMetadata`; sidecar `.stream`: snake_case).
def read_row(path):
    return {"parentUuid": "p", "isSidechain": True, "agentId": "a1", "type": "assistant",
            "message": {"model": "claude-haiku-5-5", "type": "message", "role": "assistant",
                        "content": [{"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": path}}]}}


def boundary_row(ms, stream=False):
    meta = {"trigger": "auto", "pre_tokens": 219740, "duration_ms": ms} if stream else \
        {"trigger": "auto", "preTokens": 219740, "durationMs": ms}
    if ms is None:
        meta.pop("duration_ms", None)
        meta.pop("durationMs", None)
    return {"parentUuid": None, "isSidechain": True, "type": "system", "subtype": "compact_boundary",
            "content": "Conversation compacted", "isMeta": False, "level": "info",
            ("compact_metadata" if stream else "compactMetadata"): meta}


def write_row(path):
    row = read_row(path)
    row["message"]["content"] = [{"type": "tool_use", "id": "toolu_2", "name": "Write",
                                  "input": {"file_path": path, "content": "findings"}}]
    return row


def transcript_text(events, stream=False):
    makers = {"read": read_row, "write": write_row}
    rows = [makers[kind](arg) if kind in makers else boundary_row(arg, stream) for kind, arg in events]
    return "".join(json.dumps(r) + "\n" for r in rows)


CYCLE = [("read", "C:/p/plan.md"), ("boundary", 200000), ("read", "C:/p/plan.md"), ("boundary", 60000)]
ONCE = [("read", "C:/p/plan.md"), ("boundary", 90000), ("read", "C:/p/other.md")]
# The same cycle from a seat that wrote its findings before the compaction: its work survived, so nothing is lost.
SAVED = [("read", "C:/p/plan.md"), ("boundary", 200000), ("read", "C:/p/plan.md"),
         ("write", "C:/p/report.md"), ("boundary", 60000)]


def plant_agent(session_dir, run_id, agent_id, label, events, ended=False, partial=""):
    run_dir = session_dir / "subagents" / "workflows" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / ("agent-%s.jsonl" % agent_id)).write_text(transcript_text(events) + partial, encoding="utf-8")
    (run_dir / ("agent-%s.meta.json" % agent_id)).write_text(json.dumps({"description": label}))
    rows = [{"type": "launched"},
            {"type": "started", "key": "k-" + agent_id, "agentId": agent_id, "label": label, "phase": "Review"}]
    if ended:
        rows.append({"type": "result", "key": "k-" + agent_id, "agentId": agent_id, "result": {}})
    (run_dir / "journal.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return run_dir


def append_events(run_dir, agent_id, events):
    with open(run_dir / ("agent-%s.jsonl" % agent_id), "a", encoding="utf-8") as fh:
        fh.write(transcript_text(events))


def compaction_of(path):
    r = under_test("import json; print(json.dumps(w.agent_compaction(%r)))" % str(path))
    return r, (json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {})


with tempfile.TemporaryDirectory(prefix="watch_compact_") as temp:
    t = Path(temp) / "agent.jsonl"
    r, got = compaction_of(t)
    check(got == {"count": 0, "minutes": None, "reread": None, "saved": False},
          "agent_compaction of a missing transcript is zero compactions", r.stderr + r.stdout)
    t.write_text(transcript_text(CYCLE) + '{"type":"assis', encoding="utf-8")
    r, got = compaction_of(t)
    check(got.get("count") == 2 and got.get("reread") == "plan.md" and abs(got.get("minutes", 0) - 260 / 60) < 0.01,
          "agent_compaction counts boundaries, sums their minutes and names the re-read file; a partly written last line is skipped",
          r.stderr + r.stdout)
    t.write_text(transcript_text(ONCE), encoding="utf-8")
    r, got = compaction_of(t)
    check(got.get("count") == 1 and got.get("reread") is None, "one boundary with no re-read is not a cycle", r.stderr + r.stdout)
    t.write_text(transcript_text([("read", "C:/p/a.md"), ("boundary", None), ("read", "C:/p/b.md"), ("boundary", None)]),
                 encoding="utf-8")
    r, got = compaction_of(t)
    check(got == {"count": 2, "minutes": None, "reread": None, "saved": False},
          "boundaries without a duration give minutes None; reading new files each time is no cycle", r.stderr + r.stdout)
    t.write_text(transcript_text(CYCLE), encoding="utf-8")
    r, got = compaction_of(t)
    check(got.get("reread") == "plan.md" and got.get("saved") is False,
          "a seat that wrote nothing before its last compaction did not save its work", r.stderr + r.stdout)
    t.write_text(transcript_text(SAVED), encoding="utf-8")
    r, got = compaction_of(t)
    check(got.get("reread") == "plan.md" and got.get("saved") is True,
          "a seat that wrote a file before its last compaction saved its work", r.stderr + r.stdout)

s = Session("cmp_cycle")
sess = s.root / "sess"
label = "review:plc-pattern-fit"
run = plant_agent(sess, "wf_cyc", "acyc", label, CYCLE)
put_state(s.root, [workflow_entry(sess, "wf_cyc")])
s.arm()
check(s.exits() and ("COMPACTING workflow wf_cyc %s compacted 2, re-read plan.md" % label) in s.text()
      and "STILL RUNNING 1" in s.text(),
      "a running Workflow seat in the compaction cycle wakes the session with COMPACTING, its label and count", s.text())
s.arm()
check(polled(s.root / "state", polls=2, proc=s.proc), "the re-armed watcher finished two polls")
check(s.proc.poll() is None, "the same cycle on the next arm and poll wakes nobody again", s.text())
append_events(run, "acyc", [("read", "C:/p/plan.md"), ("boundary", 30000)])
check(s.exits() and "compacted 3, re-read plan.md" in s.text(), "a third boundary is one new wake", s.text())

s = Session("cmp_once")
sess = s.root / "sess"
plant_agent(sess, "wf_once", "aonce", "review:memory", ONCE)
put_state(s.root, [workflow_entry(sess, "wf_once")])
s.arm({"WATCH_RECORDS_CHECKIN_SEC": "1"})
check(s.exits() and "CHECKIN" in s.text() and "COMPACTING" not in s.text()
      and "review:memory compacted 1 (1.5 min compacting)" in s.text(),
      "one compaction with no re-read adds the check-in clause and does not wake", s.text())

s = Session("cmp_ended")
sess = s.root / "sess"
plant_agent(sess, "wf_end", "aend", "review:done", CYCLE, ended=True)
put_state(s.root, [workflow_entry(sess, "wf_end")])
s.arm({"WATCH_RECORDS_CHECKIN_SEC": "1"})
check(s.exits() and "COMPACTING" not in s.text() and "compacted" not in s.text(),
      "an agent that already has its result neither wakes nor appears in the check-in", s.text())

s = Session("cmp_partial")
sess = s.root / "sess"
plant_agent(sess, "wf_part", "apart", "review:partial", ONCE, partial='{"type":"assistant","mess')
put_state(s.root, [workflow_entry(sess, "wf_part")])
s.arm({"WATCH_RECORDS_CHECKIN_SEC": "1"})
check(s.exits() and s.proc.returncode == 0 and "Traceback" not in s.err.read_text() and "compacted 1" in s.text(),
      "a partly written last transcript line does not crash the watcher", s.text() + s.err.read_text())

# a foreign run dir (no dispatch-table entry) is watched positionally as a workflow unit
with tempfile.TemporaryDirectory(prefix="watch_foreign_") as temp:
    foreign_session = Path(temp) / "sess"
    run = plant_agent(foreign_session, "wf_foreign", "afor", "review:foreign", CYCLE)
    state = Path(temp) / "state"
    state.mkdir()
    foreign_env = dict(os.environ, CLAUDE_CODE_SESSION_ID=SID, HARNESS_HOOK_STATE_DIR=str(state),
                       WATCH_RECORDS_POLL_SEC="0.05")
    proc = subprocess.run([sys.executable, str(WATCH), str(run)], capture_output=True, text=True,
                          timeout=HANG_BOUND, env=foreign_env)
    check(proc.returncode == 0
          and "COMPACTING workflow wf_foreign review:foreign compacted 2, re-read plan.md" in proc.stdout,
          "a Workflow run dir passed positionally is watched and its cycle wakes", proc.stdout + proc.stderr)
    host_record(foreign_session, "wf_foreign", "completed")
    proc = subprocess.run([sys.executable, str(WATCH), str(run)], capture_output=True, text=True,
                          timeout=HANG_BOUND, env=dict(foreign_env, HARNESS_HOOK_STATE_DIR=str(Path(temp) / "state2")))
    check(proc.returncode == 0 and proc.stdout.startswith("DONE workflow Workflow wf_foreign status=completed")
          and proc.stdout.strip().endswith("NONE"),
          "a foreign run dir whose host record says finished prints its DONE line, then NONE", proc.stdout + proc.stderr)

# --- a seat whose transcript stops growing while its run is alive is a stall: the client's request never returned
# (A2b rail130, 2026-10-14: two seats sat 31 min after a tool result with no further row; H3 r1/r3/r4 each lost a
# seat the same way). The watcher wakes with STALLED once per stall; a seat that grows again and stalls later is a
# new event; a stall shorter than the threshold, a finished seat and a finished run stay silent.
with tempfile.TemporaryDirectory(prefix="watch_stall_") as temp:
    stall_session = Path(temp) / "sess"
    run = plant_agent(stall_session, "wf_stall", "astall", "review:plc-test-readiness", ONCE)
    # a fresh sibling seat in the same run: its transcript, meta and journal row beside the stalled one's
    (run / "agent-afresh.jsonl").write_text(transcript_text(ONCE), encoding="utf-8")
    (run / "agent-afresh.meta.json").write_text(json.dumps({"description": "review:plc-memory-alignment"}))
    (run / "journal.jsonl").open("a", encoding="utf-8").write(json.dumps(
        {"type": "started", "key": "k-afresh", "agentId": "afresh", "label": "review:plc-memory-alignment",
         "phase": "Review"}) + "\n")
    old = time.time() - 11 * 60
    os.utime(run / "agent-astall.jsonl", (old, old))
    stall_env = dict(os.environ, CLAUDE_CODE_SESSION_ID=SID, HARNESS_HOOK_STATE_DIR=str(Path(temp) / "state"),
                     WATCH_RECORDS_POLL_SEC="0.05")

    def watch_or_hang(env, bound=HANG_BOUND):
        """The watcher's run, or a stand-in (returncode -1) when it did not wake inside `bound` seconds."""
        try:
            return subprocess.run([sys.executable, str(WATCH), str(run)], capture_output=True, text=True,
                                  timeout=bound, env=env)
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(exc.cmd, -1, "HUNG %ss: %s" % (bound, exc.stdout or ""), "")

    proc = watch_or_hang(stall_env)
    check(proc.returncode == 0 and "STALLED workflow wf_stall review:plc-test-readiness idle=11 min" in proc.stdout
          and "plc-memory-alignment idle" not in proc.stdout,
          "a Workflow seat whose transcript has not grown for longer than the stall threshold wakes with STALLED, "
          "its label and idle minutes; a fresh sibling is not named", proc.stdout + proc.stderr)
    proc = watch_or_hang(stall_env, bound=3)
    check(proc.returncode == -1, "the same stall on the next arm wakes nobody again (it prints as context and the "
          "watcher stays armed)", proc.stdout + proc.stderr)
    append_events(run, "astall", [("read", "C:/p/more.md")])
    os.utime(run / "agent-astall.jsonl", (old, old))
    proc = watch_or_hang(stall_env)
    check(proc.returncode == 0 and "STALLED workflow wf_stall review:plc-test-readiness idle=11 min" in proc.stdout,
          "a seat that grew and stalled again is a new STALLED wake", proc.stdout + proc.stderr)
    (run / "journal.jsonl").open("a", encoding="utf-8").write(
        json.dumps({"type": "result", "key": "k-astall", "agentId": "astall", "result": {}}) + "\n")
    proc = subprocess.run([sys.executable, str(WATCH), str(run)], capture_output=True, text=True,
                          timeout=HANG_BOUND, env=dict(stall_env, HARNESS_HOOK_STATE_DIR=str(Path(temp) / "state3"),
                                                       WATCH_RECORDS_CHECKIN_SEC="1"))
    check(proc.returncode == 0 and "STALLED" not in proc.stdout and "CHECKIN" in proc.stdout,
          "a seat with its result row is never stalled, however old its transcript", proc.stdout + proc.stderr)

# a Claude sidecar's `<record>.stream` carries the same compact_boundary rows: the same check-in clause and wake
s = Session("cmp_sidecar")
holder = sleeper()
rec = job(s.root, "seat", "L-seat", pid=holder.pid)
Path(str(rec) + ".stream").write_text(transcript_text(CYCLE, stream=True), encoding="utf-8")
put_state(s.root, [entry("seat", rec)])
s.arm()
check(s.exits() and "COMPACTING seat compacted 2, re-read plan.md" in s.text(),
      "a running sidecar whose stream shows the compaction cycle wakes with COMPACTING", s.text())
s = Session("cmp_sidecar_once")
rec = job(s.root, "seat", "L-seat", pid=holder.pid)
Path(str(rec) + ".stream").write_text(transcript_text(ONCE, stream=True), encoding="utf-8")
put_state(s.root, [entry("seat", rec)])
s.arm({"WATCH_RECORDS_CHECKIN_SEC": "1"})
check(s.exits() and "COMPACTING" not in s.text() and "RUNNING seat" in s.text()
      and "compacted 1 (1.5 min compacting)" in s.text(),
      "a sidecar that compacted once shows the clause in its check-in line and does not wake", s.text())
# A seat that saved its findings before compacting lost nothing: the session has no action, so it never wakes.
s = Session("cmp_sidecar_saved")
rec = job(s.root, "seat", "L-seat", pid=holder.pid)
Path(str(rec) + ".stream").write_text(transcript_text(SAVED, stream=True), encoding="utf-8")
put_state(s.root, [entry("seat", rec)])
s.arm({"WATCH_RECORDS_CHECKIN_SEC": "1"})
check(s.exits() and "CHECKIN" in s.text() and "COMPACTING" not in s.text()
      and "RUNNING seat" in s.text() and "compacted 2" in s.text(),
      "a sidecar that re-read after compacting but wrote its work first shows the clause and does not wake", s.text())
killed(holder)

s = Session("cmp_saved_workflow")
sess = s.root / "sess"
plant_agent(sess, "wf_saved", "asaved", "review:saved", SAVED)
put_state(s.root, [workflow_entry(sess, "wf_saved")])
s.arm({"WATCH_RECORDS_CHECKIN_SEC": "1"})
check(s.exits() and "CHECKIN" in s.text() and "COMPACTING" not in s.text(),
      "a Workflow seat that wrote its work before compacting does not wake", s.text())

# --------------------------------------------------- the check-in shows each running job's progress and says how to judge it
import calendar
import shutil

PROGRESS_FIX = ROOT / "tests" / "fixtures" / "watch_progress"
VERDICT_WORDS = re.compile(r"\b(loop\w*|stall\w*|lagging|converg\w*|stuck|runaway|should stop)\b", re.I)


def stamp_epoch(text):
    return calendar.timegm(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")) + float("0" + text[19:-1])


def progress_lines(fixture, now_shift=60):
    """The check-in lines for one running job whose stream is a captured fixture, aged to the fixture's own clock."""
    root = Path(tempfile.mkdtemp(prefix="progress_"))
    try:
        record = root / "seat.record.json"
        stream = root / "seat.stream"
        shutil.copyfile(PROGRESS_FIX / fixture, stream) if fixture else stream.write_text("not json at all\n")
        stamps = [json.loads(raw).get("timestamp") for raw in open(stream, "rb") if raw.startswith(b"{")]
        stamps = [stamp_epoch(t) for t in stamps if isinstance(t, str)]
        first, last = (min(stamps), max(stamps)) if stamps else (time.time() - 600, time.time())
        Path(str(record) + ".pid").write_text(line(os.getpid()) + "\n")
        os.utime(str(record) + ".pid", (first, first))
        os.utime(stream, (last, last))
        return watch_records.checkin_lines({"u": {"records": [("seat", str(record))]}}, now=last + now_shift)
    finally:
        shutil.rmtree(root, ignore_errors=True)


gate_lines = progress_lines("gatefix_trim.stream")
gate_clause = next((t for t in gate_lines if t.startswith("  progress:")), "")
check(gate_lines and gate_lines[0].startswith("RUNNING seat for") and len(gate_lines) == 2,
      "a running job's check-in is its RUNNING line plus one progress clause line", str(gate_lines))
check("AlphaRosterTests.cs 12" in gate_clause, "the clause names the most-edited file and its edit count", gate_clause)
rates = re.search(r"tool calls \d+ \(per 50 min, oldest first: ([\d ]+)\)", gate_clause)
series = [int(n) for n in rates.group(1).split()] if rates else []
check(len(series) >= 3 and series[0] > 2 * series[-1], "the clause shows the tool-call rate falling", gate_clause)
check("commits 0" in gate_clause, "a writer's clause prints its commit count", gate_clause)
check(not VERDICT_WORDS.search(re.sub(r'said ".*"', "", " ".join(gate_lines))), "no check-in line carries a verdict word", str(gate_lines))

survey_lines = progress_lines("survey_trim.stream")
check(len(survey_lines) == 2 and "commit" not in survey_lines[1], "a read-only survey's clause prints no commit field", str(survey_lines))
bad_lines = progress_lines(None)
check(bad_lines[1:] == ["  progress unreadable"], "an unparseable stream prints progress unreadable", str(bad_lines))

# the CHECKIN text carries the judge-and-act sentence in place of "read each job's progress, then re-arm"
s = Session("checkin_judge")
fan = s.root / "fan"
fan.mkdir()
j1 = fan / "j1.record.json"
inv = fan / "fanout-proof.inventory.json"
write_inventory(inv, [("j1", j1)], os.getpid())
Path(str(j1) + ".pid").write_text(line(os.getpid()) + "\n")
put_state(s.root, [entry("j1", j1, fan, inv)])
s.arm({"WATCH_RECORDS_CHECKIN_SEC": "1"})
checkin_text = s.text() if s.exits() else ""
check("CHECKIN after" in checkin_text and "read each job's progress, then re-arm" not in checkin_text,
      "the check-in no longer tells the orchestrator to read progress and re-arm", checkin_text)
check("amend.md" in checkin_text and "reap.py" in checkin_text and "owner's word" in checkin_text
      and "--digest" in checkin_text and checkin_text.rstrip().splitlines()[-1].startswith("RE-ARM NOW:"),
      "the CHECKIN text carries the judge-and-act sentence: amend file, split, stop with reap.py, owner's word for a progressing job",
      checkin_text)

# --- one supersede rule and one identity, shared by every reader. A refusal file names the
# labels a launch refused or crashed before starting; an entry issued at or before that file leaves
# every reader's view, and a later-issued same-label entry with a record supersedes it in any out dir.
with tempfile.TemporaryDirectory(prefix="refused_launch_") as raw:
    root = Path(raw)
    dead_dir, later_dir = root / "dead", root / "later"
    dead_dir.mkdir()
    later_dir.mkdir()
    later_record = later_dir / "plc-a.attempt2.record.json"
    Path(str(later_record) + ".pid").write_text(line(os.getpid()) + "\n")
    issued = time.time() - 700
    (dead_dir / "fanout-refused.refused.json").write_text(json.dumps(
        {"labels": ["plc-a"], "refusal": "review launch refused before any seat ran: no mandates list",
         "time": issued + 5}), encoding="utf-8")
    refusal_probe = under_test(
        "import time\n"
        "now = time.time()\n"
        "dead_dir, later_dir, later = %r, %r, %r\n"
        "dead = {'kind': 'sidecar', 'id': 'dead', 'record': None, 'fanout_dir': dead_dir, "
        "'issued_at': %r, 'launched_at': %r, 'jobs': [{'label': 'plc-a'}]}\n"
        "relaunch = {'kind': 'sidecar', 'id': 'relaunch', 'record': later, 'fanout_dir': later_dir, "
        "'issued_at': now - 100, 'launched_at': now - 100, 'jobs': [{'label': 'plc-a'}]}\n"
        "print('refused', w.refused_launch(dead), w.refused_launch(relaunch))\n"
        "print('superseded', w.superseded(dead, [dead, relaunch]), "
        "w.superseded(relaunch, [dead, relaunch]))\n"
        % (str(dead_dir), str(later_dir), str(later_record), issued, issued))
    lines = refusal_probe.stdout.strip().splitlines()
    check(refusal_probe.returncode == 0 and lines == ["refused True False", "superseded True False"],
          "a refusal file covers the call issued before it, not the corrected relaunch after it",
          refusal_probe.stdout + refusal_probe.stderr)

    allow_probe = under_test(
        "import time\n"
        "now = time.time()\n"
        "dead_dir, later_dir, later = %r, %r, %r\n"
        "live_a = {'kind': 'sidecar', 'id': 'a', 'record': None, 'fanout_dir': dead_dir, "
        "'issued_at': now - 5, 'launched_at': now - 5, 'jobs': [{'label': 'same'}]}\n"
        "live_b = {'kind': 'sidecar', 'id': 'b', 'record': None, 'fanout_dir': later_dir, "
        "'issued_at': now - 4, 'launched_at': now - 4, 'jobs': [{'label': 'same'}]}\n"
        "started = {'kind': 'sidecar', 'id': 'c', 'record': later, 'fanout_dir': later_dir, "
        "'issued_at': now - 3, 'launched_at': now - 3, 'jobs': [{'label': 'same'}]}\n"
        "slow = {'kind': 'sidecar', 'id': 'd', 'record': None, 'fanout_dir': dead_dir, "
        "'issued_at': now - 700, 'launched_at': now - 700, 'task_id': 'bg', "
        "'jobs': [{'label': 'bgjob'}]}\n"
        "print(w.superseded(live_a, [live_a, live_b, started]), "
        "w.superseded(live_b, [live_a, live_b, started]), "
        "w.superseded(started, [live_a, live_b, started]), "
        "w.superseded(slow, [started], live_ids={'bg'}))\n"
        % (str(dead_dir), str(later_dir), str(later_record)))
    check(allow_probe.stdout.strip() == "False False False False",
          "two live runs of one label in different dirs, a started run, and a live slow-preflight call "
          "all keep their entries", allow_probe.stdout + allow_probe.stderr)

    row = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(issued + 1)),
           "label": "plc-a", "outcome": "ended-before-result", "exitCode": 2,
           "parentSessionId": SID, "reason": "the launcher exited 2 before a result"}
    ends_probe = under_test(
        "dead = {'kind': 'sidecar', 'id': 'dead', 'record': None, 'fanout_dir': %r, "
        "'issued_at': %r, 'launched_at': %r, 'jobs': [{'label': 'plc-a'}]}\n"
        "later = {'kind': 'sidecar', 'id': 'later', 'record': %r, 'fanout_dir': %r, "
        "'issued_at': %r, 'launched_at': %r, 'jobs': [{'label': 'plc-a'}]}\n"
        "row = %r\n"
        "print(len(w.unreported_ends([dead], [row], frozenset(), 3600)), "
        "len(w.unreported_ends([later], [row], frozenset(), 3600)))\n"
        % (str(dead_dir), issued, issued, str(later_record), str(later_dir), issued + 10, issued + 10, row))
    check(ends_probe.stdout.strip() == "1 0",
          "a row older than the label's next attempt is answered by that attempt, not reported again",
          ends_probe.stdout + ends_probe.stderr)

    state_dir = root / "state"
    state_dir.mkdir()
    (state_dir / (SID[:8] + ".json")).write_text(json.dumps({"dispatch_table_pending": [
        {"kind": "sidecar", "id": "dead", "record": None, "fanout_dir": str(dead_dir),
         "issued_at": issued, "launched_at": issued, "jobs": [{"label": "plc-a"}]},
        {"kind": "sidecar", "id": "relaunch", "record": str(later_record), "fanout_dir": str(later_dir),
         "issued_at": time.time() - 100, "launched_at": time.time() - 100,
         "jobs": [{"label": "plc-a"}]}]}), encoding="utf-8")
    reader_probe = under_test(
        "print([e['id'] for e in w.pending_sidecars(%r)])" % SID,
        {"CLAUDE_CODE_SESSION_ID": SID, "HARNESS_HOOK_STATE_DIR": str(state_dir),
         "SIDECAR_LEDGER_PATH": str(root / "ledger.jsonl")})
    check(reader_probe.stdout.strip() == "['relaunch']",
          "the watcher's own reader drops the refused call's entry and keeps the relaunch",
          reader_probe.stdout + reader_probe.stderr)

    # A failed launch its label's later clean launch superseded is consumed: it wakes nobody. Rows cut from the live
    # jc-survey pair (a failure exit 16, then the relaunch's clean spend row ~8 min later); no pending entry carries either.
    def ended_reports(tag, rows):
        ledger_file = root / ("ledger-%s.jsonl" % tag)
        ledger_file.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        state_for = root / ("state-%s" % tag)
        state_for.mkdir()
        (state_for / (SID[:8] + ".json")).write_text(json.dumps({"dispatch_table_pending": []}), encoding="utf-8")
        return under_test(
            "print([e['jobs'][0]['label'] for e in w.pending_sidecars(%r)])" % SID,
            {"CLAUDE_CODE_SESSION_ID": SID, "HARNESS_HOOK_STATE_DIR": str(state_for),
             "SIDECAR_LEDGER_PATH": str(ledger_file)})

    now_s = time.time()

    def stamp_at(seconds_ago):
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now_s - seconds_ago))

    failed = {"timestamp": stamp_at(900), "parentSessionId": SID, "label": "rel-x", "transport": "opencode",
              "exitCode": 16, "outcome": "ended-before-result", "reason": "the launcher exited 16 before a result",
              "costUSD": 0}
    relaunch = {"timestamp": stamp_at(300), "parentSessionId": SID, "label": "rel-x", "exitCode": 0, "costUSD": 0.21}
    earlier_relaunch = dict(relaunch, timestamp=stamp_at(1200))
    consumed = ended_reports("consumed", [failed, relaunch])
    check(consumed.stdout.strip() == "[]",
          "a failed launch whose later clean relaunch finished is consumed and wakes nobody",
          consumed.stdout + consumed.stderr)
    unconsumed = ended_reports("unconsumed", [failed])
    check(unconsumed.stdout.strip() == "['rel-x']",
          "a failed launch with no later finished launch of its label still wakes",
          unconsumed.stdout + unconsumed.stderr)
    older = ended_reports("older", [failed, earlier_relaunch])
    check(older.stdout.strip() == "['rel-x']",
          "a clean launch that finished before the failure does not consume it",
          older.stdout + older.stderr)

# --digest prints the whole-run view for a stream, a record, and refuses a bad argument
digest_run = subprocess.run([sys.executable, str(WATCH), "--digest", str(PROGRESS_FIX / "gatefix_trim.stream")],
                            capture_output=True, text=True, timeout=60)
check(digest_run.returncode == 0 and "AlphaRosterTests.cs" in digest_run.stdout and "per hour" in digest_run.stdout,
      "--digest prints the per-hour and per-file whole-run view", digest_run.stdout[:300] + digest_run.stderr[:300])
no_arg = subprocess.run([sys.executable, str(WATCH), "--digest"], capture_output=True, text=True, timeout=60)
check(no_arg.returncode == 2 and "--digest" in no_arg.stderr, "--digest without a path is a usage error", no_arg.stderr)
unreadable = subprocess.run([sys.executable, str(WATCH), "--digest", str(PROGRESS_FIX / "absent.stream")],
                            capture_output=True, text=True, timeout=60)
check(unreadable.returncode == 1 and "progress unreadable" in unreadable.stdout, "--digest of a missing stream says progress unreadable", unreadable.stdout)

# The arm text lives once: ARM_COMMAND and ARM_CALL, imported by every hook and tool that tells the model to arm
import ast
check(getattr(watch_records, "ARM_COMMAND", None) == "python3 .claude/tools/watch_records.py --session --wake"
      and "run_in_background: true" in getattr(watch_records, "ARM_CALL", "") and "7200000" in getattr(watch_records, "ARM_CALL", ""),
      "watch_records defines ARM_COMMAND and ARM_CALL")
claude_dir = Path(__file__).resolve().parent.parent
copies = []
for rel in sorted(subprocess.run(["git", "-C", str(claude_dir), "ls-files", "-z", "hooks/*.py", "tools/*.py"],
                                 capture_output=True, text=True).stdout.split("\0")):
    # A tracked path another proof is moving or a peer has deleted holds no copy of the command: skip it.
    if not rel or rel == "tools/watch_records.py" or not (claude_dir / rel).is_file():
        continue
    source = (claude_dir / rel).read_text(encoding="utf-8")
    if "watch_records.py --session --wake" not in source and "timeout 7200000" not in source:
        continue
    tree = ast.parse(source)
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
                and ("watch_records.py --session --wake" in node.value or "timeout 7200000" in node.value)):
            copies.append("%s:%d" % (rel, node.lineno))
check(not copies, "no hook or tool hand-writes the watcher arm command or call shape", ", ".join(copies))

# X1 S2: the launcher's journal is the record's state. A `started` row whose stamped process the OS
# no longer matches is a launch that ended without an `ended` row: DIED, found by the journal. This is
# the phantom shape of 1bf8c58fd: no .exit, no .pid, so the ladder had no rung for it at HEAD.
with tempfile.TemporaryDirectory(prefix="journal_rung_") as temp:
    record = str(Path(temp) / "seat.record.json")
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    row = {"event": "started", "ts": now, "launchId": "journal-proof", "pid": gone.pid, "winpid": gone.pid}
    Path(record + ".journal.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    check(watch_records.record_state(record)[::2] == ("DIED", "journal"),
          "a started journal row whose process is gone is DIED, found by the journal",
          str(watch_records.record_state(record)))
    row_live = dict(row, pid=os.getpid(), winpid=os.getpid())
    Path(record + ".journal.jsonl").write_text(json.dumps(row_live) + "\n", encoding="utf-8")
    check(watch_records.record_state(record)[::2] == ("WATCH", "journal"),
          "a started journal row whose process is live is WATCH, found by the journal",
          str(watch_records.record_state(record)))
    ended = {"event": "ended", "ts": now, "code": 0}
    Path(record + ".journal.jsonl").write_text(json.dumps(row_live) + "\n" + json.dumps(ended) + "\n", encoding="utf-8")
    check(watch_records.record_state(record) == ("DONE", "0", "journal"),
          "an ended journal row is DONE with its rc, even while the started process lives",
          str(watch_records.record_state(record)))

# X1 S6: a real launch's `ended` row is the end; `.exit` is only the fallback. The fixtures are the journal
# and carriers a real launcher wrote (tests/fixtures/sidecar_journal), copied with the named suffixes dropped.
FIXTURES = ROOT / "tests" / "fixtures" / "sidecar_journal"


def real_launch(root, kind, label, drop=()):
    """The record path of a copied real launch: `kind` names its fixture folder, `label` its file prefix."""
    for src in (FIXTURES / kind).iterdir():
        if not src.name.endswith(tuple(drop)):
            Path(root, src.name).write_bytes(src.read_bytes())
    return str(Path(root, label + ".record.json"))


with tempfile.TemporaryDirectory(prefix="s6_ended_") as temp:
    record = real_launch(temp, "foreground_launch", "journaled", drop=(".exit", ".pid"))
    check(watch_records.ended_code(record) == "0",
          "a real launch's ended row is its end with no .exit: the code is read from the journal",
          str(watch_records.ended_code(record)))
    saved = watch_records._inventory, watch_records._process_line_gone
    watch_records._inventory = lambda unit: ("fanout pid=1", [{"label": "journaled", "recordPath": record}])
    watch_records._process_line_gone = lambda line: True
    try:
        orphan = watch_records.orphan_line({"fanout_dir": temp, "records": []})
    finally:
        watch_records._inventory, watch_records._process_line_gone = saved
    check(orphan is None, "a job whose real launch recorded its end is not ORPHANED", str(orphan))

# X2 S2: a standing quota wait is a wait like a memory wait: memory_wait names it, and the run's next transition ends it.
with tempfile.TemporaryDirectory(prefix="s2_quota_wait_") as temp:
    quota = str(Path(temp, "quota.record.json"))
    journal_row(quota, event="started", pid=1, winpid=1)
    journal_row(quota, event="waiting-quota", message="resets in 3600s: codex 5h window holds no room")
    check(watch_records.memory_wait(quota) == "waiting-quota: resets in 3600s: codex 5h window holds no room",
          "a standing waiting-quota row is returned with its message under the waiting-quota label",
          repr(watch_records.memory_wait(quota)))
    journal_row(quota, event="started", pid=2, winpid=2)
    check(watch_records.memory_wait(quota) is None,
          "a started row after a waiting-quota row ends the quota wait", repr(watch_records.memory_wait(quota)))

print("test_watch_records: %d passed, 0 failed" % PASS)
