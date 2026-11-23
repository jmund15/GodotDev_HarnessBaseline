"""Real hook-payload proofs for session-owned overnight state."""
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock


HOOK = Path(__file__).resolve().parents[1] / "hooks/overnight_ask_guard.py"
sys.path.insert(0, str(HOOK.parent))
_spec = importlib.util.spec_from_file_location("overnight_ask_guard_under_test", HOOK)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


class OvernightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def call(self, *args, sid="one", payload=None):
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.root),
               "CLAUDE_CODE_SESSION_ID": sid, "CLAUDE_SESSION_ID": ""}
        return self.run_main(args, payload or {}, env)

    @staticmethod
    def run_main(args, payload, env, real_root=False):
        """The hook's main() in this process: the decisions are in-process cases (T6); only the entry-point
        case below spawns the script. An uncaught exception propagates, which the spawned form showed as a Traceback.
        The checkout-root lookup spawns git, so it is the identity here except in the linked-worktree case,
        which is its one check (tests/test_checkout_root.py covers the lookup itself)."""
        out, err = io.StringIO(), io.StringIO()
        root = guard.checkout_root if real_root else str
        with mock.patch.dict(os.environ, env), mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))),                 mock.patch.object(guard, "checkout_root", root),                 contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = guard.main([str(HOOK), *args])
        return types.SimpleNamespace(returncode=rc, stdout=out.getvalue(), stderr=err.getvalue())

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

    EVIDENCE = 'Evidence: {"file": "settled.txt", "quote": "the check settled it"}'

    def close_doc(self, sid="one", q_line=None, next_session="- Next session: anthropic · Opus",
                  evidence=EVIDENCE):
        path = self.root / "Overnight" / f"2026-09-20-{sid}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        (self.root / "settled.txt").write_text("the check settled it\n", encoding="utf-8")
        rows = ["## Outcome", "", "- done — proof"]
        if q_line and next_session:
            rows.append(next_session)
        rows += ["", "## Decisions for you", ""]
        if q_line:
            rows.append(q_line)
            if evidence and not q_line.startswith("**Q1 — choose later"):
                rows.append(evidence)
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
        self.prompt("/overnight off", sid="two")
        self.assertEqual("deny", self.decision())
        self.assertEqual("allow", self.decision(sid="two"))
        self.assertTrue(self.marker().exists())

    def test_owner_prompt_without_the_keyword_keeps_the_run_armed(self):
        self.arm()
        for text in ("morning, what happened?", "<system-reminder>ctx</system-reminder>\nok go on",
                     "overnight went badly, why?", "/overnight review", "overnight_review went badly",
                     "/overnight_reviewer", "what does /overnight offer?"):
            with self.subTest(text=text):
                out = self.prompt(text)
                self.assertTrue(self.marker().exists())
                context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
                self.assertIn("/overnight off", context)
                self.assertIn("armed", context)
                self.assertEqual("deny", self.decision())

    def test_overnight_off_in_an_owner_prompt_ends_the_run(self):
        for text in ("/overnight off", "ok, enough. /overnight off", "<system-reminder>ctx</system-reminder>\n/overnight off",
                     "/overnight --off",
                     "<command-name>/overnight</command-name>\n<command-args>--off</command-args>"):
            with self.subTest(text=text):
                self.arm()
                out = self.prompt(text)
                self.assertFalse(self.marker().exists())
                context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
                self.assertIn("ended", context)
                self.assertNotIn("--arm", context)
                self.assertEqual("allow", self.decision())

    def test_overnight_review_in_an_owner_prompt_ends_the_run(self):
        for text in ("/overnight_review", "/overnight_review 3", "ok, answered. /overnight_review",
                     "<command-message>overnight_review</command-message>\n<command-name>/overnight_review</command-name>"):
            with self.subTest(text=text):
                self.arm()
                out = self.prompt(text)
                self.assertFalse(self.marker().exists())
                context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
                self.assertIn("ended", context)
                self.assertIn("/overnight_review", context)
                self.assertNotIn("--arm", context)
                self.assertEqual("allow", self.decision())

    def test_overnight_review_ends_only_its_own_session_run(self):
        self.arm()
        self.prompt("/overnight_review", sid="two")
        self.assertTrue(self.marker().exists())

    def test_overnight_review_with_no_armed_run_changes_nothing(self):
        self.assertEqual("", self.prompt("/overnight_review").strip())
        self.assertFalse(self.marker().exists())

    def test_overnight_review_from_a_runtime_envelope_does_not_end_the_run(self):
        self.arm()
        self.prompt('<task-notification><task-id>x</task-id>/overnight_review</task-notification>')
        self.assertTrue(self.marker().exists())

    def test_overnight_off_from_a_runtime_envelope_does_not_end_the_run(self):
        self.arm()
        self.prompt('<task-notification><task-id>x</task-id>/overnight off</task-notification>')
        self.assertTrue(self.marker().exists())

    def test_runtime_envelopes_and_overnight_resume_keep_it_armed(self):
        self.arm()
        for text in ("<task-notification><task-id>x</task-id></task-notification>",
                     "Another Claude session sent a message: <cross-session-message from=\"p\">hi"
                     "</cross-session-message>",
                     '[Cross-session idle notice] "project-a2", which you asked to be notified about, is idle now',
                     "<local-command-stdout>done</local-command-stdout>",
                     "<system-reminder>only an injection</system-reminder>",
                     "/overnight", "<command-name>/overnight</command-name>", ""):
            with self.subTest(text=text):
                self.arm()
                self.assertEqual("", self.prompt(text).strip())
                self.assertEqual("deny", self.decision())

    def test_a_close_doc_validates_after_the_owner_disarmed_the_run(self):
        self.arm()
        self.prompt("/overnight off")
        self.assertFalse(self.marker().exists())
        result = self.call("--validate-close", str(self.close_doc()))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotEqual(0, self.call("--validate-close", str(self.close_doc(sid="other"))).returncode)

    def plant_overnight_dir(self, folder):
        adaptation = self.root / ".claude/skills/project_subsystems/adaptation.json"
        adaptation.parent.mkdir(parents=True, exist_ok=True)
        adaptation.write_text(json.dumps({"overnight_dir": str(folder)}), encoding="utf-8")

    def vault_doc(self, sid="one", rows=()):
        """Plant adaptation.json and this session's Close doc in the Overnight dir it names."""
        folder = self.root / "vault" / "Overnight"
        folder.mkdir(parents=True, exist_ok=True)
        self.plant_overnight_dir(folder)
        (self.root / "settled.txt").write_text("the check settled it\n", encoding="utf-8")
        body = ["## Outcome", "", "- done — proof", "- Next session: anthropic · Opus", "", "## Decisions for you", ""]
        body += list(rows) + ["", "## Left undone", "", "- None.", ""]
        path = folder / f"2026-09-20-{sid}.md"
        path.write_text("\n".join(body), encoding="utf-8")
        return path

    def ended(self, sid="one"):
        return self.root / ".claude/scratch/overnight" / f"ended-{sid}.json"

    def context(self, out):
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""

    def test_ending_a_run_names_each_q_row_still_open_in_this_sessions_close_doc(self):
        for text in ("/overnight off", "/overnight_review Q5 yes"):
            with self.subTest(text=text):
                self.arm()
                self.vault_doc(rows=[self.new_row(), self.EVIDENCE, "",
                                     self.new_row().replace("Q1", "Q2"), self.EVIDENCE, self.RESOLVED])
                self.vault_doc(sid="two", rows=[self.new_row().replace("Q1", "Q7"), self.EVIDENCE])
                context = self.context(self.prompt(text))
                self.assertIn("Q1", context)
                self.assertNotIn("Q2", context)
                self.assertNotIn("Q7", context)
                self.assertIn("AskUserQuestion", context)
                self.assertIn("this turn", context)
                self.assertIn("commands/overnight.md §Step 4", context)
                self.assertNotIn("allowed again", context)
                self.assertEqual([1], json.loads(self.ended().read_text(encoding="utf-8"))["open"])

    def test_ending_a_run_with_no_open_row_says_so_and_still_ends_parking(self):
        self.arm()
        self.vault_doc(rows=[self.new_row(), self.EVIDENCE, self.RESOLVED])
        context = self.context(self.prompt("/overnight off"))
        self.assertIn("no Q row is open", context)
        self.assertNotIn("Q1", context)
        self.assertIn("commands/overnight.md §Step 4", context)

    def test_ending_a_run_whose_overnight_dir_cannot_be_read_says_to_read_the_close_doc(self):
        self.arm()
        context = self.context(self.prompt("/overnight off"))
        self.assertFalse(self.marker().exists())
        self.assertIn("could not be read", context)
        self.assertIsNone(json.loads(self.ended().read_text(encoding="utf-8"))["open"])

    def test_an_open_row_is_named_on_each_owner_prompt_until_a_question_is_asked_or_it_resolves(self):
        self.arm()
        doc = self.vault_doc(rows=[self.new_row(), self.EVIDENCE])
        self.prompt("/overnight_review")
        later = self.context(self.prompt("so where are we?"))
        self.assertIn("Q1", later)
        self.assertIn("AskUserQuestion", later)
        self.assertNotIn("armed", later)
        self.assertEqual("", self.prompt("<task-notification><task-id>x</task-id></task-notification>").strip())
        self.assertEqual("", self.prompt("so where are we?", sid="two").strip())
        self.assertEqual("allow", self.decision(tool="Bash"))
        self.assertIn("Q1", self.context(self.prompt("and now?")))
        self.assertEqual("allow", self.decision())
        self.assertEqual("", self.prompt("and now?").strip())
        self.arm()
        self.prompt("/overnight off")
        doc.write_text(doc.read_text(encoding="utf-8").replace(self.EVIDENCE, self.EVIDENCE + "\n" + self.RESOLVED),
                       encoding="utf-8")
        self.assertEqual("", self.prompt("and now?").strip())

    def test_a_q_row_opened_after_the_owner_ended_the_run_fails_validation(self):
        self.arm()
        doc = self.vault_doc(rows=[self.new_row(), self.EVIDENCE])
        self.prompt("/overnight_review")
        kept = self.call("--validate-close", str(doc))
        self.assertEqual(0, kept.returncode, kept.stderr)
        self.assertIn("Q1", kept.stdout)
        self.assertIn("AskUserQuestion", kept.stdout)
        doc = self.vault_doc(rows=[self.new_row(), self.EVIDENCE, "", self.new_row().replace("Q1", "Q2"), self.EVIDENCE])
        parked = self.call("--validate-close", str(doc))
        self.assertEqual(2, parked.returncode)
        self.assertIn("Q2", parked.stderr)
        self.assertNotIn("Q1", parked.stderr)
        self.assertIn("AskUserQuestion", parked.stderr)
        self.assertIn("commands/overnight.md §Step 4", parked.stderr)
        doc = self.vault_doc(rows=[self.new_row().replace("Q1", "Q2"), self.EVIDENCE, self.RESOLVED])
        answered = self.call("--validate-close", str(doc))
        self.assertEqual(0, answered.returncode, answered.stderr)

    def test_a_new_run_clears_the_ended_record_and_parks_again(self):
        self.arm()
        self.vault_doc()
        self.prompt("/overnight off")
        self.assertTrue(self.ended().exists())
        self.arm()
        self.assertFalse(self.ended().exists())
        doc = self.vault_doc(rows=[self.new_row(), self.EVIDENCE])
        result = self.call("--validate-close", str(doc))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_an_ended_record_lapses_with_the_run_ttl_and_a_peers_is_never_read(self):
        self.arm()
        doc = self.vault_doc(rows=[self.new_row(), self.EVIDENCE])
        self.prompt("/overnight off")
        doc = self.vault_doc(rows=[self.new_row().replace("Q1", "Q2"), self.EVIDENCE])
        peer = self.vault_doc(sid="two", rows=[self.new_row(), self.EVIDENCE])
        self.assertEqual(0, self.call("--validate-close", str(peer), sid="two").returncode)
        self.assertEqual(2, self.call("--validate-close", str(doc)).returncode)
        record = json.loads(self.ended().read_text(encoding="utf-8"))
        record["ended_at"] = time.time() - 17 * 3600
        self.ended().write_text(json.dumps(record), encoding="utf-8")
        self.assertEqual(0, self.call("--validate-close", str(doc)).returncode)
        self.assertFalse(self.ended().exists())
        self.assertEqual("", self.prompt("and now?").strip())

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
        # On cloud the vault is absent, so `/worklog add` queues the item in worklog-pending.md and leaves the
        # mirror alone (worklog_cloud.md); a title queued there as an ADD names a live item, any other op does not.
        pending = self.root / ".claude/worklog-pending.md"
        row = "- Fold run — blocked by Q1 — worklog: Run the fold on prod"
        for line, ok in (
            ("- COMPLETE: Run the fold on prod", False),
            ("- ~~ADD ledger: Run the fold on prod (server · feature · scope 1)~~ (skipped: exists)", False),
            ("- ADD ledger: Run the fold on prod (server · feature · scope 1)", True),
        ):
            with self.subTest(pending=line):
                pending.write_text(f"# Worklog pending\n\n## 2026-11-03T17:30Z cloud session one\n{line}\n", encoding="utf-8")
                close = self.close_doc(q_line=q1)
                close.write_text(close.read_text().replace("- None.", row), encoding="utf-8")
                result = self.call("--validate-close", str(close))
                self.assertEqual(ok, result.returncode == 0, result.stderr)

    def deny_reason(self):
        result = self.call(payload={"tool_name": "AskUserQuestion", "session_id": "one", "cwd": str(self.root)})
        return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]

    def test_deny_reason_points_at_step_2_instead_of_restating_it(self):
        self.arm()
        reason = self.deny_reason()
        self.assertIn("commands/overnight.md §Step 2", reason)
        self.assertNotIn("reversible from the branch", reason)

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

    def test_a_q_row_always_carries_a_recommendation_and_no_recommendation_park_exists(self):
        # Lack of a recommendation is never a park reason: the run gathers evidence and decides.
        self.arm()
        for line, ok in (
            (self.new_row(reason="no-recommendation"), False),
            (self.new_row(rec="none"), False),
            (self.new_row(rec="a"), True),
        ):
            with self.subTest(row=line):
                result = self.call("--validate-close", str(self.close_doc(q_line=line)))
                self.assertEqual(ok, result.returncode == 0, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

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

    RESOLVED = "Resolved: 2026-10-01 — a — done"

    def test_an_open_q_row_needs_a_verified_evidence_item(self):
        row = self.new_row()
        missing = self.validate(row, evidence=None)
        self.assertEqual(2, missing.returncode)
        self.assertIn("Q1", missing.stderr)
        self.assertIn("Evidence", missing.stderr)
        absent = self.validate(row, evidence='Evidence: {"file": "settled.txt", "quote": "never written there"}')
        self.assertEqual(2, absent.returncode)
        self.assertIn("Q1", absent.stderr)
        for bad in ("Evidence: unknown", 'Evidence: {"owner": "go ahead"}', 'Evidence: {"file": "settled.txt"}'):
            with self.subTest(line=bad):
                self.assertEqual(2, self.validate(row, evidence=bad).returncode)
        present = self.validate(row)
        self.assertEqual(0, present.returncode, present.stderr)
        command = self.validate(row, evidence='Evidence: {"command": "git status", "output": "no such text"}')
        self.assertEqual(2, command.returncode)

    def test_a_resolved_q_row_needs_no_evidence_line(self):
        for lines in (self.RESOLVED, self.EVIDENCE + chr(10) + self.RESOLVED):
            with self.subTest(lines=lines):
                result = self.validate(self.new_row() + chr(10) + lines, evidence=None)
                self.assertEqual(0, result.returncode, result.stderr)

    def test_open_questions_reads_rows_with_and_without_the_evidence_line(self):
        out = self.questions({"a.md": chr(10).join([
            "## Decisions for you", "", self.new_row(), self.EVIDENCE, "",
            self.new_row().replace("Q1", "Q2"), "",
            self.new_row().replace("Q1", "Q3"), self.EVIDENCE, self.RESOLVED])})
        self.assertEqual([1, 2], sorted(r["number"] for r in out["open"]))
        self.assertEqual([3], [r["number"] for r in out["resolved"]])

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

    def test_open_questions_without_a_dir_reads_the_overnight_dir_from_adaptation_json(self):
        folder = self.root / "vault" / "Overnight"
        folder.mkdir(parents=True)
        (folder / "a.md").write_text("## Decisions for you\n\n**Q1 — x? Options: O1 a; O2 b. Default: a.**\n",
                                     encoding="utf-8")
        self.plant_overnight_dir(folder)
        result = self.call("--open-questions")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(1, json.loads(result.stdout)["docs_scanned"])
        self.assertEqual(0, self.call("--archive-closed").returncode)

    def test_no_source_holds_a_placeholder_token_a_consumer_install_would_expand(self):
        pattern = re.compile(r"\{\{[A-Z_]+\}\}")
        for path in (HOOK, Path(__file__)):
            self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")), path.name)

    def test_open_questions_without_a_dir_or_an_overnight_dir_fails_with_the_missing_source(self):
        result = self.call("--open-questions")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("overnight_dir", result.stderr)

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

    def test_a_linked_worktree_cwd_arms_and_reads_the_main_checkout_marker(self):
        main, linked = self.root / "main", self.root / "linked"
        main.mkdir()
        git_env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "init"],
                     ["worktree", "add", "-q", str(linked)]):
            subprocess.run(["git", *args], cwd=main, env=git_env, check=True, capture_output=True)
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        env.update({"CLAUDE_CODE_SESSION_ID": "one", "CLAUDE_SESSION_ID": ""})
        with mock.patch.dict(os.environ, env, clear=True):
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
            cwd = os.getcwd()
            os.chdir(linked)
            try:
                arm = self.run_main(["--arm", "finish"], {}, os.environ.copy(), real_root=True)
                status = self.run_main(["--status"], {}, os.environ.copy(), real_root=True)
            finally:
                os.chdir(cwd)
        self.assertEqual(0, arm.returncode, arm.stderr)
        self.assertTrue((main / ".claude/scratch/overnight/active-one.json").exists())
        self.assertFalse((linked / ".claude/scratch/overnight/active-one.json").exists())
        self.assertTrue(json.loads(status.stdout)["armed"])

    def test_the_spawned_script_arms_and_denies_an_ask(self):
        """The one spawned case: the script as the harness runs it, argv for --arm and a stdin payload for the hook."""
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.root), "CLAUDE_CODE_SESSION_ID": "one",
               "CLAUDE_SESSION_ID": ""}
        arm = subprocess.run([sys.executable, str(HOOK), "--arm", "finish"], text=True, capture_output=True,
                             env=env, timeout=60)
        self.assertEqual(0, arm.returncode, arm.stderr)
        ask = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(
            {"tool_name": "AskUserQuestion", "session_id": "one", "cwd": str(self.root)}),
            text=True, capture_output=True, env=env, timeout=60)
        self.assertEqual(0, ask.returncode, ask.stderr)
        self.assertEqual("deny", json.loads(ask.stdout)["hookSpecificOutput"]["permissionDecision"])

    def test_malformed_hook_input_fails_open(self):
        result = self.call(payload=["not an object"])
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stdout)


if __name__ == "__main__":
    from _case_pool import run_cases, unittest_cases
    sys.exit(run_cases(unittest_cases(OvernightTests)))
