"""Synthetic channel and response-shape proof for the global Read advisory."""
import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("large_read_reminder", Path(__file__).parents[1] / "user_scope/hooks/large_read_reminder.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class LargeReadTests(unittest.TestCase):
    def run_hook(self, event):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(hook.sys, "stdin", io.StringIO(json.dumps(event))), redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(0, hook.main())
        self.assertEqual("", err.getvalue())
        return json.loads(out.getvalue()) if out.getvalue() else None

    def test_large_text_reaches_model_channel(self):
        result = self.run_hook({"tool_name": "Read", "tool_response": "\n".join(["line"] * 401)})
        self.assertIsNotNone(result)
        output = result["hookSpecificOutput"]
        self.assertEqual("PostToolUse", output["hookEventName"])
        self.assertIn("read_files", output["additionalContext"])
        self.assertIn("bounded answer", output["additionalContext"])
        self.assertNotIn("ask_model", output["additionalContext"])

    def test_actual_returned_lines_not_total_file_lines(self):
        self.assertIsNone(self.run_hook({"tool_name": "Read", "tool_response": {
            "type": "text", "file": {"numLines": 20, "totalLines": 5000, "content": "line\n" * 20}}}))
        self.assertIsNotNone(self.run_hook({"tool_name": "Read", "tool_response": {
            "type": "text", "file": {"numLines": 401, "totalLines": 5000}}}))

    def test_text_block_shape(self):
        self.assertIsNotNone(self.run_hook({"tool_name": "Read", "tool_response": {
            "content": [{"type": "text", "text": "line\n" * 401}]}}))

    def test_threshold_is_strict(self):
        self.assertIsNone(self.run_hook({"tool_name": "Read", "tool_response": "line\n" * 400}))

    def test_wrong_tool_and_unknown_payload_are_silent(self):
        for event in ([], None, {"tool_name": "Bash", "tool_response": "line\n" * 401},
                      {"tool_name": "Read", "tool_response": {"type": "image", "file": {"numLines": True}}}):
            with self.subTest(event_type=type(event).__name__):
                self.assertIsNone(self.run_hook(event))

    def test_malformed_input_fails_open(self):
        with patch.object(hook.sys, "stdin", io.StringIO("{bad")), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(0, hook.main())
        self.assertEqual("", out.getvalue())


if __name__ == "__main__":
    unittest.main()
