#!/usr/bin/env python3
"""Regression proof that orchestration metrics closes every opened input file."""
import ast
import builtins
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_PATH = os.path.join(HERE, "..", "tools", "orchestration_metrics.py")
spec = importlib.util.spec_from_file_location("orchestration_metrics_resource_lifetime", MODULE_PATH)
om = importlib.util.module_from_spec(spec)
spec.loader.exec_module(om)


class TrackedFile:
    opened = []

    def __init__(self, real):
        self.real = real
        self.closed = False
        type(self).opened.append(self)

    def __enter__(self):
        self.real.__enter__()
        return self

    def __exit__(self, *args):
        self.real.__exit__(*args)
        self.closed = True

    def close(self):
        self.real.close()
        self.closed = True

    def __iter__(self):
        return iter(self.real)

    def __getattr__(self, name):
        return getattr(self.real, name)


class OpenTracker:
    def __init__(self):
        self.original = builtins.open

    def __enter__(self):
        def tracked(*args, **kwargs):
            return TrackedFile(self.original(*args, **kwargs))
        builtins.open = tracked
        TrackedFile.opened = []
        return self

    def __exit__(self, *args):
        builtins.open = self.original

    @property
    def all_closed(self):
        return all(handle.closed or handle.real.closed for handle in TrackedFile.opened)


