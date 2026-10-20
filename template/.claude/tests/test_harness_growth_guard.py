"""The growth advisory measures the edited repo file, never a same-named foreign file,
and it speaks once per file per turn unless a threshold is newly crossed. It also lists each
sentence the edit removed against HEAD, marking one another `.claude` markdown file carries as moved.

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
        self.head_text = None

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
                 "tool_input": {"file_path": str(path)},
                 "tool_response": {"originalFile": self.head_text or ""}}
        with patch.object(hook.sys, "stdin", io.StringIO(json.dumps(event))), \
                patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(env_root or self.root),
                                        "HARNESS_HOOK_STATE_DIR": str(self.state)}), \
                patch.object(hook, "head_size", return_value=0), \
                patch.object(hook, "head_text", lambda root, rel: self.head_text), \
                redirect_stdout(io.StringIO()) as output:
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

    def test_generated_dot_cache_mirror_stays_silent(self):
        mirror = self.root / ".claude/.cache/baseline/CLAUDE.md"
        mirror.parent.mkdir(parents=True)
        mirror.write_text(SMALL, encoding="utf-8")
        live = self.root / ".claude/skills/CLAUDE.md"
        live.parent.mkdir(parents=True)
        live.write_text(SMALL, encoding="utf-8")
        self.assertNotEqual("", self.emit(live), "positive control: the same file outside .cache fires")
        self.assertEqual("", self.emit(mirror))

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


RULE_A = "Keep the owner statement in the header and cite it from each step that reads it."
RULE_B = "A rule that spans its steps is stated once, in the header, and no step restates it."
RE_HOME = "A removed rule is re-homed, never deleted to fit a size threshold (instruction_quality §5)."


class RemovedSentenceTests(unittest.TestCase):
    """A small file crosses no byte threshold, so every line here is the removal report."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.state = Path(self.temp.name) / "state"
        self.state.mkdir(parents=True)
        self.target = self.root / ".claude/skills/x/SKILL.md"
        self.target.parent.mkdir(parents=True)
        self.turns = 0
        self.head = None

    def plant(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def turn(self, session):
        self.turns += 1
        row = {"type": "user", "uuid": f"t{self.turns}", "message": {"role": "user", "content": f"turn {self.turns}"}}
        with (self.state / (session[:8] + ".jsonl")).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")

    def emit(self, session="rmturn01", head="same"):
        head = self.head if head == "same" else head
        event = {"tool_name": "Edit", "session_id": session, "cwd": str(self.root),
                 "transcript_path": str(self.state / (session[:8] + ".jsonl")),
                 "tool_input": {"file_path": str(self.target)},
                 "tool_response": {"originalFile": head or ""}}
        with patch.object(hook.sys, "stdin", io.StringIO(json.dumps(event))), \
                patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.root),
                                        "HARNESS_HOOK_STATE_DIR": str(self.state)}), \
                patch.object(hook, "head_size", return_value=0), \
                patch.object(hook, "head_text", lambda root, rel: head), \
                redirect_stdout(io.StringIO()) as output:
            hook.main()
        out = output.getvalue()
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""

    def test_a_removed_sentence_prints_removed_and_the_re_home_line(self):
        self.turn("rmturn01")
        self.head = f"- {RULE_A}\n- {RULE_B}\n"
        self.plant(".claude/skills/x/SKILL.md", f"- {RULE_A}\n")
        out = self.emit()
        self.assertIn("removed", out)
        self.assertIn(RULE_B, out)
        self.assertNotIn(RULE_A, out)
        self.assertIn(RE_HOME, out)

    def test_a_sentence_another_file_carries_prints_moved_and_no_re_home_line(self):
        self.turn("rmturn01")
        self.head = f"- {RULE_A}\n- {RULE_B}\n"
        self.plant(".claude/skills/x/SKILL.md", f"- {RULE_A}\n")
        self.plant(".claude/skills/x/reference/detail.md", f"# Detail\n\n{RULE_B[:30]}\n{RULE_B[30:]}\n")
        out = self.emit()
        self.assertIn("moved → .claude/skills/x/reference/detail.md", out)
        self.assertNotIn("removed:", out)
        self.assertNotIn(RE_HOME, out)

    def test_a_copy_in_scratch_is_not_a_new_home(self):
        self.turn("rmturn01")
        self.head = f"- {RULE_A}\n- {RULE_B}\n"
        self.plant(".claude/skills/x/SKILL.md", f"- {RULE_A}\n")
        self.plant(".claude/scratch/copy.md", RULE_B + "\n")
        self.assertIn(RE_HOME, self.emit())

    def test_the_report_lists_at_most_eight_sentences_each_clipped_then_counts_the_rest(self):
        self.turn("rmturn01")
        long_one = "Sentence zero " + "word " * 60 + "ends here."
        many = [long_one] + [f"Removed sentence number {i} says something distinct about its topic." for i in range(1, 11)]
        self.head = "\n".join(f"- {t}" for t in many) + "\n"
        self.plant(".claude/skills/x/SKILL.md", f"- {RULE_A}\n")
        out = self.emit()
        listed = [ln for ln in out.splitlines() if ln.startswith("- ")]
        self.assertEqual(len(listed), 8)
        self.assertIn("+3 more", out)
        self.assertTrue(all(len(ln.split(": ", 1)[1].strip('"')) <= 140 for ln in listed), listed)

    def test_the_same_removal_in_one_turn_is_silent_and_a_changed_set_speaks_again(self):
        self.turn("rmturn01")
        self.head = f"- {RULE_A}\n- {RULE_B}\n- Third rule stays out of the current file entirely today.\n"
        self.plant(".claude/skills/x/SKILL.md", f"- {RULE_A}\n")
        self.assertIn(RE_HOME, self.emit())
        self.assertEqual("", self.emit())
        self.plant(".claude/skills/x/SKILL.md", f"- {RULE_A}\n- {RULE_B}\n")
        again = self.emit()
        self.assertIn("Third rule", again)
        self.assertNotIn("A rule that spans", again)

    def test_a_removal_speaks_even_when_the_byte_line_was_already_claimed(self):
        self.turn("rmturn01")
        self.plant(".claude/skills/x/SKILL.md", SMALL)
        self.assertIn("3,002 B", self.emit(head=None))
        self.assertEqual("", self.emit(head=None))
        self.head = f"- {RULE_B}\n- {SMALL}"
        out = self.emit()
        self.assertIn(RULE_B, out)
        self.assertNotIn("3,002 B", out)

    def test_no_head_copy_or_an_unchanged_file_reports_no_removal(self):
        self.turn("rmturn01")
        self.plant(".claude/skills/x/SKILL.md", f"- {RULE_A}\n")
        self.assertEqual("", self.emit(head=None))
        self.assertEqual("", self.emit(session="rmturn02", head=f"- {RULE_A}\n"))

    def test_a_failing_head_read_stays_silent_and_never_raises(self):
        self.turn("rmturn01")
        self.plant(".claude/skills/x/SKILL.md", f"- {RULE_A}\n")

        def boom(root, rel):
            raise RuntimeError("git unavailable")

        event = {"tool_name": "Edit", "session_id": "rmturn01", "cwd": str(self.root),
                 "tool_input": {"file_path": str(self.target)}}
        with patch.object(hook.sys, "stdin", io.StringIO(json.dumps(event))), \
                patch.dict(os.environ, {"HARNESS_HOOK_STATE_DIR": str(self.state)}), \
                patch.object(hook, "head_size", return_value=0), \
                patch.object(hook, "head_text", boom), \
                redirect_stdout(io.StringIO()) as output:
            hook.main()
        self.assertEqual("", output.getvalue())

    def counted_emit(self, **kw):
        """emit() plus the corpus walks it ran and the `.claude` markdown files it opened besides the target."""
        walks, opened = [], []
        real_walk, real_open = os.walk, open

        def walk(top, *a, **k):
            walks.append(top)
            return real_walk(top, *a, **k)

        def counting_open(path, *a, **k):
            if str(path).endswith(".md") and Path(path).resolve() != self.target.resolve():
                opened.append(Path(path).name)
            return real_open(path, *a, **k)

        with patch.object(hook.os, "walk", walk), patch.object(hook, "open", counting_open, create=True):
            out = self.emit(**kw)
        return out, walks, opened

    def test_a_sentence_reworded_in_the_same_file_reads_reworded_with_no_re_home_line(self):
        self.turn("rmturn01")
        self.head = f"- {RULE_A}\n- {RULE_B}\n"
        reworded = "A rule that spans its steps is stated once, in the header, and every step cites it instead."
        self.plant(".claude/skills/x/SKILL.md", f"- {RULE_A}\n- {reworded}\n")
        out = self.emit()
        self.assertIn(f'- reworded: "{RULE_B}"', out)
        self.assertNotIn("- removed:", out)
        self.assertNotIn(RE_HOME, out)

    def test_an_unrelated_added_sentence_does_not_make_a_removal_a_rewording(self):
        self.turn("rmturn01")
        self.head = f"- {RULE_A}\n- {RULE_B}\n"
        self.plant(".claude/skills/x/SKILL.md", f"- {RULE_A}\n- Run the proof file directly before each commit.\n")
        out = self.emit()
        self.assertIn(f'- removed: "{RULE_B}"', out)
        self.assertIn(RE_HOME, out)

    def test_a_repeated_removal_set_runs_no_home_search(self):
        self.turn("rmturn01")
        self.head = f"- {RULE_A}\n- {RULE_B}\n"
        self.plant(".claude/skills/x/SKILL.md", f"- {RULE_A}\n")
        self.assertIn(RE_HOME, self.emit())
        out, walks, _opened = self.counted_emit()
        self.assertEqual("", out)
        self.assertEqual([], walks)

    def test_a_changed_removal_set_reads_unchanged_files_from_the_corpus_cache(self):
        self.turn("rmturn01")
        self.head = f"- {RULE_A}\n- {RULE_B}\n- Third rule stays out of the current file entirely today.\n"
        self.plant(".claude/skills/x/reference/detail.md", "# Detail\n\nUnrelated detail text lives here.\n")
        self.plant(".claude/skills/x/SKILL.md", f"- {RULE_A}\n")
        first, _walks, opened_first = self.counted_emit()
        self.plant(".claude/skills/x/SKILL.md", f"- {RULE_A}\n- {RULE_B}\n")
        second, _walks, opened_second = self.counted_emit()
        self.assertIn(RE_HOME, first)
        self.assertIn("Third rule", second)
        self.assertEqual(["detail.md"], opened_first)
        self.assertEqual([], opened_second)

    def test_an_edited_corpus_file_is_read_again(self):
        self.turn("rmturn01")
        self.head = f"- {RULE_A}\n- {RULE_B}\n"
        detail = self.root / ".claude/skills/x/reference/detail.md"
        self.plant(".claude/skills/x/reference/detail.md", "# Detail\n\nUnrelated detail text lives here.\n")
        self.plant(".claude/skills/x/SKILL.md", f"- {RULE_A}\n")
        self.assertIn("- removed:", self.emit())
        self.plant(".claude/skills/x/reference/detail.md", f"# Detail\n\n{RULE_B}\n")
        os.utime(detail, ns=(detail.stat().st_atime_ns, detail.stat().st_mtime_ns + 10**9))
        out = self.emit(session="rmturn02")
        self.assertIn("moved → .claude/skills/x/reference/detail.md", out)

    def test_a_home_search_past_its_time_budget_says_it_is_incomplete(self):
        self.turn("rmturn01")
        self.head = f"- {RULE_A}\n- {RULE_B}\n"
        self.plant(".claude/skills/x/reference/detail.md", f"# Detail\n\n{RULE_B}\n")
        self.plant(".claude/skills/x/SKILL.md", f"- {RULE_A}\n")
        with patch.object(hook, "HOME_SEARCH_SECONDS", -1.0):
            out = self.emit()
        self.assertIn("- removed:", out)
        self.assertIn("home search stopped", out)

    def test_removal_state_is_kept_apart_from_threshold_flags(self):
        self.turn("rmturn01")
        self.head = f"- {RULE_B}\n- {SMALL}"
        self.plant(".claude/skills/x/SKILL.md", SMALL)
        out = self.emit()
        self.assertIn("3,002 B", out)
        self.assertIn(RULE_B, out)
        state = json.loads((self.state / "rmturn01.json").read_text(encoding="utf-8"))
        record = state["harness_growth_seen"]["files"][".claude/skills/x/SKILL.md"]
        self.assertIsInstance(record, dict)
        self.assertTrue(record["flags"] and not any(f.startswith("removed") for f in record["flags"]), record)
        self.assertIsInstance(record["removed"], str)

    def test_printed_thresholds_are_review_triggers_not_caps(self):
        self.plant(".claude/skills/x/SKILL.md", HUGE)
        out = self.emit(session="rmturn03", head=None)
        self.assertIn("per-unit review trigger", out)
        self.assertIn("split trigger", out)
        self.assertNotRegex(out, r"\bcap\b")


