"""wake_digest.shell_notices never reports a watcher's shell as an ended shell, whatever its output's last line says."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import wake_digest  # noqa: E402
import watch_records  # noqa: E402

WATCH = "python3 .claude/tools/watch_records.py --session --wake"


def rows(use_id, task, command, out_file):
    use = {"type": "assistant", "isSidechain": False, "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": use_id, "name": "Bash", "input": {"command": command}}]}}
    res = {"type": "user", "isSidechain": False, "timestamp": "2026-11-01T23:53:43.276Z",
           "message": {"role": "user", "content": [{"tool_use_id": use_id, "type": "tool_result",
                                                    "content": "Command running in background with ID: " + task, "is_error": False}]},
           "toolUseResult": {"stdout": "", "stderr": "", "backgroundTaskId": task}}
    note = {"type": "queue-operation", "operation": "enqueue", "content": (
        "<task-notification>\n<task-id>%s</task-id>\n<tool-use-id>%s</tool-use-id>\n<output-file>%s</output-file>\n"
        "<status>completed</status>\n<summary>Background command \"x\" completed (exit code 0)</summary>\n"
        "</task-notification>" % (task, use_id, out_file))}
    return "".join(json.dumps(r) + "\n" for r in (use, res, note))


class WatcherShell(unittest.TestCase):
    def notices(self, command, output):
        with tempfile.TemporaryDirectory() as tmp:
            out_file = os.path.join(tmp, "t.output")
            with open(out_file, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(output)
            transcript = os.path.join(tmp, "s.jsonl")
            with open(transcript, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(rows("toolu_1", "btask1", command, out_file))
            found, _ = wake_digest.shell_notices(transcript, wake_digest.new_shell_scan(), 0)
            return [n["task_id"] for n in found]

    def test_a_watcher_that_woke_on_a_done_report_is_omitted(self):
        self.assertEqual(self.notices(WATCH, "DONE job-a\nsome digest line\n"), [])

    def test_an_ordinary_shell_is_reported(self):
        self.assertEqual(self.notices("npm run build", "built\n"), ["btask1"])

    def test_an_ordinary_shell_ending_in_a_done_line_is_reported(self):
        self.assertEqual(self.notices("make", "DONE job-a\n"), ["btask1"])


# Trimmed from session f3a34186's bqcuah9lv enqueue and delivered attachment.
CAPTURED_NOTICE = (
    '<task-notification>\n<task-id>bqcuah9lv</task-id>\n'
    '<tool-use-id>toolu_01Pr2DwGTywhD76H56rJ57Qf</tool-use-id>\n'
    '<status>completed</status>\n'
    '<summary>Background command "Price this session and its delegated runs" completed (exit code 0)</summary>\n'
    '</task-notification>')
CAPTURED_END = {"type": "queue-operation", "operation": "enqueue", "content": CAPTURED_NOTICE}
CAPTURED_DELIVERY = {"type": "attachment", "isSidechain": False, "attachment": {
    "type": "queued_command", "prompt": CAPTURED_NOTICE, "commandMode": "task-notification",
    "origin": {"kind": "task-notification", "producer": "session-task"}}}


class DeliveredShell(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.transcript = os.path.join(self.tmp.name, "s.jsonl")
        self.state = os.path.join(self.tmp.name, "state.json")
        with open(self.state, "w", encoding="utf-8") as fh:
            json.dump({watch_records.SHELL_OFFSET_KEY: 0}, fh)
        self.append(CAPTURED_END)
        self.shells = watch_records.ShellEnds("session", self.state)
        self.shells.path = self.transcript

    def append(self, row):
        with open(self.transcript, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row) + "\n")

    def source(self):
        return {"shell:" + task: {"shell": notice, "records": [], "fanout_dir": None}
                for task, notice in self.shells.poll().items()}

    def test_delivered_end_keeps_watcher_idle_instead_of_waking(self):
        self.append(CAPTURED_DELIVERY)
        source = lambda: self.source()
        source.shells = self.shells
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(watch_records.time, "sleep", side_effect=InterruptedError):
            with self.assertRaises(InterruptedError):
                watch_records.wake_loop(source)
        self.assertEqual(output.getvalue(), "")

    def test_undelivered_end_still_exits_with_shell_digest(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(watch_records.wake_loop(self.source), 0)
        self.assertIn("SHELL bqcuah9lv completed exit=0", output.getvalue())
        self.assertTrue(output.getvalue().endswith("NONE\n"))

    def test_delivery_after_poll_removes_retained_end(self):
        self.assertIn("bqcuah9lv", self.shells.poll())
        self.append(CAPTURED_DELIVERY)
        self.assertEqual(self.shells.poll(), {})

    def test_delivery_drops_deferred_digest_and_watcher_can_wake_for_new_end(self):
        polls = 0

        def source():
            nonlocal polls
            polls += 1
            if polls == 1:
                return {"live": {"running_shell": "other", "records": [], "fanout_dir": None}}
            if polls == 3:
                self.append(CAPTURED_DELIVERY)
            if polls == 4:
                self.append({"type": "queue-operation", "operation": "enqueue", "content":
                             CAPTURED_NOTICE.replace("bqcuah9lv", "newtask")})
            grouped = self.source()
            if polls < 3:
                grouped["live"] = {"running_shell": "other", "records": [], "fanout_dir": None}
            if polls == 4:
                record = os.path.join(self.tmp.name, "failed.record.json")
                with open(record + ".exit", "w", encoding="utf-8") as fh:
                    fh.write("1")
                grouped.update(watch_records.units([{"id": "failed", "record": record, "call": "launch"}]))
            return grouped

        source.shells = self.shells
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(watch_records.time, "sleep"):
            self.assertEqual(watch_records.wake_loop(source), 0)
        self.assertEqual(polls, 4)
        self.assertNotIn("SHELL bqcuah9lv", output.getvalue())
        self.assertIn("SHELL newtask", output.getvalue())

    def test_session_watcher_stays_idle_without_a_decision_event(self):
        cases = ({}, {"workflow": {"workflow": {"id": "finished"}, "records": [], "fanout_dir": None}})
        for grouped in cases:
            with self.subTest(grouped=grouped):
                source = lambda: grouped
                source.shells = self.shells
                output = io.StringIO()
                with contextlib.redirect_stdout(output), \
                        patch.object(watch_records, "workflow_running", return_value=False), \
                        patch.object(watch_records, "workflow_end_text", return_value="DONE workflow finished"), \
                        patch.object(watch_records.time, "sleep", side_effect=InterruptedError):
                    with self.assertRaises(InterruptedError):
                        watch_records.wake_loop(source)
                self.assertNotIn("NONE", output.getvalue())

    def test_session_watcher_waits_after_live_work_disappears(self):
        source = lambda: {}
        source.shells = self.shells
        with patch.object(watch_records, "_wake", side_effect=[([], 1, set()), ([], 0, set())]), \
                patch.object(watch_records.time, "sleep", side_effect=[None, InterruptedError]), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(InterruptedError):
                watch_records.wake_loop(source)
        self.assertEqual(output.getvalue(), "")

    def test_racing_duplicate_arm_waits_then_takes_over(self):
        with patch.dict(os.environ, CLAUDE_CODE_SESSION_ID="session"), \
                patch.object(watch_records, "registration", side_effect=[("state", "duplicate", "owner"),
                                                                        ("state", "duplicate", None)]) as register, \
                patch.object(watch_records.time, "sleep") as sleep, \
                patch.object(watch_records, "_session_source", return_value=lambda: {}), \
                patch.object(watch_records, "wake_loop", return_value=0) as loop, \
                patch.object(watch_records, "deregister"), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(watch_records.main(["--session", "--wake"]), 0)
        self.assertEqual(register.call_count, 2)
        sleep.assert_called_once()
        loop.assert_called_once()
        self.assertEqual(output.getvalue(), "")

    def test_no_end_and_no_work_still_exits_none_for_explicit_watch(self):
        with open(self.transcript, "w", encoding="utf-8"):
            pass
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(watch_records.wake_loop(self.source), 0)
        self.assertEqual(output.getvalue(), "NONE\n")

    def test_sidechain_delivery_does_not_hide_main_session_end(self):
        self.append(dict(CAPTURED_DELIVERY, isSidechain=True))
        self.assertIn("bqcuah9lv", self.shells.poll())

    def test_queue_remove_is_not_proof_of_delivery(self):
        self.append(dict(CAPTURED_END, operation="remove", reason="absorbed_mid_turn"))
        self.assertIn("bqcuah9lv", self.shells.poll())


if __name__ == "__main__":
    unittest.main()