def dump(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(value, fh)


def transcript_line(index, message_id=None):
    message = {"usage": {"input_tokens": 1, "output_tokens": 2}, "content": []}
    if message_id is not None:
        message["id"] = message_id
    return {"type": "assistant", "timestamp": f"2026-09-09T10:{index // 60:02d}:{index % 60:02d}Z", "message": message}


class ResourceLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="om_handles_")
        self.session = os.path.join(self.root, "session")
        self.workflow = os.path.join(self.session, "workflows", "run.json")
        self.archive = os.path.join(self.root, "archive.jsonl")
        self.ledger = os.path.join(self.root, "ledger.jsonl")
        self.transcript = os.path.join(self.root, "chat.jsonl")
        dump(self.workflow, {"runId": "run", "workflowName": "dispatch",
                             "workflowProgress": [{"type": "workflow_agent", "agentId": "a", "label": "job"}]})
        dump(self.archive, {"run": "other"})
        dump(self.ledger, {"label": "job", "timestamp": "2026-09-09T10:00:00Z"})
        os.makedirs(os.path.join(self.session, "subagents", "workflows", "run"), exist_ok=True)
        with open(os.path.join(self.session, "subagents", "workflows", "run", "agent-a.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(transcript_line(0, "m")) + "\n")
        with open(self.transcript, "w", encoding="utf-8") as fh:
            for index in range(240):
                fh.write(json.dumps(transcript_line(index, f"m{index}")) + "\n")
            fh.write("{malformed\n")
        om.ARCHIVE = self.archive

    def test_success_and_malformed_paths_close_handles(self):
        with OpenTracker() as tracker:
            self.assertEqual(len(om.agent_usage(os.path.dirname(os.path.join(self.session, "subagents", "workflows", "run", "agent-a.jsonl")))), 1)
            om.collect(self.session)
            om.collect_sidecar(self.ledger)
            om._sidecar_record_row(self.ledger)  # malformed schema is still valid JSON
            om._sidecar_record_launches(self.transcript)
            om.load_run_ledger()
        self.assertTrue(tracker.all_closed)
        self.assertGreater(len(TrackedFile.opened), 0)

    def test_malformed_json_load_closes_before_error_return(self):
        bad = os.path.join(self.root, "bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("not json")
        with OpenTracker() as tracker:
            self.assertEqual(om._read_verdict_file(bad), {})
            self.assertIsNone(om._sidecar_record_row(bad))
        self.assertTrue(tracker.all_closed)

    def test_long_transcript_keeps_end_evidence_and_closes(self):
        final_record = os.path.join(self.root, "final.record.json")
        launch = transcript_line(240, "launch-at-end")
        launch["cwd"] = self.root
        launch["message"]["content"] = [{"type": "tool_use", "name": "Bash", "input": {
            "command": "bash .claude/scripts/opencode_sidecar.sh -R final.record.json"}}]
        with open(self.transcript, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(launch) + "\n")
        with OpenTracker() as tracker:
            launches = om._sidecar_record_launches(self.transcript)
        self.assertTrue(tracker.all_closed)
        self.assertEqual({om._resolve_session_path(final_record, self.root)}, set(launches))
        self.assertIsNotNone(next(iter(launches.values())))

    def test_missing_exit_code_is_unknown_not_a_failed_exit(self):
        record = os.path.join(self.root, "missing-exit.record.json")
        for value in ({"label": "job"}, {"label": "job", "exitCode": None},
                      {"label": "job", "exitCode": False}):
            with self.subTest(value=value):
                dump(record, value)
                self.assertEqual("unknown", om._sidecar_record_row(record)["state"])
        for code, state in ((0, "completed"), (1, "exit-1")):
            dump(record, {"label": "job", "exitCode": code})
            self.assertEqual(state, om._sidecar_record_row(record)["state"])

    def test_global_sidecar_missing_exit_code_is_unknown(self):
        ledger = os.path.join(self.root, "missing-exit-ledger.jsonl")
        dump(ledger, {"label": "job", "timestamp": "2026-09-09T10:00:00Z"})
        self.assertEqual("unknown", om.collect_sidecar(ledger)[0]["state"])

    def test_empty_agent_transcript_keeps_usage_unknown(self):
        run_dir = os.path.join(self.root, "empty-run")
        os.makedirs(run_dir)
        Path(os.path.join(run_dir, "agent-empty.jsonl")).write_text("", encoding="utf-8")
        usage = om.agent_usage(run_dir)["empty"]
        self.assertEqual(0, usage["turns"])
        self.assertTrue(all(usage[key] is None for key in ("inp", "out", "cw", "cr")))
        self.assertIsNone(om.agent_cost(usage))

    def test_sidecar_run_identity_prefers_launch_id(self):
        first = os.path.join(self.root, "first.record.json")
        second = os.path.join(self.root, "second.record.json")
        common = {"label": "same", "timestamp": "2026-09-09T10:00:00Z", "exitCode": 0}
        dump(first, {**common, "launchId": "launch-one"})
        dump(second, {**common, "launchId": "launch-two"})
        self.assertEqual("sidecar-launch-one", om._sidecar_record_row(first)["run"])
        self.assertEqual("sidecar-launch-two", om._sidecar_record_row(second)["run"])

    def test_archive_chunks_rows_and_rejects_an_impossible_row(self):
        old_max, old_rotations = om.ARCHIVE_MAX_BYTES, om.ARCHIVE_MAX_ROTATIONS
        om.ARCHIVE_MAX_BYTES, om.ARCHIVE_MAX_ROTATIONS = 80, 3
        try:
            rows = [{"run": f"r{i}", "value": "x" * 24} for i in range(3)]
            fresh, skipped, conflicts = om._archive_rows_atomic(rows)
            self.assertEqual((len(fresh), skipped, conflicts), (3, 0, []))
            paths = om._archive_paths()
            self.assertGreater(len(paths), 1)
            self.assertTrue(all(os.path.getsize(path) <= om.ARCHIVE_MAX_BYTES for path in paths))
            self.assertEqual({"other", "r0", "r1", "r2"},
                             {row["run"] for row in om._archive_records()})
            before = {path: Path(path).read_bytes() for path in paths}
            with self.assertRaisesRegex(ValueError, "exceeds archive byte cap"):
                om._archive_rows_atomic([{"run": "huge", "value": "z" * 200}])
            self.assertEqual(before, {path: Path(path).read_bytes() for path in paths})
        finally:
            om.ARCHIVE_MAX_BYTES, om.ARCHIVE_MAX_ROTATIONS = old_max, old_rotations

    def test_every_open_call_is_inside_with_or_is_a_write(self):
        with open(MODULE_PATH, encoding="utf-8") as source:
            tree = ast.parse(source.read())
        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "open":
                continue
            parent = next((p for p in ast.walk(tree) if isinstance(p, (ast.With, ast.AsyncWith)) and any(node is x.context_expr for x in p.items)), None)
            if parent is None:
                violations.append(node.lineno)
        self.assertEqual(violations, [], f"unmanaged open() calls at lines {violations}")


if __name__ == "__main__":
    unittest.main()
