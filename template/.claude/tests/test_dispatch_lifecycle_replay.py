#!/usr/bin/env python3
"""Proof: one failed fan-out launch gives one Stop report and one watcher wake (S17).

Two replays, both on planted session state under this proof's own temp root; the live routing state
and this session's dispatch table are never read or written.
  A  refused, then crashed, then relaunched into one out dir;
  B  refused and crashed in one out dir with the relaunch in a different dir (the live shape), whose
     dead launch's end row was acknowledged before the relaunch -- the shape that woke a watcher again.

Each case drives the real Stop hook (`dispatch_table.py --complete`) and the real session watcher
(`watch_records.py --session --wake`) against the same planted failure shape.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "dispatch_table.py"
WATCH = ROOT / "tools" / "watch_records.py"
SID = "s17replay-0000-0000-0000-000000000000"
PASSED = 0
# The machine's live usage-limit markers must not reach any case.
NO_MARKERS = tempfile.mkdtemp(prefix="exhausted_none_")


def check(ok, label, detail=""):
    global PASSED
    if not ok:
        raise AssertionError("%s: %s" % (label, detail))
    PASSED += 1
    print("PASS", label)


def stamp(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


def pid_line(pid=99999999):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + (
        "winpid=%d " % pid if os.name == "nt" else "") + "pid=%d" % pid


def entry(label, out_dir, call, issued, record=None, inventory=None):
    return {"kind": "sidecar", "id": "%s:%s:%s" % (out_dir, label, call), "record": record,
            "fanout_dir": str(out_dir), "inventory": inventory, "task_id": None,
            "launched_at": issued, "issued_at": issued, "jobs": [{"label": label, "launcher": "proof"}]}


def refusal(out_dir, labels, when):
    """The `<out dir>/fanout-<id>.refused.json` a fan-out writes when it refuses before any seat."""
    path = Path(out_dir) / ("fanout-%d.refused.json" % int(when * 1_000_000))
    path.write_text(json.dumps({"labels": list(labels), "refusal": "review launch refused before any seat ran",
                                "time": when}), encoding="utf-8")
    return path


def inventory(out_dir, records, written):
    """The real inventory a fan-out writes at launch: one job per label with its record path."""
    path = Path(out_dir) / "fanout-proof.inventory.json"
    path.write_text(json.dumps({"parentSessionId": SID, "fanoutId": "proof", "fanoutProcess": pid_line(),
                                "jobs": [{"label": label, "recordPath": str(record)}
                                         for label, record in records]}), encoding="utf-8")
    os.utime(path, (written, written))
    return str(path)


def relaunch(out_dir, label, issued, live):
    """A corrected launch's record: running (a live `.pid`, no record yet) or ended (its record and
    `.exit` written, as a launcher writes them)."""
    record = Path(out_dir) / (label + ".attempt2.record.json")
    Path(str(record) + ".pid").write_text(pid_line(os.getpid()) + "\n", encoding="utf-8")
    if not live:
        record.write_text("{}", encoding="utf-8")
        Path(str(record) + ".exit").write_text("0", encoding="utf-8")
    return str(record)


class Env:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="s17_"))
        self.state = self.root / "state"
        self.state.mkdir()
        self.ledger = self.root / "ledger.jsonl"
        self.ledger.write_text("", encoding="utf-8")
        self.env = dict(os.environ, HARNESS_HOOK_STATE_DIR=str(self.state),
                        SIDECAR_LEDGER_PATH=str(self.ledger), CLAUDE_PROJECT_DIR=str(self.root),
                        CLAUDE_PROJECTS_ROOT=str(self.root / "projects"),
                        BENCH_ROOTS=json.dumps({"BenchmarkArms": str(self.root / "no_arms")}),
                        SIDECAR_EXHAUSTED_DIR=NO_MARKERS,
                        DISPATCH_TABLE_LAUNCH_WAIT="0", DISPATCH_TABLE_UNRESOLVED_WAIT="0")

    def path(self):
        return self.state / (SID[:8] + ".json")

    def put(self, entries, **extra):
        self.path().write_text(json.dumps(dict({"dispatch_table_pending": entries}, **extra)),
                               encoding="utf-8")

    def entries(self):
        data = json.loads(self.path().read_text()) if self.path().exists() else {}
        return data.get("dispatch_table_pending") or []

    def row(self, label, when, code=2):
        with self.ledger.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"timestamp": stamp(when), "parentSessionId": SID, "label": label,
                                 "transport": "codex", "exitCode": code, "outcome": "ended-before-result",
                                 "reason": "the launcher exited %d before a result" % code,
                                 "costUSD": 1}) + "\n")

    def stop(self):
        payload = {"hook_event_name": "Stop", "stop_hook_active": False, "session_id": SID,
                   "effort": {"level": "medium"}, "background_tasks": []}
        run = subprocess.run([sys.executable, str(HOOK), "--complete"], input=json.dumps(payload),
                             capture_output=True, text=True, timeout=60, env=self.env, encoding="utf-8")
        check(run.returncode == 0 and "Traceback" not in run.stderr, "the Stop hook ran", run.stderr)
        return (json.loads(run.stdout) if run.stdout.strip() else {}).get("systemMessage", "")

    def watch(self):
        run = subprocess.run([sys.executable, str(WATCH), "--session", "--wake"], capture_output=True,
                             text=True, timeout=90, encoding="utf-8",
                             env=dict(self.env, CLAUDE_CODE_SESSION_ID=SID,
                                      WATCH_RECORDS_POLL_SEC="0.05", WATCH_RECORDS_CHECKIN_SEC="600"))
        check(run.returncode == 0 and not run.stderr.strip(), "the watcher exited cleanly", run.stderr)
        return run.stdout

    def watch_stays_armed(self):
        """(exited, stdout) of a session watcher given many polls after it registered, then stopped: a session
        watch exits only on a decision event, because an idle exit is itself a paid notification."""
        proc = subprocess.Popen([sys.executable, str(WATCH), "--session", "--wake"], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                env=dict(self.env, CLAUDE_CODE_SESSION_ID=SID,
                                         WATCH_RECORDS_POLL_SEC="0.05", WATCH_RECORDS_CHECKIN_SEC="600"))
        deadline = time.time() + 60
        while time.time() < deadline and proc.poll() is None:
            data = json.loads(self.path().read_text()) if self.path().exists() else {}
            if any(watcher.get("session") for watcher in data.get("sidecar_watchers") or []):
                break
            time.sleep(0.05)
        try:
            proc.wait(timeout=2)
            exited = True
        except subprocess.TimeoutExpired:
            exited = False
            proc.kill()
        return exited, proc.communicate()[0]


# ------------------------------------------------------- A: refused, crashed, relaunched in one out dir
e = Env()
now = time.time()
out = e.root / "out"
out.mkdir()
relaunch_record = relaunch(out, "plc-a", now - 100, live=True)
relaunch_inventory = inventory(out, [("plc-a", relaunch_record)], now - 50)
refusal(out, ["plc-a"], now - 790)
e.put([entry("plc-a", out, "toolu_refused", now - 800),
       entry("plc-b", out, "toolu_crashed", now - 780),
       entry("plc-a", out, "toolu_relaunch", now - 100, record=relaunch_record,
             inventory=relaunch_inventory)])
text = e.stop()
reports = [line for line in text.splitlines() if "never started" in line]
check(len(reports) == 1 and "plc-b" in reports[0] and "plc-a" not in reports[0],
      "one Stop report for the dead batch, and none for the refused call", text)
check(all("toolu_refused" not in json.dumps(item) for item in e.entries()),
      "the refused call's entry leaves the table", e.entries())
check(any("toolu_relaunch" in json.dumps(item) for item in e.entries()),
      "the corrected relaunch keeps its entry", e.entries())
Path(relaunch_record + ".exit").write_text("0", encoding="utf-8")
woke = e.watch()
check("STILL RUNNING 0" in woke and "ENDED plc-a" not in woke and "UNRESOLVED" not in woke
      and "DONE plc-a exit=0" in woke,
      "the relaunch's end is the one wake; the refused call adds none", woke)

# ------------------------------------------------------- B: the relaunch in a different out dir
e = Env()
now = time.time()
dead_dir, live_dir = e.root / "dead", e.root / "live"
dead_dir.mkdir()
live_dir.mkdir()
record_c = relaunch(live_dir, "plc-c", now - 100, live=False)
record_d = relaunch(live_dir, "plc-d", now - 100, live=False)
live_inventory = inventory(live_dir, [("plc-c", record_c), ("plc-d", record_d)], now - 50)
refusal(dead_dir, ["plc-c"], now - 790)
e.row("plc-c", now - 795)
ack_key = json.dumps([["finish", "plc-c", stamp(now - 795)], None], separators=(",", ":"))
e.put([entry("plc-c", dead_dir, "toolu_refused", now - 800),
       entry("plc-d", dead_dir, "toolu_crashed", now - 780),
       entry("plc-c", live_dir, "toolu_relaunch_c", now - 100, record=record_c, inventory=live_inventory),
       entry("plc-d", live_dir, "toolu_relaunch_d", now - 100, record=record_d, inventory=live_inventory)],
      sidecar_wake_seen=[ack_key])
woke = e.watch()
check("STILL RUNNING 0" in woke and "ENDED" not in woke and "UNRESOLVED" not in woke
      and "DONE plc-c exit=0" in woke and "DONE plc-d exit=0" in woke,
      "one wake for the relaunch's real ends; neither dead call's entry counts", woke)
exited, again = e.watch_stays_armed()
check(not exited and not again.strip(),
      "a second watcher on the same state stays armed and silent: no dead entry re-wakes, no acknowledged end returns",
      again)
text = e.stop()
check("never started" not in (text or ""),
      "Stop drops a dead call in either out dir: a later-issued same-label run with a record supersedes",
      text)
check(all("toolu_refused" not in json.dumps(item) and "toolu_crashed" not in json.dumps(item)
          for item in e.entries()),
      "neither dead call's entry is left in the table", e.entries())

print("test_dispatch_lifecycle_replay: %d passed, 0 failed" % PASSED)
