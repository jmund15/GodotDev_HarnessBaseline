"""agent_progress.read: a Workflow seat's assistant rows count, and only heartbeat rows are skipped.

A seat row's usage carries `output_tokens_details.thinking_tokens`; the heartbeat is
`{"type":"system","subtype":"thinking_tokens",...}`. The rows below keep the live shapes' keys.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import agent_progress  # noqa: E402


def seat_call(n, ts):
    return {"parentUuid": "p%d" % n, "isSidechain": True, "type": "assistant", "timestamp": ts,
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_%d" % n, "name": "Bash",
                                                          "input": {"command": "git status", "description": "status"}}],
                        "usage": {"input_tokens": 3, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 18914,
                                  "output_tokens": 90, "output_tokens_details": {"thinking_tokens": 35}}}}


def seat_result(n, ts):
    return {"type": "user", "timestamp": ts,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_%d" % n, "content": "ok"}]}}


HEARTBEAT = {"type": "system", "subtype": "thinking_tokens", "session_id": "S"}


class ReadSeat(unittest.TestCase):
    def read(self, rows, compact=True):
        fd, path = tempfile.mkstemp(suffix=".jsonl", dir=os.environ.get("TMP"))
        sep = (",", ":") if compact else (", ", ": ")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("".join(json.dumps(r, separators=sep) + "\n" for r in rows))
        self.addCleanup(os.remove, path)
        return agent_progress.read(path)

    def test_seat_rows_with_thinking_usage_count_as_calls(self):
        rows = [seat_call(1, "2026-10-29T15:23:53.341Z"), seat_result(1, "2026-10-29T15:23:54.000Z"),
                seat_call(2, "2026-10-29T15:24:10.000Z"), seat_result(2, "2026-10-29T15:24:11.000Z")]
        for compact in (True, False):
            progress = self.read(rows, compact)
            self.assertEqual(len(progress.calls), 2)
            self.assertEqual(len(progress.contexts), 2)

    def test_heartbeat_rows_are_skipped(self):
        for compact in (True, False):
            progress = self.read([seat_call(1, "2026-10-29T15:23:53.341Z"), HEARTBEAT, HEARTBEAT], compact)
            self.assertEqual(progress.rows, 1)
            self.assertEqual(len(progress.calls), 1)


if __name__ == "__main__":
    unittest.main()
