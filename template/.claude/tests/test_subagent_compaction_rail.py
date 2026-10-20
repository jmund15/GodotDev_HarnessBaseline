"""subagent_compaction_rail: a Workflow subagent's compaction gets the Task-section rail; the parent and sidecar children get nothing from it.

Payload shapes are cut from .claude/scratch/a2_probe/hook_log.jsonl (real PreCompact / SessionStart(compact) firings of a Workflow subagent:
no agent_id, parent session_id and transcript_path). A subagent is told apart by an `auto` trigger while the parent's gauge sits under the floor.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
HOOK = HOOKS / "subagent_compaction_rail.py"
SID = "953e9207-bb96-48a4-bd4e-e237148db367"


def run(payload, env_extra=None, raw=None, parent_gauge=True, state=None):
    """One hook run in `state` (a shared dir, so a PreCompact marker reaches the SessionStart after it) or a fresh one."""
    with tempfile.TemporaryDirectory() as fresh:
        tmp = state or fresh
        transcript = Path(tmp) / "t.jsonl"
        transcript.write_text(json.dumps({"type": "assistant", "message": {"usage": {
            "input_tokens": 1000, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}) + "\n",
            encoding="utf-8")
        if parent_gauge:
            (Path(tmp) / f"cc-cachestat-{SID}.json").write_text(
                json.dumps({"context_pct": 1, "last": "0:0:10000"}), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SIDECAR_PROMPT_FILE"}
        env["CC_CACHESTAT_DIR"] = tmp
        env.update(env_extra or {})
        if isinstance(payload, dict):
            payload = dict(payload, transcript_path=str(transcript))
        data = raw if raw is not None else json.dumps(payload)
        return subprocess.run([sys.executable, str(HOOK)], input=data, capture_output=True, text=True, env=env)


PRE = {"hook_event_name": "PreCompact", "session_id": SID, "trigger": "auto", "custom_instructions": "", "cwd": "."}
START = {"hook_event_name": "SessionStart", "session_id": SID, "source": "compact", "model": "x", "cwd": "."}


class SubagentRailTest(unittest.TestCase):
    def ok(self, result):
        self.assertEqual(result.returncode, 0, f"CRASH: {result.stderr}")
        self.assertNotIn("Traceback", result.stderr)

    def test_subagent_precompact_prints_task_rail(self):
        r = run(PRE)
        self.ok(r)
        self.assertIn('"Task" section', r.stdout)
        self.assertIn("Recent results", r.stdout)
        self.assertNotIn("re-injected verbatim", r.stdout)

    def test_agent_id_marks_subagent(self):
        r = run(dict(PRE, trigger="manual", agent_id="abc"), parent_gauge=False)
        self.ok(r)
        self.assertIn('"Task" section', r.stdout)

    def test_parent_manual_precompact_silent(self):
        r = run(dict(PRE, trigger="manual"))
        self.ok(r)
        self.assertEqual(r.stdout, "")

    def test_parent_over_floor_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "t.jsonl"
            transcript.write_text(json.dumps({"type": "assistant", "message": {"usage": {
                "input_tokens": 9000}}}) + "\n", encoding="utf-8")
            (Path(tmp) / f"cc-cachestat-{SID}.json").write_text(
                json.dumps({"context_pct": 90, "last": "0:0:9000"}), encoding="utf-8")
            env = dict(os.environ, CC_CACHESTAT_DIR=tmp)
            env.pop("CLAUDE_CODE_SIDECAR_PROMPT_FILE", None)
            r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(dict(PRE, transcript_path=str(transcript))),
                               capture_output=True, text=True, env=env)
        self.ok(r)
        self.assertEqual(r.stdout, "")

    def test_subagent_session_start_compact_prints_resume(self):
        with tempfile.TemporaryDirectory() as state:
            self.ok(run(PRE, state=state))
            r = run(START, state=state)
            self.ok(r)
            self.assertIn("Remaining", r.stdout)
            self.assertIn("state", r.stdout)
            self.assertEqual(run(START, state=state).stdout, "", "one marker serves one SessionStart")

    def test_parent_precompact_clears_marker(self):
        with tempfile.TemporaryDirectory() as state:
            self.ok(run(PRE, state=state))
            self.ok(run(dict(PRE, trigger="manual"), state=state))
            self.assertEqual(run(START, state=state).stdout, "")

    def test_session_start_startup_silent(self):
        r = run(dict(START, source="startup"))
        self.ok(r)
        self.assertEqual(r.stdout, "")

    def test_parent_session_start_compact_silent(self):
        r = run(START, parent_gauge=False)
        self.ok(r)
        self.assertEqual(r.stdout, "")

    def test_sidecar_child_silent(self):
        env = {"CLAUDE_CODE_SIDECAR_PROMPT_FILE": "/tmp/brief.md"}
        for payload in (PRE, START):
            r = run(payload, env_extra=env)
            self.ok(r)
            self.assertEqual(r.stdout, "")

    def test_non_json_stdin_silent(self):
        r = run(None, raw="not json")
        self.ok(r)
        self.assertEqual(r.stdout, "")


class RailModuleTest(unittest.TestCase):
    def test_routes(self):
        sys.path.insert(0, str(HOOKS))
        import _compaction_rail as cr
        self.assertIn("re-injected verbatim", cr.rail_text(4, "sidecar"))
        sub = cr.rail_text(3, "subagent")
        self.assertIn('"Task" section', sub)
        self.assertNotIn("re-injected verbatim", sub)
        self.assertEqual(cr.DEFAULT_RAIL, 4)
        self.assertEqual(set(cr.RAILS), {3, 4})


if __name__ == "__main__":
    unittest.main()
