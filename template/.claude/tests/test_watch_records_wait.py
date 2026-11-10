"""Proof of what a session watcher costs while it waits and how a watcher nobody can hear from ends.

Two subjects, one file, both on the real watcher:

1. The ledger read. Every poll reads the session's spend and finish rows out of the shared spend ledger.
   The ledger is append-only and, on this machine, 34 MB over 16k rows for every session's launches, so a
   poll that re-read it cost 0.25 to 0.53 s of CPU every 0.5 s (measured 2026-11-10). The reader keeps a
   saved offset and parses only what was appended; a truncated or replaced file restarts from zero, and a
   half-written last line waits for its newline.

2. Lifetime. A session watcher exists to deliver a wake into its session's background shell. When the
   session host is gone, or the shell that carries its stdout is gone, nothing can receive that wake, and
   the watcher must end instead of polling forever. It must NOT end when the host cannot be observed (a
   proof fixture, or an old client that writes no session registry row).

Waits here are for observable events (a line in the watcher's output, an exit), never elapsed time.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
WATCH = Path(os.environ.get("WATCH_RECORDS_UNDER_TEST", ROOT / "tools" / "watch_records.py"))
SID = "waitproof-0000-0000-0000-000000000000"
PASS = 0
HANG_BOUND = int(os.environ.get("WAIT_PROOF_BOUND", "120"))
sys.path.insert(0, str(ROOT / "hooks"))
sys.path.insert(0, str(ROOT / "tools"))
import _hook_state  # noqa: E402
import watch_records as w  # noqa: E402

BASE = Path(tempfile.mkdtemp(prefix="wait_proof_", dir=os.environ.get("TMP")))
# The session's transcript root is this proof's own empty login: the machine's real store is 4513 project
# dirs and a live peer's transcript could answer for a made-up session.
os.environ["CLAUDE_CONFIG_DIR"] = str(BASE / "login")
(BASE / "login" / "projects").mkdir(parents=True)
# Every watcher this file arms checks its lifetime far more often than the shipped 10 s.
HOST_SEC = "0.1"


def check(condition, label, detail=""):
    global PASS
    if not condition:
        raise AssertionError("%s: %s" % (label, detail))
    PASS += 1
    print("PASS", label)


def until(predicate, seconds=HANG_BOUND, step=0.02):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


LEDGER = BASE / "ledger.jsonl"
# Every watcher this file arms reads this ledger, not the machine's 34 MB one: its size is what a poll costs.
os.environ["SIDECAR_LEDGER_PATH"] = str(LEDGER)


def ledger_cases():
    def row(session, **extra):
        return dict({"timestamp": "2026-11-10T00:00:00Z", "parentSessionId": session, "label": "l",
                     "exitCode": 0, "costUSD": 0}, **extra)


    def append(*rows, partial=False):
        text = "".join(json.dumps(r) + "\n" for r in rows)
        if partial:
            text = text.rstrip("\n")
        with open(LEDGER, "a", encoding="utf-8") as fh:
            fh.write(text)


    def finish_and_clean():
        return w.finish_rows(SID), w.clean_launch_rows(SID)


    # A first read of a ledger this session has no rows in must not poison the later reads.
    LEDGER.write_text("", encoding="utf-8")
    check(finish_and_clean() == ([], []), "an empty ledger yields no rows")
    append(row(SID, costUSD=1))
    finish, clean = finish_and_clean()
    check(len(clean) == 1 and clean[0]["costUSD"] == 1 and finish == [],
          "a clean launch row appended after the first read is seen on the next read", str(clean))
    append(row(SID, outcome="ended-before-result", exitCode=1), row("someone-else", costUSD=2))
    finish, clean = finish_and_clean()
    check(len(finish) == 1 and finish[0]["outcome"] == "ended-before-result",
          "a finish row appended after the clean one is seen too, and another session's row is not", str(finish))
    check(len(clean) == 1, "another session's spend row never arrives in this session's rows", str(clean))

    # A poll that finds nothing new parses nothing: this is the property that makes an idle wait cheap.
    import unittest.mock as mock  # noqa: E402
    with mock.patch.object(w.json, "loads", wraps=json.loads) as loads:
        finish_and_clean()
    check(loads.call_count == 0, "a read of an unchanged ledger parses no row",
          "%d rows parsed" % loads.call_count)

    # A half-written last line is not a row until its newline lands.
    append(row(SID, costUSD=3), partial=True)
    check(len(finish_and_clean()[1]) == 1, "a partial last line is not read as a row")
    with open(LEDGER, "a", encoding="utf-8") as fh:
        fh.write("\n")
    check(len(finish_and_clean()[1]) == 2, "the same row is read once its newline lands")

    # A truncated or replaced ledger restarts from zero instead of resume-reading a dead offset.
    LEDGER.write_text(json.dumps(row(SID, costUSD=4)) + "\n", encoding="utf-8")
    finish, clean = finish_and_clean()
    check(len(clean) == 1 and clean[0]["costUSD"] == 4,
          "a ledger that shrank is read again from the start", str(clean))


def lifetime_cases():
    def session_root(name):
        root = BASE / name
        (root / "state").mkdir(parents=True)
        return root

    def arm(root, *, sid=SID, extra=None):
        env = dict(os.environ, CLAUDE_CODE_SESSION_ID=sid, HARNESS_HOOK_STATE_DIR=str(root / "state"),
                   WATCH_RECORDS_POLL_SEC="0.05", WATCH_RECORDS_HOST_SEC=HOST_SEC,
                   CLAUDE_PROJECT_DIR=str(root),
                   BENCH_ROOTS=json.dumps({"BenchmarkArms": str(root / "no_arms")}))
        env.update(extra or {})
        out = root / "watcher.out"
        return subprocess.Popen([sys.executable, str(WATCH), "--session", "--wake"],
                                stdout=open(out, "w"), stderr=open(root / "watcher.err", "w"),
                                text=True, env=env), out


    def sleeper():
        return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


    def row_for(pid, sid=SID):
        """A session-registry row as this client writes it: the host's pid, its session, its creation time."""
        started = w._process_started(pid)
        stamp = int((started + 11644473600) * 1e7) if started else None
        return {"pid": pid, "sessionId": sid, "procStart": str(stamp) if stamp else None,
                "startedAt": int((started or time.time()) * 1000)}


    def plant_host(pid, sid=SID, login=None):
        folder = Path(login or (BASE / "login"), "sessions")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ("%d.json" % pid)).write_text(json.dumps(row_for(pid, sid)), encoding="utf-8")


    def text_of(path):
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

    def killed(process):
        process.kill()
        process.wait()


    # A host row naming a live process keeps the watcher armed: two polls with no decision event prove it.
    host = sleeper()
    plant_host(host.pid)
    root = session_root("live")
    proc, out = arm(root)
    time.sleep(1.0)
    check(proc.poll() is None, "a watcher whose session host is alive stays armed")
    check("HOST GONE" not in text_of(out), "a live host prints no HOST GONE line", text_of(out)[-200:])

    # The host dies: nothing can receive a wake any more, so the watcher ends and says why.
    killed(host)
    check(until(lambda: proc.poll() is not None, seconds=HANG_BOUND),
          "a watcher whose session host has exited ends by itself")
    check("HOST GONE" in text_of(out), "the ending watcher names the gone host", text_of(out)[-300:])
    check(proc.returncode == 0, "a watcher that ends on a gone host is not a failure", str(proc.returncode))

    # No registry row at all (an old client, or a proof fixture): the host cannot be observed, so the watcher
    # keeps the old behavior. Ending here would kill a live session's watcher on any client that writes none.
    root = session_root("unknown")
    proc, out = arm(root)
    time.sleep(1.0)
    check(proc.poll() is None, "a watcher whose host cannot be observed stays armed")
    killed(proc)

    # The shell that carries the watcher's stdout is its receiver too: when it is gone, so is the wake.
    root = session_root("orphan")
    out = root / "watcher.out"
    mid = subprocess.Popen([sys.executable, "-c",
                            "import subprocess,sys;"
                            "subprocess.Popen(sys.argv[2:], stdout=open(sys.argv[1], 'w'))",
                            str(out), sys.executable, str(WATCH), "--session", "--wake"],
                           env=dict(os.environ, CLAUDE_CODE_SESSION_ID="parentgone-0000-0000-0000-000000000000",
                                    HARNESS_HOOK_STATE_DIR=str(root / "state"), WATCH_RECORDS_POLL_SEC="0.05",
                                    WATCH_RECORDS_HOST_SEC=HOST_SEC, CLAUDE_PROJECT_DIR=str(root)))
    mid.wait(timeout=HANG_BOUND)
    check(until(lambda: "HOST GONE" in text_of(out), seconds=HANG_BOUND),
          "a watcher whose parent shell is gone ends by itself", text_of(out)[-300:])


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("ledger", "all"):
        ledger_cases()
    if which in ("life", "all"):
        lifetime_cases()
    print("test_watch_records_wait: %d passed, 0 failed" % PASS)
