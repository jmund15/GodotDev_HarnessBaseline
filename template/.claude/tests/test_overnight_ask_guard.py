"""Real hook-payload proofs for session-owned overnight state."""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import _scan_fixtures as fx

HOOK = Path(__file__).resolve().parents[1] / "hooks/overnight_ask_guard.py"


class OvernightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def call(self, *args, sid="one", payload=None):
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.root),
               "CLAUDE_CODE_SESSION_ID": sid, "CLAUDE_SESSION_ID": ""}
        result = subprocess.run([sys.executable, str(HOOK), *args],
                                input=json.dumps(payload or {}), text=True, capture_output=True,
                                env=env, timeout=60)
        self.assertNotIn("Traceback", result.stderr)
        return result

    def decision(self, sid="one", tool="AskUserQuestion", env_sid="one"):
        result = self.call(sid=env_sid, payload={"tool_name": tool, "session_id": sid, "cwd": str(self.root)})
        self.assertEqual(0, result.returncode, result.stderr)
        data = json.loads(result.stdout) if result.stdout.strip() else {}
        return data.get("hookSpecificOutput", {}).get("permissionDecision", "allow")

    def prompt(self, text, sid="one"):
        result = self.call(sid=sid, payload={"hook_event_name": "UserPromptSubmit", "session_id": sid,
                                             "cwd": str(self.root), "prompt": text})
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout

    def marker(self, sid="one"):
        return self.root / ".claude/scratch/overnight" / f"active-{sid}.json"

    def arm(self, sid="one", goal="finish"):
        result = self.call("--arm", goal, sid=sid)
        self.assertEqual(0, result.returncode, result.stderr)

    def close_doc(self, sid="one", q_line=None, next_session="- Next session: anthropic · Opus"):
        path = self.root / "Overnight" / f"2026-09-20-{sid}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = ["## Outcome", "", "- done — proof"]
        if q_line and next_session:
            rows.append(next_session)
        rows += ["", "## Decisions for you", ""]
        if q_line:
            rows.append(q_line)
        rows.extend(["", "## Left undone", "", "- None.", ""])
        path.write_text("\n".join(rows), encoding="utf-8")
        return path

    def test_no_marker_allows(self):
        self.assertEqual("allow", self.decision())

    def test_arm_uses_current_cli_session_id(self):
        self.arm()
        self.assertTrue(self.marker().exists())
        doc = json.loads(self.marker().read_text(encoding="utf-8"))
        self.assertEqual("one", doc["session_id"])
        self.assertEqual("finish", doc["goal"])
        self.assertEqual("deny", self.decision())
        self.assertEqual("allow", self.decision(tool="Bash"))

    def test_payload_identity_is_authoritative_and_peer_is_not_blocked(self):
        self.arm()
        self.assertEqual("allow", self.decision(sid="two", env_sid="one"))
        self.assertEqual("deny", self.decision(sid="one", env_sid="two"))

    def test_peer_owner_return_never_touches_own_state(self):
        self.arm(goal="first")
        self.arm(sid="two", goal="second")
        self.assertEqual("first", json.loads(self.marker().read_text())["goal"])
        self.prompt("morning", sid="two")
        self.assertEqual("deny", self.decision())
        self.assertEqual("allow", self.decision(sid="two"))
        self.assertTrue(self.marker().exists())

    def test_owner_prompt_ends_the_unattended_run(self):
        self.arm()
        out = self.prompt("morning, what happened?")
        self.assertFalse(self.marker().exists())
        self.assertIn("disarmed", json.loads(out)["hookSpecificOutput"]["additionalContext"])
        self.assertEqual("allow", self.decision())
        self.arm()
        self.prompt("<system-reminder>ctx</system-reminder>\nok go on")
        self.assertFalse(self.marker().exists())
        self.arm()
        self.prompt("overnight went badly, why?")
        self.assertFalse(self.marker().exists())

    def test_runtime_envelopes_and_overnight_resume_keep_it_armed(self):
        self.arm()
        for text in ("<task-notification><task-id>x</task-id></task-notification>",
                     "Another Claude session sent a message: <cross-session-message from=\"p\">hi"
                     "</cross-session-message>",
                     "<local-command-stdout>done</local-command-stdout>",
                     "<system-reminder>only an injection</system-reminder>",
                     "/overnight", "<command-name>/overnight</command-name>", ""):
            with self.subTest(text=text):
                self.assertEqual("", self.prompt(text).strip())
                self.assertEqual("deny", self.decision())

    def test_a_close_doc_validates_after_the_owner_disarmed_the_run(self):
        self.arm()
        self.prompt("morning, what happened?")
        self.assertFalse(self.marker().exists())
        result = self.call("--validate-close", str(self.close_doc()))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotEqual(0, self.call("--validate-close", str(self.close_doc(sid="other"))).returncode)

    def test_the_model_cannot_disarm_itself(self):
        self.arm()
        close = self.close_doc()
        self.assertEqual(0, self.call("--validate-close", str(close)).returncode)
        self.assertNotEqual(0, self.call("--disarm").returncode)
        self.assertEqual("deny", self.decision())

    def test_expired_owned_state_is_removed_but_peer_is_preserved(self):
        self.arm()
        self.arm(sid="two")
        doc = json.loads(self.marker().read_text())
        doc["armed_at"] = time.time() - 17 * 3600
        self.marker().write_text(json.dumps(doc), encoding="utf-8")
        self.assertEqual("allow", self.decision())
        self.assertFalse(self.marker().exists())
        self.assertTrue(self.marker("two").exists())

    def test_legacy_anonymous_marker_cannot_block_or_be_deleted(self):
        legacy = self.marker().with_name("active.json")
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps({"goal": "unknown owner", "session_id": "", "armed_at": time.time()}))
        self.assertEqual("allow", self.decision())
        self.prompt("morning")
        self.assertTrue(legacy.exists())

    def test_missing_or_unsafe_identity_cannot_arm(self):
        for sid in ("", "../peer", "a/b", "a\\b"):
            with self.subTest(sid=sid):
                self.assertNotEqual(0, self.call("--arm", "finish", sid=sid).returncode)
        self.assertFalse((self.root / ".claude/scratch/overnight/active.json").exists())

    def test_invalid_or_mismatched_state_is_not_claimed(self):
        self.arm()
        for doc in ([], {"session_id": "two", "armed_at": time.time()},
                    {"session_id": "one", "armed_at": float("nan")},
                    {"session_id": "one", "armed_at": time.time() + 3600}):
            with self.subTest(doc=doc):
                self.marker().write_text(json.dumps(doc))
                self.assertEqual("allow", self.decision())
                self.prompt("morning")
                self.assertTrue(self.marker().exists())

    def test_status_names_only_this_session(self):
        self.arm()
        result = self.call("--status")
        self.assertEqual(0, result.returncode, result.stderr)
        doc = json.loads(result.stdout)
        self.assertTrue(doc["armed"])
        self.assertEqual("one", doc["session_id"])
        self.assertEqual("finish", doc["goal"])
        self.assertFalse(json.loads(self.call("--status", sid="two").stdout)["armed"])

    def test_validate_close_accepts_a_parseable_doc_and_stays_armed(self):
        self.arm()
        close = self.close_doc(q_line=(
            "**Q1 — Delete the unpushed spike branch? Options: delete | keep. "
            "Park: irreversible — 3 commits exist on no remote. Recommendation: keep. "
            "Reversibility: none. Default: nothing deleted; the branch stays."
        ))
        result = self.call("--validate-close", str(close))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(self.marker().exists())
        self.assertEqual("deny", self.decision())

    def test_validate_close_rejects_wrong_identity_headings_and_q_shape(self):
        self.arm()
        wrong = self.close_doc("two")
        self.assertNotEqual(0, self.call("--validate-close", str(wrong)).returncode)
        bad_heading = self.close_doc()
        bad_heading.write_text(bad_heading.read_text().replace("## Left undone", "## Timeline"))
        self.assertNotEqual(0, self.call("--validate-close", str(bad_heading)).returncode)
        bad_q = self.close_doc(q_line="**Q1 — choose later. Recommendation: A.")
        self.assertNotEqual(0, self.call("--validate-close", str(bad_q)).returncode)
        self.assertTrue(self.marker().exists())

    def test_left_undone_rows_name_their_blocking_q_and_a_live_worklog_item(self):
        self.arm()
        mirror = self.root / ".claude/worklog-titles.md"
        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.write_text("## Active (1)\n\n### Tooling\n- refactor · 3 · Resume slice S4\n", encoding="utf-8")
        q1 = ("**Q1 — Wait on the peer's staged files? Options: wait | reroute. "
              "Park: out-of-scope — a peer holds them. Recommendation: wait. "
              "Reversibility: clean. Default: the run waits.")
        for row, ok in (
            ("- S4 execution, blocked on Q1.", False),
            ("- S4 execution — blocked by Q2 — worklog: Resume slice S4", False),
            ("- S4 execution — blocked by Q1 — worklog: Resume slice S9", False),
            ("- S4 execution — blocked by Q1 — worklog: Resume slice S4", True),
        ):
            with self.subTest(row=row):
                close = self.close_doc(q_line=q1)
                close.write_text(close.read_text().replace("- None.", row), encoding="utf-8")
                result = self.call("--validate-close", str(close))
                self.assertEqual(ok, result.returncode == 0, result.stderr)
                if not ok:
                    self.assertIn("Left undone", result.stderr)
        # Step 4's item carries `When: after <sid8> Q<n>`, so it sits in the Ledger, whose mirror line ends
        # in its `[after: ...]` condition; the row names the title without it.
        mirror.write_text("## Ledger (1)\n\n### Tooling\n- refactor · 3 · Resume slice S4 [after: ab12cd34 Q1]\n",
                          encoding="utf-8")
        close = self.close_doc(q_line=q1)
        close.write_text(close.read_text().replace("- None.", "- S4 execution — blocked by Q1 — worklog: Resume slice S4"),
                         encoding="utf-8")
        result = self.call("--validate-close", str(close))
        self.assertEqual(0, result.returncode, result.stderr)

    def deny_reason(self):
        result = self.call(payload={"tool_name": "AskUserQuestion", "session_id": "one", "cwd": str(self.root)})
        return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]

    def test_deny_reason_points_at_step_2_instead_of_restating_it(self):
        self.arm()
        reason = self.deny_reason()
        self.assertIn("commands/overnight.md §Step 2", reason)
        self.assertNotIn("reversible from the branch", reason)

    def test_overnight_review_prompt_disarms(self):
        for text in ("/overnight_review", "<command-name>/overnight_review</command-name>"):
            with self.subTest(text=text):
                self.arm()
                self.prompt(text)
                self.assertFalse(self.marker().exists())
                self.assertEqual("allow", self.decision())

    def questions(self, docs):
        folder = self.root / "Overnight"
        folder.mkdir(parents=True, exist_ok=True)
        for name, body in docs.items():
            (folder / name).write_bytes(body.encode("utf-8"))
        result = self.call("--open-questions", str(folder))
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def test_open_questions_routes_every_question_line_to_one_list(self):
        good = ("**Q1 — Push main? Options: push | wait. "
                "Park: irreversible — no owner prompt. Recommendation: push. "
                "Reversibility: none. Default: nothing pushed.")
        doc_a = "\n".join([
            "## Outcome", "", "- done", "", "## Decisions for you", "",
            good, "",
            "**Q2 — Keep lens? Options: a | b. Park: out-of-scope — x. Recommendation: a.",
            "Resolved: 2026-09-24 — a — kept",
            "**Q3 — why was Crab#1 knocked up?",
            "**Q7 — Old-grammar row? Options: a | b. Park: out-of-scope — x. Recommendation: a.",
            "  - **Q4 — legacy bullet? (a) yes (b) no",
            "    Resolved: 2026-09-24 — obsolete — gone",
            "### Q5 — heading-form question",
            "- Q6 — unbolded bullet question",
            "", "## Left undone", "", "- **Q1:** blocked on push.", ""])
        out = self.questions({"a.md": doc_a, "empty.md": ""})
        self.assertEqual(2, out["docs_scanned"])
        self.assertEqual([1, 7], sorted(r["number"] for r in out["open"]))
        self.assertEqual([3, 5, 6], sorted(r["number"] for r in out["malformed"]))
        self.assertEqual([2, 4], sorted(r["number"] for r in out["resolved"]))
        self.assertEqual(["empty.md"], [Path(d).name for d in out["unreadable"]])

    NEW_ROW = ("**Q1 — Which lens? Options: a | b. Park: {reason} — the owner picks. "
               "Recommendation: {rec}. Reversibility: {rev}. Default: nothing changes.")

    def new_row(self, reason="irreversible", rec="a", rev="clean"):
        return self.NEW_ROW.format(reason=reason, rec=rec, rev=rev)

    def validate(self, q_line, **kw):
        self.arm()
        return self.call("--validate-close", str(self.close_doc(q_line=q_line, **kw)))

    def test_validate_close_accepts_an_owner_domain_park(self):
        result = self.validate(self.new_row(reason="owner-domain"))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_validate_close_needs_reversibility_and_default_on_every_q_row(self):
        legacy = ("**Q1 — Which lens? Options: a | b. Park: out-of-scope — x. Recommendation: a.")
        result = self.validate(legacy)
        self.assertEqual(2, result.returncode)
        self.assertIn("Reversibility", result.stderr)
        self.assertIn("Default", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_validate_close_rejects_an_unknown_reversibility(self):
        result = self.validate(self.new_row(rev="maybe"))
        self.assertEqual(2, result.returncode)
        self.assertIn("Reversibility", result.stderr)

    def test_a_recommendation_holding_a_period_keeps_both_fields(self):
        result = self.validate(self.new_row(rec="use v1.2 as is"))
        self.assertEqual(0, result.returncode, result.stderr)
        guard = self.load_guard()
        match = guard._Q_ROW.fullmatch(self.new_row(rec="use v1.2 as is"))
        self.assertEqual("use v1.2 as is", match.group("recommendation"))
        self.assertEqual("clean", match.group("reversibility"))
        self.assertEqual("nothing changes.", match.group("default") + ".")

    def load_guard(self):
        import importlib.util
        sys.path.insert(0, str(HOOK.parent))
        spec = importlib.util.spec_from_file_location("overnight_ask_guard_under_test", HOOK)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_a_doc_with_a_q_row_needs_a_next_session_line(self):
        row = self.new_row()
        missing = self.validate(row, next_session=None)
        self.assertEqual(2, missing.returncode)
        self.assertIn("Next session", missing.stderr)
        for bad in ("- Next session: anthropic", "- Next session: · Opus", "- Next session anthropic · Opus"):
            with self.subTest(line=bad):
                self.assertEqual(2, self.validate(row, next_session=bad).returncode)
        for good in ("- Next session: anthropic · Opus", "Next session: codex · gpt-5 high"):
            with self.subTest(line=good):
                result = self.validate(row, next_session=good)
                self.assertEqual(0, result.returncode, result.stderr)

    def test_a_doc_without_a_q_row_needs_no_next_session_line(self):
        result = self.validate(None)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_open_questions_routes_old_and_new_grammar_to_open_and_a_broken_row_to_malformed(self):
        out = self.questions({"a.md": chr(10).join([
            "## Decisions for you", "", self.new_row(reason="owner-domain"),
            "**Q2 — Old? Options: a | b. Park: irreversible — x. Recommendation: a.",
            "**Q3 — broken row without options."])})
        self.assertEqual([1, 2], sorted(r["number"] for r in out["open"]))
        self.assertEqual([3], [r["number"] for r in out["malformed"]])

    def test_open_questions_on_an_empty_dir_is_empty(self):
        out = self.questions({})
        self.assertEqual({"docs_scanned": 0, "open": [], "malformed": [], "resolved": [], "unreadable": []}, out)

    def test_open_questions_without_a_dir_reads_the_vault_overnight_dir_from_the_baseline_lock(self):
        vault = self.root / "vault"
        folder = vault / "DevProjects/Proj/Claude/TODO/Overnight"
        folder.mkdir(parents=True)
        (folder / "a.md").write_text("## Decisions for you\n\n**Q1 — x? Options: O1 a; O2 b. Default: a.**\n",
                                     encoding="utf-8")
        (self.root / ".claude").mkdir()
        (self.root / ".claude/baseline.lock.json").write_text(json.dumps(
            {"substitutions": {fx.placeholder("VAULT_ROOT"): str(vault), fx.placeholder("PROJECT_NAME"): "Proj"}}), encoding="utf-8")
        result = self.call("--open-questions")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(1, json.loads(result.stdout)["docs_scanned"])
        self.assertEqual(0, self.call("--archive-closed").returncode)

    def test_no_source_holds_a_placeholder_token_a_consumer_install_would_expand(self):
        pattern = re.compile(r"\{\{[A-Z_]+\}\}")
        for path in (HOOK, Path(__file__)):
            self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")), path.name)

    def test_open_questions_without_a_dir_or_a_baseline_lock_fails_with_the_missing_source(self):
        result = self.call("--open-questions")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("baseline.lock.json", result.stderr)

    def test_open_questions_never_touches_markers(self):
        self.arm()
        doc = json.loads(self.marker().read_text())
        doc["armed_at"] = time.time() - 40 * 3600
        self.marker().write_text(json.dumps(doc), encoding="utf-8")
        self.questions({"a.md": "## Decisions for you\n\n**Q1 — x"})
        self.assertTrue(self.marker().exists())

    SID_DONE = "11111111-aaaa-bbbb-cccc-000000000001"
    SID_OPEN = "22222222-aaaa-bbbb-cccc-000000000002"
    SID_ARMED = "33333333-aaaa-bbbb-cccc-000000000003"

    def archive(self, docs, armed_sid=None):
        folder = self.root / "Overnight"
        (folder / "archive").mkdir(parents=True, exist_ok=True)
        for name, body in docs.items():
            (folder / name).write_bytes(body.encode("utf-8"))
        if armed_sid:
            self.arm(sid=armed_sid)
        result = self.call("--archive-closed", str(folder))
        self.assertEqual(0, result.returncode, result.stderr)
        top = sorted(p.name for p in folder.glob("*.md"))
        kept = sorted(p.name for p in (folder / "archive").glob("*.md"))
        return json.loads(result.stdout), top, kept

    def test_archive_closed_moves_finished_session_docs_and_keeps_every_doc_that_still_needs_the_owner(self):
        resolved = "## Decisions for you\n\n" + self.new_row() + "\nResolved: 2026-10-01 — a — done\n"
        docs = {
            f"2026-09-30-{self.SID_DONE}.md": resolved,
            "2026-09-30-11111111-autolearn-packet.md": "| R1 | x |\n",
            f"2026-09-30-{self.SID_OPEN}.md": "## Decisions for you\n\n" + self.new_row() + "\n",
            "2026-09-30-22222222-autolearn-packet.md": "| R1 | x |\n",
            f"2026-09-30-{self.SID_ARMED}.md": resolved,
            "2026-09-08.md": "## Outcome\n\n- done\n\n## Decisions for you\n\nNone.\n",
            "2026-09-09.md": "## Decisions for you\n\n**Q1 — broken row without options.\n",
            "empty.md": "",
        }
        out, top, archived = self.archive(docs, armed_sid=self.SID_ARMED)
        self.assertEqual(sorted([f"2026-09-30-{self.SID_DONE}.md", "2026-09-30-11111111-autolearn-packet.md",
                                 "2026-09-08.md"]), archived)
        self.assertEqual(sorted([f"2026-09-30-{self.SID_OPEN}.md", "2026-09-30-22222222-autolearn-packet.md",
                                 f"2026-09-30-{self.SID_ARMED}.md", "2026-09-09.md", "empty.md"]), top)
        self.assertEqual(archived, sorted(Path(p).name for p in out["archived"]))
        reasons = {Path(k["doc"]).name: k["reason"] for k in out["kept"]}
        self.assertEqual("open", reasons[f"2026-09-30-{self.SID_OPEN}.md"])
        self.assertEqual("open", reasons["2026-09-30-22222222-autolearn-packet.md"])
        self.assertEqual("armed", reasons[f"2026-09-30-{self.SID_ARMED}.md"])
        self.assertEqual("malformed", reasons["2026-09-09.md"])
        self.assertEqual("unreadable", reasons["empty.md"])
        self.assertTrue(self.marker(self.SID_ARMED).exists())
        rescan = self.questions({})
        self.assertEqual(5, rescan["docs_scanned"])

    def test_archive_closed_keeps_the_packet_of_an_armed_session_that_has_no_close_doc_yet(self):
        packet = f"2026-09-30-{self.SID_ARMED[:8]}-autolearn-packet.md"
        out, top, archived = self.archive({packet: "| R1 | x |\n"}, armed_sid=self.SID_ARMED)
        self.assertEqual([packet], top)
        self.assertEqual([], archived)
        self.assertEqual("armed", out["kept"][0]["reason"])

    def test_archive_closed_never_overwrites_an_archived_doc_of_the_same_name(self):
        folder = self.root / "Overnight"
        (folder / "archive").mkdir(parents=True)
        (folder / "archive" / "2026-09-08.md").write_bytes(b"older copy\n")
        out, top, archived = self.archive({"2026-09-08.md": "## Outcome\n\n- done\n"})
        self.assertEqual(["2026-09-08.md"], top)
        self.assertEqual(b"older copy\n", (folder / "archive" / "2026-09-08.md").read_bytes())
        self.assertEqual("archive-target-exists", out["kept"][0]["reason"])

    def test_malformed_hook_input_fails_open(self):
        result = self.call(payload=["not an object"])
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stdout)


if __name__ == "__main__":
    unittest.main()
