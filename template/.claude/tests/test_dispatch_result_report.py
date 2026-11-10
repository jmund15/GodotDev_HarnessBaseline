"""Re-runnable proof: a job's delivery is the report it wrote, and a trailing reply never replaces it.

The stream is a trimmed cut of a real job (drive-e9), planted here so the proof runs without the
never-ship fixture tree: a report message, a Stop-hook feedback turn, then a short reply to it.
Without a named report the reply is the final message, so the capture flags it; with the report
written to `<label>.md` the report stays the delivery and the reply is kept apart as
`<label>.reply.md`.

    python3 .claude/tests/test_dispatch_result_report.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import dispatch_result  # noqa: E402

REPORT = "the job's complete report\n"
REPLY = "No more work needed: that denial was already handled."
STREAM = [
    {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "The gate failure is fixed and committed; every named class is green.\n"}]}},
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "text", "text": "Stop hook feedback:\nA denial followed that report."}]}},
    {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": REPLY}]}},
    {"type": "result", "result": REPLY},
]
STREAM_TEXT = "\n".join(json.dumps(row) for row in STREAM)


class Delivery(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="dr_report_"))
        self.out = self.dir / "job.out.json"
        self.out.write_text(STREAM_TEXT, encoding="utf-8")
        self.since = time.time() - 5

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_written_report_stays_the_delivery(self):
        (self.dir / "job.md").write_text(REPORT, encoding="utf-8")
        got = dispatch_result.capture_delivery(self.dir, "job", self.out, self.since)
        self.assertEqual((self.dir / "job.md").read_text(encoding="utf-8"), REPORT)
        self.assertEqual(got["path"], self.dir / "job.md")
        self.assertIsNone(got["flag"])
        reply = Path(got["reply"])
        self.assertEqual(reply.name, "job.reply.md")
        self.assertTrue(reply.read_text(encoding="utf-8").startswith("No more work needed"))

    def test_no_report_keeps_the_final_message_and_flags_it(self):
        got = dispatch_result.capture_delivery(self.dir, "job", self.out, self.since)
        self.assertTrue((self.dir / "job.md").read_text(encoding="utf-8").startswith("No more work needed"))
        self.assertIsNone(got["reply"])
        self.assertIn("trailing reply", got["flag"])

    def test_a_report_older_than_the_run_is_not_this_jobs(self):
        stale = self.dir / "job.md"
        stale.write_text(REPORT, encoding="utf-8")
        os.utime(stale, (self.since - 3600, self.since - 3600))
        got = dispatch_result.capture_delivery(self.dir, "job", self.out, self.since)
        self.assertTrue(stale.read_text(encoding="utf-8").startswith("No more work needed"))
        self.assertIsNotNone(got["flag"])

    def test_single_turn_stream_is_not_flagged(self):
        single = [STREAM[0], {"result": "one answer"}]
        self.out.write_text("\n".join(json.dumps(r) for r in single), encoding="utf-8")
        got = dispatch_result.capture_delivery(self.dir, "job", self.out, self.since)
        self.assertIsNone(got["flag"])

    def test_text_then_tool_result_is_not_a_new_turn(self):
        tool_result = {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}
        stream = [STREAM[0], tool_result, STREAM[2], STREAM[3]]
        self.assertFalse(dispatch_result.answered_after_report("\n".join(json.dumps(r) for r in stream)))

    def test_stream_shape_is_flagged(self):
        self.assertTrue(dispatch_result.answered_after_report(STREAM_TEXT))


if __name__ == "__main__":
    unittest.main()