class TouchedUnitsTests(unittest.TestCase):
    def test_unrelated_edit_does_not_report_existing_thresholds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / '.claude/rules/probe.md'
            target.parent.mkdir(parents=True)
            before = '- measured 2026-10-01 ' + 'large ' * 6000 + '\n- Keep the old rule.\n'
            after = before + '- Run its proof.\n'
            target.write_text(after, encoding='utf-8')
            event = {'tool_name': 'Write', 'cwd': tmp, 'tool_input': {'file_path': str(target), 'content': after},
                     'tool_response': {'originalFile': before}}
            with patch.object(hook, 'head_size', return_value=len(before.encode())), \
                    patch.object(hook, 'head_text', return_value=before):
                self.assertIsNone(hook.process(event))
                changed = after + '- measured 2026-10-08 ' + 'new ' * 250 + '\n'
                target.write_text(changed, encoding='utf-8')
                event['tool_input']['content'] = changed
                event['tool_response']['originalFile'] = after
                result = hook.process(event)
            self.assertIsNotNone(result)
            self.assertIn('narrative-shaped', result['context'])
            self.assertNotIn('split trigger', result['context'])
            self.assertNotIn('2026-10-01', result['context'])


# Captured Write update from top-level session 00d8a771-c98d-41a8-814a-ff440c801325.jsonl.
LIVE_WRITE = json.loads('{"content": "from pathlib import Path\\n\\npath = Path(r\\"C:\\\\Users\\\\dev\\\\.local\\\\bin\\\\claude.exe\\")\\ndata = path.read_bytes()\\nterms = [b\\"function kwt\\", b\\"function Swt\\", b\\"function wwt\\", b\\"function y8\\", b\\"function uWe\\", b\\"function zQ\\", b\\"function vf\\", b\\"subagent_estimate\\", b\\"reactiveUnsupported\\"]\\nfor needle in terms:\\n    found = data.find(needle)\\n    print(f\\"=== {needle.decode()} offset={found} ===\\")\\n    if found < 0:\\n        continue\\n    raw = data[max(0, found - 180):min(len(data), found + len(needle) + 1100)]\\n    text = \\"\\".join(chr(b) if 32 <= b <= 126 or b in (9, 10, 13) else \\" \\" for b in raw)\\n    text = \\" \\".join(text.split())\\n    print(text[:1300])\\n", "type": "update", "originalFile": "from pathlib import Path\\n\\npath = Path(r\\"C:\\\\Users\\\\dev\\\\.local\\\\bin\\\\claude.exe\\")\\ndata = path.read_bytes()\\nterms = [b\\"function kwt\\", b\\"function Swt\\", b\\"function wwt\\", b\\"function y8\\", b\\"function uWe\\", b\\"function zQ\\", b\\"function vf\\", b\\"subagent_estimate\\", b\\"reactiveUnsupported\\"]\\nfor needle in terms:\\n    print(f\\"=== {needle.decode()} ===\\")\\n    start = 0\\n    count = 0\\n    while count < 6:\\n        found = data.find(needle, start)\\n        if found < 0:\\n            break\\n        raw = data[max(0, found - 300):min(len(data), found + len(needle) + 2200)]\\n        text = \\"\\".join(chr(b) if 32 <= b <= 126 or b in (9, 10, 13) else \\" \\" for b in raw)\\n        text = \\" \\".join(text.split())\\n        print(f\\"[{count + 1}] offset={found} {text[:2500]}\\")\\n        count += 1\\n        start = found + len(needle)\\n", "structuredPatch": [{"oldStart": 4, "oldLines": 16, "newStart": 4, "newLines": 11, "lines": [" data = path.read_bytes()", " terms = [b\\"function kwt\\", b\\"function Swt\\", b\\"function wwt\\", b\\"function y8\\", b\\"function uWe\\", b\\"function zQ\\", b\\"function vf\\", b\\"subagent_estimate\\", b\\"reactiveUnsupported\\"]", " for needle in terms:", "-    print(f\\"=== {needle.decode()} ===\\")", "-    start = 0", "-    count = 0", "-    while count < 6:", "-        found = data.find(needle, start)", "-        if found < 0:", "-            break", "-        raw = data[max(0, found - 300):min(len(data), found + len(needle) + 2200)]", "-        text = \\"\\".join(chr(b) if 32 <= b <= 126 or b in (9, 10, 13) else \\" \\" for b in raw)", "-        text = \\" \\".join(text.split())", "-        print(f\\"[{count + 1}] offset={found} {text[:2500]}\\")", "-        count += 1", "-        start = found + len(needle)", "+    found = data.find(needle)", "+    print(f\\"=== {needle.decode()} offset={found} ===\\")", "+    if found < 0:", "+        continue", "+    raw = data[max(0, found - 180):min(len(data), found + len(needle) + 1100)]", "+    text = \\"\\".join(chr(b) if 32 <= b <= 126 or b in (9, 10, 13) else \\" \\" for b in raw)", "+    text = \\" \\".join(text.split())", "+    print(text[:1300])"]}]}')

class LiveWritePatchTests(unittest.TestCase):
    def test_live_write_patch_reconstructs_preimage(self):
        # The producer's originalFile is an independent oracle for its structuredPatch.
        response = dict(LIVE_WRITE, originalFile=None)
        assert hook.pre_edit_text({'tool_name': 'Write', 'tool_response': response}, LIVE_WRITE['content']) == LIVE_WRITE['originalFile']
        assert hook.pre_edit_text({'tool_name': 'Write'}, LIVE_WRITE['content']) is None
        response['structuredPatch'] = [dict(response['structuredPatch'][0], newStart=999)]
        assert hook.pre_edit_text({'tool_name': 'Write', 'tool_response': response}, LIVE_WRITE['content']) is None


if __name__ == "__main__":
    unittest.main()
