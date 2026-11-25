"""A Workflow subagent gets the compaction rail; only a parent start clears parent state.

For the SessionStart(compact) shapes, see logs/compaction_hooks.jsonl rows 746 and 753,
matched to the parent and agent-ad1ad58c74f9002b7 transcripts of session b9a508a5.
The PreCompact trigger heuristic still handles older payloads without an agent ID.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from _hook_call import call

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
HOOK = HOOKS / "subagent_compaction_rail.py"
SID = "953e9207-bb96-48a4-bd4e-e237148db367"


def _call(tokens, sidechain=False):
    return json.dumps({"type": "assistant", "isSidechain": sidechain, "message": {"usage": {
        "input_tokens": tokens, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}) + "\n"


def run(payload, env_extra=None, raw=None, live_subagent=True, state=None, parent_tokens=1000, spawn=False):
    """Plant a parent transcript and, when live_subagent is true, a newer Workflow agent at 469k tokens."""
    with tempfile.TemporaryDirectory() as fresh:
        tmp = state or fresh
        transcript = Path(tmp) / "t.jsonl"
        transcript.write_text(_call(parent_tokens), encoding="utf-8")
        agent = Path(tmp) / "t" / "subagents" / "workflows" / "wf_1" / "agent-a1.jsonl"
        if live_subagent:
            agent.parent.mkdir(parents=True, exist_ok=True)
            agent.write_text(_call(469_000, sidechain=True), encoding="utf-8")
            earlier = agent.stat().st_mtime - 2
            os.utime(transcript, (earlier, earlier))
        elif agent.exists():
            agent.unlink()
        env = {"HARNESS_HOOK_STATE_DIR": str(Path(tmp) / "hook_state")}
        env.update(env_extra or {})
        if isinstance(payload, dict):
            payload = dict(payload, transcript_path=str(transcript))
        data = raw if raw is not None else json.dumps(payload)
        if spawn:
            full_env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SIDECAR_PROMPT_FILE"}
            return subprocess.run([sys.executable, str(HOOK)], input=data, capture_output=True,
                                  text=True, env=dict(full_env, **env))
        code, out, err, _ = call(HOOK, data, env=env)
        return subprocess.CompletedProcess([str(HOOK)], code, out, err)


PRE = {"hook_event_name": "PreCompact", "session_id": SID, "trigger": "auto", "custom_instructions": "", "cwd": "."}
START = {"hook_event_name": "SessionStart", "session_id": SID, "source": "compact", "model": "x", "cwd": "."}
AGENT = {"agent_id": "ad1ad58c74f9002b7"}
SUBAGENT_START = dict(START, **AGENT)


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
        r = run(dict(PRE, trigger="manual", agent_id="abc"), live_subagent=False)
        self.ok(r)
        self.assertIn('"Task" section', r.stdout)

    def test_parent_manual_precompact_silent(self):
        r = run(dict(PRE, trigger="manual"))
        self.ok(r)
        self.assertEqual(r.stdout, "")

    def test_parent_at_its_window_silent(self):
        for live in (False, True):
            r = run(PRE, live_subagent=live, parent_tokens=467_000)
            self.ok(r)
            self.assertEqual(r.stdout, "", f"live_subagent={live}")

    def test_subagent_session_start_compact_prints_resume_without_precompact(self):
        r = run(SUBAGENT_START, live_subagent=False, spawn=True)
        self.ok(r)
        self.assertIn("Remaining", r.stdout)

    def test_session_start_startup_silent(self):
        r = run(dict(START, source="startup"))
        self.ok(r)
        self.assertEqual(r.stdout, "")

    def test_parent_session_start_compact_silent(self):
        r = run(START, live_subagent=False)
        self.ok(r)
        self.assertEqual(r.stdout, "")

    def test_sidecar_child_silent(self):
        env = {"CLAUDE_CODE_SIDECAR_PROMPT_FILE": "/tmp/brief.md"}
        for payload in (PRE, SUBAGENT_START):
            r = run(payload, env_extra=env)
            self.ok(r)
            self.assertEqual(r.stdout, "")

    def test_non_json_stdin_silent(self):
        r = run(None, raw="not json", spawn=True)
        self.ok(r)
        self.assertEqual(r.stdout, "")


class ParentCompactionClearTest(unittest.TestCase):
    """The shared session ID never authorizes a subagent to clear its parent's old context."""

    MARKED = {"model_ladder_ready": True, "fired_since_compaction": {"skill_in_context:orchestration": True}}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.hooks_state = os.path.join(self.tmp, "hook_state")
        os.makedirs(self.hooks_state)
        self.state_file = os.path.join(self.hooks_state, SID[:8] + ".json")
        with open(self.state_file, "w", encoding="utf-8") as fh:
            json.dump(self.MARKED, fh)

    def go(self, payload, env_extra=None, live_subagent=True):
        env = dict(env_extra or {}, HARNESS_HOOK_STATE_DIR=self.hooks_state)
        result = run(payload, env_extra=env, live_subagent=live_subagent, state=self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def state(self):
        with open(self.state_file, encoding="utf-8") as fh:
            return json.load(fh)

    def test_a_parent_session_start_compact_clears_the_markers_and_stamps_the_clear(self):
        before = time.time()
        self.go(START, live_subagent=False)
        state = self.state()
        self.assertNotIn("model_ladder_ready", state)
        self.assertNotIn("fired_since_compaction", state)
        self.assertGreaterEqual(state["compacted_at"], before)

    def precompact_overlap(self, *precompacts):
        for payload in precompacts:
            self.go(payload)
        self.assertEqual(self.state(), self.MARKED)

    def test_overlap_parent_start_first_clears_only_at_parent_start(self):
        self.precompact_overlap(dict(PRE, trigger="manual"), dict(PRE, **AGENT))
        self.assertEqual(self.go(START).stdout, "", "the parent must not consume the subagent's resume")
        cleared = self.state()
        self.assertNotIn("model_ladder_ready", cleared)
        self.assertIn("compacted_at", cleared)
        self.assertIn("Remaining", self.go(SUBAGENT_START).stdout)
        self.assertEqual(self.state(), cleared)

    def test_overlap_subagent_start_first_preserves_the_working_parent(self):
        self.precompact_overlap(dict(PRE, **AGENT), dict(PRE, trigger="manual"))
        out = self.go(SUBAGENT_START).stdout
        self.assertEqual(self.state(), self.MARKED, "a subagent must not clear the working parent")
        self.assertIn("Remaining", out)
        self.assertEqual(self.go(START).stdout, "")
        self.assertNotIn("model_ladder_ready", self.state())

    def test_a_sidecar_child_clears_its_own_state_silently(self):
        env = {"CLAUDE_CODE_SIDECAR_PROMPT_FILE": "/tmp/brief.md"}
        result = self.go(START, env_extra=env, live_subagent=False)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("model_ladder_ready", self.state())

    def test_a_sidecar_childs_subagent_compaction_leaves_the_markers_and_prints_nothing(self):
        env = {"CLAUDE_CODE_SIDECAR_PROMPT_FILE": "/tmp/brief.md"}
        self.assertEqual(self.go(PRE, env_extra=env).stdout, "")
        self.assertEqual(self.go(SUBAGENT_START, env_extra=env).stdout, "")
        self.assertEqual(self.state(), self.MARKED)

    def test_a_non_compact_session_start_clears_nothing(self):
        self.go(dict(START, source="resume"), live_subagent=False)
        self.assertEqual(self.state(), self.MARKED)


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
