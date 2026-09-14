#!/usr/bin/env python3
"""Streaming context accounting: identity, missing data, boundaries and payload units."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from _transcript_summary import TranscriptSummaryBuilder


def assistant(ident, usage=None, **extra):
    message = {"id": ident, "model": "test-model", "content": []}
    if usage is not None:
        message["usage"] = usage
    return {"type": "assistant", "timestamp": "2026-09-11T00:00:00Z", "message": message, **extra}


def boundary(ident):
    return {"type": "system", "subtype": "compact_boundary", "uuid": ident,
            "compactMetadata": {"trigger": "auto", "preTokens": 180000, "postTokens": 32000, "durationMs": 400}}


def census(*rows):
    builder = TranscriptSummaryBuilder("session", "test.jsonl")
    for row in rows:
        builder.process_line(row if isinstance(row, str) else json.dumps(row))
    return builder.finalize()["context_census"]


class ContextTests(unittest.TestCase):
    def test_streaming_usage_replaces_observations_not_sums(self):
        got = census(assistant("m", {"input_tokens": 10, "output_tokens": 1}),
                     assistant("m", {"input_tokens": 10, "output_tokens": 4,
                                     "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}))
        self.assertEqual(1, got["assistant_messages"])
        self.assertEqual(4, got["usage"]["output_tokens"]["observed_total"])
        self.assertEqual(10, got["first_assistant"]["reported_input_sum"])

    def test_missing_is_not_zero(self):
        got = census(assistant("missing"), assistant("zero", {"input_tokens": 0}))
        self.assertEqual({"observed_total": 0, "measured_messages": 1, "missing_messages": 1}, got["usage"]["input_tokens"])
        self.assertIsNone(got["first_assistant"]["reported_input_sum"])
        self.assertIsNone(got["usage"]["output_tokens"]["observed_total"])

    def test_invalid_usage_is_not_measured(self):
        got = census(assistant("m", {"input_tokens": True, "output_tokens": -1, "cache_read_input_tokens": "0"}))
        self.assertIsNone(got["usage"]["input_tokens"]["observed_total"])
        self.assertIsNone(got["usage"]["output_tokens"]["observed_total"])
        self.assertEqual(1, got["usage"]["cache_read_input_tokens"]["missing_messages"])

    def test_first_new_message_after_boundary_not_old_streaming_update(self):
        got = census(assistant("old", {"input_tokens": 10}), boundary("b"),
                     assistant("old", {"output_tokens": 5}), assistant("new", {"input_tokens": 40}),
                     assistant("new", {"cache_read_input_tokens": 2, "cache_creation_input_tokens": 0}))
        b = got["boundaries"][0]
        self.assertEqual("new", b["next_assistant"]["message_id"])
        self.assertEqual(42, b["next_assistant"]["reported_input_sum"])
        self.assertEqual(32000, b["post_tokens"])

    def test_two_boundaries_and_no_following_response(self):
        got = census(boundary("a"), assistant("m", {"input_tokens": 50}), boundary("b"), boundary("b"))
        self.assertEqual(2, len(got["boundaries"]))
        self.assertEqual("m", got["boundaries"][0]["next_assistant"]["message_id"])
        self.assertIsNone(got["boundaries"][1]["next_assistant"])

    def test_child_rows_and_bad_rows_are_counted_not_consumed(self):
        got = census("{bad", [], assistant("child", {"input_tokens": 999}, isSidechain=True), assistant("main", {"input_tokens": 1}))
        self.assertEqual(1, got["assistant_messages"])
        self.assertEqual(1, got["coverage"]["malformed_rows"])
        self.assertEqual(1, got["coverage"]["invalid_rows"])
        self.assertEqual(1, got["coverage"]["excluded_sidechain_rows"])

    def test_tool_results_deduplicate_and_measure_utf8(self):
        use = assistant("m")
        use["message"]["content"] = [{"type": "tool_use", "id": "t", "name": "Read", "input": {}}]
        result = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "content": "é"}]}}
        got = census(use, use, result, result)
        self.assertEqual(1, got["tool_results"]["count"])
        self.assertEqual(2, got["tool_results"]["text_utf8_bytes"])
        self.assertEqual(2, got["tool_results"]["by_tool"]["Read"]["text_utf8_bytes"])

    def test_restore_attachments_and_snapshots_have_separate_units(self):
        restored = {"type": "attachment", "uuid": "restore", "attachment": {
            "type": "invoked_skills", "skills": [{"name": "test", "path": "test.md", "content": "éabc"}]}}
        snapshot = {"type": "attachment", "uuid": "snapshot", "attachment": {"type": "prompt_snapshot", "systemPrompt": ["diagnostic copy"]}}
        got = census(boundary("b"), restored, restored, snapshot)
        self.assertEqual(1, got["attachments"]["by_type"]["invoked_skills"]["count"])
        self.assertEqual(5, got["attachments"]["by_type"]["invoked_skills"]["body_utf8_bytes"])
        self.assertEqual(1, got["attachments"]["by_type"]["prompt_snapshot"]["count"])
        self.assertEqual(5, got["boundaries"][0]["attachment_body_utf8_bytes"]["invoked_skills"])
        self.assertNotIn("prompt_snapshot", got["boundaries"][0]["attachment_body_utf8_bytes"])

    def test_first_response_without_usage_is_not_skipped(self):
        got = census(boundary("b"), assistant("missing"), assistant("later", {"input_tokens": 200}))
        self.assertEqual("missing", got["boundaries"][0]["next_assistant"]["message_id"])
        self.assertIsNone(got["boundaries"][0]["next_assistant"]["reported_input_sum"])

    def test_context_render_is_bounded_and_names_units(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        import session_digest
        got = census(*[boundary(str(i)) for i in range(50)])
        rendered = session_digest.render_context({"session_id": "session", "context_census": got})
        self.assertIn("50", rendered)
        self.assertIn("UTF-8", rendered)
        self.assertIn("not live context", rendered)
        self.assertIn("last 10", rendered)
        self.assertLess(len(rendered), 6000)

    def test_context_render_accepts_missing_session_identity(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        import session_digest
        for identity in ({}, {"session_id": None}):
            with self.subTest(identity=identity):
                rendered = session_digest.render_context({**identity, "context_census": census()})
                self.assertIn("Context census — unknown", rendered)
                self.assertNotIn("session_digest_None", rendered)

    def test_context_cli_writes_counts_not_conversation(self):
        import contextlib
        import io
        import tempfile
        from unittest.mock import patch
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        import session_digest
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = root / "fixture.jsonl"
            transcript.write_text(json.dumps({"type": "user", "message": {"content": "PRIVATE PROMPT NOT FOR CENSUS"}}) + "\n" +
                                  json.dumps(assistant("m", {"input_tokens": 4})), encoding="utf-8")
            with patch.object(sys, "argv", ["session_digest", "--context-only", "--project-dir", str(root)]), \
                    patch.object(session_digest, "pick_transcript", return_value=transcript), \
                    patch.object(session_digest, "last_assistant_text", side_effect=AssertionError("unexpected scan")), \
                    patch.object(session_digest, "tool_census", side_effect=AssertionError("unexpected scan")), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                session_digest.main()
            artifact = (root / "logs/session_digest_fixture.context.json").read_text(encoding="utf-8")
            self.assertNotIn("PRIVATE PROMPT", artifact + output.getvalue())
            self.assertFalse((root / "logs/session_digest_fixture.json").exists())
            self.assertEqual(4, json.loads(artifact)["context_census"]["usage"]["input_tokens"]["observed_total"])

    def test_existing_summary_contract_is_retained(self):
        builder = TranscriptSummaryBuilder("session", "test.jsonl")
        builder.process_line(json.dumps({"type": "user", "message": {"content": "Please inspect the harness"}}))
        got = builder.finalize()
        self.assertIn("metadata", got)
        self.assertIn("compactions", got)
        self.assertIn("tool_summary", got)
        self.assertEqual("Please inspect the harness", got["user_messages"][0]["content"])
        self.assertIn("context_census", got)


if __name__ == "__main__":
    unittest.main()
