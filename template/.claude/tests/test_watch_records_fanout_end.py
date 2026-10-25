"""A fan-out job's end and a refused call's leftover entry reach the session watcher truthfully.

Cut from the captured session 9b60b8a7 (2026-10-25): its hook state, ledger row and fan-out directories, with
paths, labels and times moved into a planted session.

  * A fan-out call was refused by `--detach`'s doctrine check and re-issued twelve seconds later. The refused
    call's pending entry never allocated a record, so the watcher woke the session with `UNRESOLVED <label>`
    for a job that was running under the second call's entry.
  * Another fan-out's launcher exited 2 five seconds after its call. The Stop hook consumed its pending entry
    (telling the user, not the model), so a watcher armed afterwards had no entry and printed nothing; the
    spend ledger's `ended-before-result` row was the only trace.

    python3 .claude/tests/test_watch_records_fanout_end.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "tools")
SID = "9b60b8a7-97fc-4406-b403-639453559942"
LIVE, ENDED = "derive-live", "derive-ended"
LAUNCH = "72abcd6d-439f-45d1-9d5a-aeebb937980e"
ENDED_REASON = "the launcher exited 2 before a result"


def stamp(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


PROCESS_STARTED = time.time()


def alive():
    """A process line the watcher reads as live: this test process, stamped after it started (a stamp earlier than
    the process's creation reads as a reused PID)."""
    return "%s %spid=%d" % (stamp(PROCESS_STARTED), "winpid=%d " % os.getpid() if os.name == "nt" else "", os.getpid())


class Session:
    """A planted login (`<config>/projects/p/<SID>.jsonl`), an empty hook state, a ledger and a fan-out directory."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="wr_fanout_end_")
        self.config = os.path.join(self.root, "config")
        project = os.path.join(self.config, "projects", "p")
        self.state = os.path.join(self.root, "state")
        self.ledger_path = os.path.join(self.root, "ledger.jsonl")
        self.dir = os.path.join(self.root, "fanout")
        for path in (project, self.state, self.dir):
            os.makedirs(path)
        with open(os.path.join(project, SID + ".jsonl"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"timestamp": stamp(time.time() - 3600)}) + "\n")
        self.now = time.time()

    def record(self, label):
        return os.path.join(self.dir, label + ".record.json")

    def entry(self, label, issued, record=None, **extra):
        """A pending fan-out entry as the launch hook registers it (the captured entry's keys)."""
        entry = {"kind": "sidecar", "id": "%s:%s:toolu_%d" % (self.dir, label, int(issued)), "record": record,
                 "fanout_dir": self.dir, "task_id": None, "launched_at": issued + 7.0,
                 "jobs": [{"label": label, "model": "alias-a", "effort": "high", "launcher": "anthropic",
                           "record": record, "fanout_dir": None, "kind": "sidecar"}],
                 "call": "toolu_%d" % int(issued), "writes": False, "issued_at": issued}
        entry.update(extra)
        return entry

    def inventory(self, name, label, written, with_process=True):
        """The fan-out inventory the driver writes once, listing the job's record (the captured one, trimmed)."""
        path = os.path.join(self.dir, "fanout-%s.inventory.json" % name)
        body = {"schemaVersion": 1, "fanoutId": name, "parentSessionId": SID, "afterSeats": False,
                "jobs": [{"label": label, "launchId": LAUNCH, "recordPath": self.record(label),
                          "launcher": "anthropic", "workdir": self.root}]}
        if with_process:
            body["fanoutProcess"] = alive()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(body, fh)
        os.utime(path, (written, written))
        return path

    def running(self, label, launched):
        """A launched, unfinished job: the 0-byte record the launcher allocates and a `.pid` whose last line is live."""
        record = self.record(label)
        open(record, "w").close()
        with open(record + ".pid", "w", encoding="utf-8") as fh:
            fh.write("%s launch=%s winpid=0 pid=0\n%s launch=%s %s\n" % (
                stamp(launched), LAUNCH, stamp(PROCESS_STARTED), LAUNCH, alive().split(" ", 1)[1]))
        return record

    def ended(self, label, code, with_json=False):
        record = self.record(label)
        with open(record + ".exit", "w", encoding="utf-8") as fh:
            fh.write(str(code))
        if with_json:
            with open(record, "w", encoding="utf-8") as fh:
                json.dump({"label": label, "exitCode": code}, fh)
        return record

    def pending(self, entries):
        """Replace only the pending list, as the Stop hook does: the watcher's acknowledgments stay."""
        path = os.path.join(self.state, SID[:8] + ".json")
        try:
            with open(path, encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, ValueError):
            state = {}
        state["dispatch_table_pending"] = entries
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(state, fh)

    def ledger(self, *rows):
        with open(self.ledger_path, "w", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def finish_row(self, label, at, outcome="ended-before-result", code=2, session=SID):
        return {"timestamp": stamp(at), "parentSessionId": session, "label": label, "transport": "anthropic",
                "exitCode": code, "outcome": outcome, "reason": ENDED_REASON, "costUSD": 0}

    def watch(self):
        env = dict(os.environ, CLAUDE_CODE_SESSION_ID=SID, CLAUDE_CONFIG_DIR=self.config, HARNESS_HOOK_STATE_DIR=self.state,
                   SIDECAR_LEDGER_PATH=self.ledger_path, WATCH_RECORDS_POLL_SEC="0.1", PYTHONIOENCODING="utf-8")
        return subprocess.Popen([sys.executable, os.path.join(TOOLS, "watch_records.py"), "--session", "--wake"],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def outcome(proc, seconds):
    """(text, exited): the watcher's stdout when it exits within `seconds`, else what it printed before it was stopped."""
    try:
        out, _err = proc.communicate(timeout=seconds)
        return out.strip(), True
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _err = proc.communicate()
        return out.strip(), False


class FanoutEnd(unittest.TestCase):
    def setUp(self):
        self.s = Session()
        self.addCleanup(self.s.close)

    def refused_then_relaunched(self):
        """Pending as captured: the refused call's entry (no record) and the re-issued call's, running."""
        now = self.s.now
        self.s.running(LIVE, now - 470)
        self.s.inventory("72abcd6d-439f-45d1-9d5a-aeebb937980e", LIVE, now - 470, with_process=False)
        inventory = self.s.inventory("eb581e94-7e51-478f-9937-6a6eca3ca7c3", LIVE, now - 465)
        refused = self.s.entry(LIVE, now - 500)
        relaunch = self.s.entry(LIVE, now - 488, self.s.record(LIVE), inventory=inventory)
        return refused, relaunch

    def test_refused_call_leftover_does_not_wake_unresolved_for_the_live_relaunch(self):
        refused, relaunch = self.refused_then_relaunched()
        self.s.pending([refused, relaunch])
        text, exited = outcome(self.s.watch(), 4)
        self.assertNotIn("UNRESOLVED", text)
        self.assertFalse(exited, "the watcher must keep watching the running job, printed: %r" % text)

    def test_unresolved_still_fires_when_no_later_call_launched_the_job(self):
        self.s.pending([self.s.entry(LIVE, self.s.now - 500)])
        text, exited = outcome(self.s.watch(), 6)
        self.assertIn("UNRESOLVED " + LIVE, text)
        self.assertTrue(exited)

    def test_a_later_call_that_also_allocated_no_record_does_not_hide_it(self):
        now = self.s.now
        self.s.pending([self.s.entry(LIVE, now - 500), self.s.entry(LIVE, now - 488)])
        text, exited = outcome(self.s.watch(), 6)
        self.assertIn("UNRESOLVED " + LIVE, text)
        self.assertTrue(exited)

    def test_live_job_whose_record_json_is_empty_reads_as_running(self):
        now = self.s.now
        record = self.s.running(LIVE, now - 470)
        self.assertEqual(os.path.getsize(record), 0)
        inventory = self.s.inventory("eb581e94-7e51-478f-9937-6a6eca3ca7c3", LIVE, now - 465)
        self.s.pending([self.s.entry(LIVE, now - 488, record, inventory=inventory)])
        text, exited = outcome(self.s.watch(), 4)
        self.assertFalse(exited, "an empty record JSON is a running job, printed: %r" % text)
        for word in ("UNRESOLVED", "DIED", "DONE", "NO-IDENTITY", "ENDED"):
            self.assertNotIn(word, text)

    def test_end_the_stop_hook_consumed_is_reported_once_from_the_ledger(self):
        now = self.s.now
        self.s.pending([])
        self.s.ledger(self.s.finish_row(ENDED, now - 300))
        text, exited = outcome(self.s.watch(), 8)
        self.assertTrue(exited, "an unreported failed end wakes the session")
        self.assertIn("ENDED %s exit=2 %s" % (ENDED, ENDED_REASON), text)
        again, exited = outcome(self.s.watch(), 8)
        self.assertTrue(exited)
        self.assertNotIn("ENDED", again)
        self.assertEqual(again.splitlines()[-1], "NONE")

    def test_end_reported_through_its_entry_is_not_reported_again_after_the_entry_is_consumed(self):
        now = self.s.now
        inventory = self.s.inventory("7b162c1c-72f9-4c74-9d23-226c36cd4721", ENDED, now - 290)
        with open(self.s.record(ENDED) + ".pid", "w", encoding="utf-8") as fh:
            fh.write("%s launch=%s winpid=1 pid=1\n" % (stamp(now - 280), LAUNCH))
        self.s.ended(ENDED, 2)
        entry = self.s.entry(ENDED, now - 300, self.s.record(ENDED), inventory=inventory)
        self.s.pending([entry])
        self.s.ledger(self.s.finish_row(ENDED, now - 270))
        text, exited = outcome(self.s.watch(), 8)
        self.assertTrue(exited)
        self.assertIn("ENDED %s exit=2" % ENDED, text)
        self.s.pending([])
        again, exited = outcome(self.s.watch(), 8)
        self.assertTrue(exited)
        self.assertNotIn("ENDED", again)

    def test_clean_old_and_foreign_rows_report_nothing(self):
        now = self.s.now
        self.s.pending([])
        self.s.ledger(self.s.finish_row("old-ended", now - 25 * 3600),
                      self.s.finish_row("clean", now - 60, outcome="finished-without-record", code=0),
                      self.s.finish_row("someone-elses", now - 60, session="00000000-0000-0000-0000-000000000000"))
        text, exited = outcome(self.s.watch(), 8)
        self.assertTrue(exited)
        self.assertEqual(text, "NONE")

    def test_a_job_that_ended_nonzero_after_the_watcher_armed_wakes_with_its_exit(self):
        now = self.s.now
        inventory = self.s.inventory("7b162c1c-72f9-4c74-9d23-226c36cd4721", ENDED, now - 100)
        record = self.s.running(ENDED, now - 90)
        self.s.pending([self.s.entry(ENDED, now - 120, record, inventory=inventory)])
        proc = self.s.watch()
        time.sleep(2.0)
        self.assertIsNone(proc.poll(), "a running job keeps the watcher alive")
        self.s.ended(ENDED, 1, with_json=True)
        text, exited = outcome(proc, 8)
        self.assertTrue(exited)
        self.assertIn("DONE %s exit=1" % ENDED, text)


if __name__ == "__main__":
    unittest.main()
