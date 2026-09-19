"""The growth advisory measures the edited repo file, never a same-named foreign file,
and it speaks once per file per turn unless a threshold is newly crossed.

State is redirected with HARNESS_HOOK_STATE_DIR — this never touches ~/.claude/.routing_state/.
"""
import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("harness_growth_guard", Path(__file__).resolve().parents[1] / "hooks/harness_growth_guard.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)

SMALL = "- " + "large " * 500          # 3,002 B: grows, dense, per-unit outlier
HUGE = "- " + "large " * 6000          # 36,002 B: the same three plus the split cap


class GrowthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.state = Path(self.temp.name) / "state"
        self.state.mkdir(parents=True)
        self.target = self.root / ".claude/CLAUDE.md"
        self.target.parent.mkdir(parents=True)
        self.target.write_text(SMALL, encoding="utf-8")
        self.counter = 0
        self.turn_counter = 0

    def transcript_path(self, session):
        return self.state / (session[:8] + ".jsonl")

    def append_turn(self, session, prompt):
        self.turn_counter += 1
        row = {
            "type": "user",
            "uuid": f"turn-{self.turn_counter}",
            "message": {"role": "user", "content": prompt},
        }
        with self.transcript_path(session).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")

    def emit(self, path, cwd=None, env_root=None, session=None):
        """One Write|Edit through the hook. A fresh session per call by default, so a
        case that is not about cadence never inherits another case's receipts."""
        if session is None:
            self.counter += 1
            session = "growth%02d" % self.counter
        event = {"tool_name": "Edit", "session_id": session, "cwd": str(cwd or self.root),
                 "transcript_path": str(self.transcript_path(session)),
                 "tool_input": {"file_path": str(path)}}
        with patch.object(hook.sys, "stdin", io.StringIO(json.dumps(event))), \
                patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(env_root or self.root),
                                        "HARNESS_HOOK_STATE_DIR": str(self.state)}), \
                patch.object(hook, "head_size", return_value=0), redirect_stdout(io.StringIO()) as output:
            hook.main()
        return output.getvalue()

    def set_turn(self, session, prompt):
        """Plant one owner turn and reset the fields UserPromptSubmit owns."""
        path = self.state / (session[:8] + ".json")
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        state["last_prompt"] = prompt[:4000]
        state["post_grep_nudges_fired_this_turn"] = []
        state["pre_nudges_fired_this_turn"] = []
        state["edit_seen_this_turn"] = False
        path.write_text(json.dumps(state), encoding="utf-8")
        self.append_turn(session, prompt)

    def set_runtime_turn(self, session, prompt):
        path = self.state / (session[:8] + ".json")
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        state["post_grep_nudges_fired_this_turn"] = []
        state["pre_nudges_fired_this_turn"] = []
        state["edit_seen_this_turn"] = False
        path.write_text(json.dumps(state), encoding="utf-8")
        self.append_turn(session, prompt)

    def test_repo_absolute_path_fires_in_model_channel(self):
        result = json.loads(self.emit(self.target))["hookSpecificOutput"]
        self.assertEqual("PostToolUse", result["hookEventName"])
        self.assertIn("3,002 B", result["additionalContext"])

    def test_foreign_claude_path_never_maps_to_repo_file(self):
        foreign = Path(self.temp.name) / "home/.claude/CLAUDE.md"
        foreign.parent.mkdir(parents=True)
        foreign.write_text("small", encoding="utf-8")
        self.assertEqual("", self.emit(foreign))

    def test_payload_cwd_beats_environment(self):
        self.assertIn("3,002 B", self.emit(self.target, env_root=self.root / "wrong"))

    def test_relative_path_is_resolved_against_payload_root(self):
        self.assertIn("3,002 B", self.emit(".claude/CLAUDE.md"))

    def test_traversal_and_sibling_prefix_are_not_in_scope(self):
        for path in ("../peer/.claude/CLAUDE.md", str(self.root) + "-peer/.claude/CLAUDE.md"):
            with self.subTest(path=path):
                self.assertEqual("", self.emit(path))

    def test_excluded_scratch_file_stays_silent(self):
        self.assertEqual("", self.emit(self.root / ".claude/scratch/report.md"))

    def test_second_edit_to_the_same_file_in_one_turn_is_silent(self):
        self.set_turn("growturn", "first user turn")
        self.assertIn("3,002 B", self.emit(self.target, session="growturn"))
        self.assertEqual("", self.emit(self.target, session="growturn"))

    def test_a_newly_crossed_cap_re_arms_within_the_same_turn(self):
        self.set_turn("growturn", "first user turn")
        first = self.emit(self.target, session="growturn")
        self.assertIn("3,002 B", first)
        self.assertNotIn("split trigger", first)
        self.target.write_text(HUGE, encoding="utf-8")
        second = self.emit(self.target, session="growturn")
        self.assertIn("36,002 B", second)
        self.assertIn("split trigger", second)

    def test_the_next_turn_re_arms_the_same_measurement(self):
        self.set_turn("growturn", "first user turn")
        self.assertIn("3,002 B", self.emit(self.target, session="growturn"))
        self.assertEqual("", self.emit(self.target, session="growturn"))
        self.set_turn("growturn", "second user turn")
        self.assertIn("3,002 B", self.emit(self.target, session="growturn"))

    def test_e13_repeated_owner_prompt_still_starts_a_new_turn(self):
        self.set_turn("growturn", "repeat this exact request")
        self.assertIn("3,002 B", self.emit(self.target, session="growturn"))
        self.set_turn("growturn", "repeat this exact request")
        self.assertIn("3,002 B", self.emit(self.target, session="growturn"))

    def test_e13_prompts_sharing_first_4000_characters_are_distinct_turns(self):
        prefix = "x" * 4000
        self.set_turn("growturn", prefix + " first tail")
        self.assertIn("3,002 B", self.emit(self.target, session="growturn"))
        self.set_turn("growturn", prefix + " second tail")
        self.assertIn("3,002 B", self.emit(self.target, session="growturn"))

    def test_synthetic_system_context_does_not_fake_a_new_turn(self):
        self.set_turn("growturn", "owner request")
        self.assertIn("3,002 B", self.emit(self.target, session="growturn"))
        self.append_turn("growturn", "<system-reminder>tool context</system-reminder>")
        self.assertEqual("", self.emit(self.target, session="growturn"))

    def append_row(self, session, content, **extra):
        self.turn_counter += 1
        row = dict({"type": "user", "uuid": f"turn-{self.turn_counter}",
                    "message": {"role": "user", "content": content}}, **extra)
        with self.transcript_path(session).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")

    def test_d6_envelope_only_row_does_not_start_a_turn(self):
        self.set_turn("growturn", "owner request")
        self.assertIn("3,002 B", self.emit(self.target, session="growturn"))
        self.set_runtime_turn(
            "growturn",
            "<task-notification>background task completed</task-notification>",
        )
        self.assertEqual("", self.emit(self.target, session="growturn"))

    def test_d6_sidechain_row_does_not_start_a_turn(self):
        self.set_turn("growturn", "owner request")
        self.assertIn("3,002 B", self.emit(self.target, session="growturn"))
        self.append_row("growturn", "subagent brief that is not the owner", isSidechain=True)
        self.assertEqual("", self.emit(self.target, session="growturn"))

    def test_d6_empty_args_command_row_does_not_start_a_turn(self):
        self.set_turn("growturn", "owner request")
        self.assertIn("3,002 B", self.emit(self.target, session="growturn"))
        self.append_row("growturn", "<command-message>clear</command-message>\n<command-name>/clear</command-name>")
        self.assertEqual("", self.emit(self.target, session="growturn"))

    def test_d6_injection_prefixed_prompt_starts_a_turn(self):
        self.set_turn("growturn", "owner request")
        self.assertIn("3,002 B", self.emit(self.target, session="growturn"))
        self.append_row("growturn", "<system-reminder>hook context</system-reminder>\nsecond owner request")
        self.assertIn("3,002 B", self.emit(self.target, session="growturn"))

    def test_a_second_file_in_the_same_turn_still_speaks(self):
        self.set_turn("growturn", "first user turn")
        other = self.root / ".claude/commands/other.md"
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text(SMALL, encoding="utf-8")
        self.assertIn("CLAUDE.md", self.emit(self.target, session="growturn"))
        self.assertIn("commands/other.md", self.emit(other, session="growturn"))

    def test_receipts_never_reach_the_real_state_directory(self):
        self.set_turn("growturn", "first user turn")
        self.emit(self.target, session="growturn")
        planted = json.loads((self.state / "growturn.json").read_text(encoding="utf-8"))
        self.assertIn("harness_growth_seen", planted)


if __name__ == "__main__":
    unittest.main()
