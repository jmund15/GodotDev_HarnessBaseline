#!/usr/bin/env python3
"""Proof for the post-compaction state (hooks/_compaction_record.py and its readers).

1. The SessionStart:compact rails state the compaction ordinal and the previous boundary time from the
   transcript's `compact_boundary` rows, whether or not the client has written the current boundary yet.
2. Only a session that dispatched gets the dispatch-doctrine line, and that line is the gate's own rule text,
   built from the same step strings the gate's deny uses.
3. The digest counts one compaction per `compact_boundary` row: the continuation summary also appears in an
   `api-request-blob` row, which the old summary-text count counted a second time.
4. A skill keeps its in-context marker across a compaction exactly when the client's `invoked_skills`
   attachment after the boundary carries it whole; a truncated or dropped copy, an attachment from an earlier
   compaction, and a subagent's context do not count. Both guards that read the marker agree.

The fixture is real rows cut from a captured session transcript (tests/fixtures/compaction_state).

    python3 .claude/tests/test_compaction_state.py
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
CLAUDE = HERE.parent
sys.path[:0] = [str(CLAUDE / "hooks"), str(CLAUDE / "tools")]
STATE = tempfile.mkdtemp(prefix="compaction_state_")
os.environ["HARNESS_HOOK_STATE_DIR"] = STATE

import _hook_state  # noqa: E402
import _transcript_summary  # noqa: E402
import harness_edit_skill_reminder  # noqa: E402
import model_ladder_gate  # noqa: E402
import session_model_rails  # noqa: E402
from test_sidecar_dispatch_context import REPO, arm_ladder, sidecar  # noqa: E402

FIXTURE = {}
for _line in (HERE / "fixtures" / "compaction_state" / "rows.jsonl").read_text(encoding="utf-8").splitlines():
    _item = json.loads(_line)
    FIXTURE[_item["label"]] = _item["row"]


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def row(label, **overrides):
    out = json.loads(json.dumps(FIXTURE[label]))
    out.update(overrides)
    return out


def skill_copy(name):
    """The content the client attaches for skill `name`: a `Base directory for this skill:` line, then the
    SKILL.md body with its frontmatter removed. That is the shape cut from the captured attachment at
    transcript line 2799."""
    path = CLAUDE / "skills" / name / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:].lstrip("\n")
    return "Base directory for this skill: %s\n\n%s" % (path.parent, text)


def current_attachment(*names, fixture="invoked_skills"):
    """An `invoked_skills` attachment carrying the current bytes of each named skill. A skill with no
    project SKILL.md (a bundled one) keeps the fixture's whole copy, which no disk change can stale."""
    by_name = {s["name"]: s["content"] for s in row(fixture)["attachment"]["skills"]}
    skills = []
    for name in names:
        try:
            content = skill_copy(name)
        except OSError:
            content = by_name[name]
        skills.append({"name": name, "path": "projectSettings:" + name, "content": content})
    return {"type": "attachment", "attachment": {"type": "invoked_skills", "skills": skills}}


def segment(boundary_ts, attachment="invoked_skills", settled=True):
    """One compaction as the client writes it: boundary, summary, its api-request-blob copy, the skills
    attachment (a fixture label or a row), then (once the hooks ran and the session continued) a
    SessionStart hook row and a turn."""
    rows = [row("boundary_manual", timestamp=boundary_ts), row("compact_summary"), row("api_request_blob"),
            attachment if isinstance(attachment, dict) else row(attachment)]
    if settled:
        rows += [row("sessionstart_hook"), row("assistant_text")]
    return rows


class Planted:
    def __init__(self, test, rows):
        handle, self.path = tempfile.mkstemp(suffix=".jsonl", prefix="planted_")
        os.close(handle)
        with open(self.path, "w", encoding="utf-8", newline="\n") as fh:
            for item in rows:
                fh.write(json.dumps(item, separators=(",", ":")) + "\n")
        test.addCleanup(os.unlink, self.path)


T1 = "2026-10-19T09:19:52.678Z"
T2 = "2026-10-19T15:30:44.038Z"


def compact_lines(test, rows, sid):
    planted = Planted(test, rows)
    return session_model_rails.compaction_lines({"session_id": sid, "transcript_path": planted.path,
                                                 "source": "compact", "cwd": str(CLAUDE.parent)})


class OrdinalTest(unittest.TestCase):
    def test_a_settled_last_boundary_is_the_previous_one(self):
        rows = [row("assistant_text")] + segment(T1) + segment(T2)
        lines = compact_lines(self, rows, "ord00001")
        head = lines[0]
        self.assertIn("Compaction 3 of this session", head)
        self.assertIn("2026-10-19T15:30:44Z", head)

    def test_a_boundary_already_written_is_the_current_one(self):
        rows = [row("assistant_text")] + segment(T1) + segment(T2, settled=False)
        head = compact_lines(self, rows, "ord00002")[0]
        self.assertIn("Compaction 2 of this session", head)
        self.assertIn("2026-10-19T09:19:52Z", head)

    def test_a_first_compaction_names_no_previous_time(self):
        head = compact_lines(self, [row("assistant_text")], "ord00003")[0]
        self.assertIn("Compaction 1 of this session", head)
        self.assertNotIn("previous", head)


class DoctrineLineTest(unittest.TestCase):
    def payload(self, path):
        return {"session_id": "doc00001", "transcript_path": path, "cwd": str(CLAUDE.parent)}

    def test_no_dispatch_no_doctrine_line(self):
        lines = compact_lines(self, [row("assistant_text"), row("fanout_mention")] + segment(T1), "doc00001")
        self.assertFalse(any("ladder-slice" in line for line in lines), lines)

    def test_a_workflow_or_fanout_dispatch_gets_the_gates_own_rule(self):
        for label in ("workflow_dispatch", "fanout_dispatch"):
            planted = Planted(self, [row(label)] + segment(T1))
            lines = session_model_rails.compaction_lines(dict(self.payload(planted.path), source="compact"))
            rule = model_ladder_gate.doctrine_rule(self.payload(planted.path))
            self.assertTrue(any(line.endswith(rule) for line in lines), (label, lines))

    def test_the_rule_and_the_deny_share_each_step_string(self):
        payload = {"session_id": "doc00002", "cwd": str(CLAUDE.parent)}
        rule = model_ladder_gate.doctrine_rule(payload)
        deny, _ = model_ladder_gate.doctrine_check(payload)
        read = model_ladder_gate.READ_STEP.format(path=model_ladder_gate.expected_path(payload))
        for step in (model_ladder_gate.SKILL_STEP, read):
            self.assertIn(step, rule)
            self.assertIn(step, deny)
        deny_tiers, _ = model_ladder_gate.doctrine_check(payload, tiers=lambda: ["architect"], embed_slice=False)
        self.assertIn(model_ladder_gate.SLICE_STEP.format(which="architect", tiers="architect"),
                      deny_tiers)
        self.assertIn(model_ladder_gate.SLICE_STEP.format(which="each tier it pins", tiers="<tier>..."), rule)


class DigestCountTest(unittest.TestCase):
    def test_one_compaction_per_boundary_row(self):
        planted = Planted(self, [row("assistant_text")] + segment(T1) + segment(T2))
        builder = _transcript_summary.TranscriptSummaryBuilder("dig00001", planted.path)
        with open(planted.path, encoding="utf-8") as fh:
            for line in fh:
                builder.process_line(line)
        result = builder.finalize()
        self.assertEqual(result["compactions"]["count"], 2)
        self.assertEqual(result["compactions"]["timestamps"], [T1, T2])
        self.assertFalse(any("This session is being continued" in str(m) for m in result["user_messages"]))


class RetentionTest(unittest.TestCase):
    """Latest attachment (cut from line 43100): orchestration, workflow-authoring, self_evaluate and
    sync_subsystems whole; plan_check and instruction_quality truncated. The earlier one (line 40781) carried
    autolearn whole, which the latest dropped."""

    def compacted(self, sid, *invoked, boundary_after_clear=True, settled=True, attachment="invoked_skills"):
        for name in invoked:
            _hook_state.mark_skill_in_context(sid, name)
        now = time.time()
        if boundary_after_clear:
            _hook_state.clear_compaction_keys(sid)
            latest = iso(now + 60)
        else:
            latest = iso(now - 60)
            _hook_state.clear_compaction_keys(sid)
        rows = segment(iso(now - 3600), attachment="invoked_skills_prev") + segment(
            latest, attachment=attachment, settled=settled)
        return Planted(self, rows).path

    def test_a_skill_reattached_whole_keeps_its_marker(self):
        current = current_attachment("orchestration", "workflow-authoring")
        path = self.compacted("ret00001", "orchestration", "workflow-authoring", attachment=current)
        self.assertTrue(_hook_state.skill_in_context("ret00001", "orchestration", transcript_path=path))
        self.assertTrue(_hook_state.skill_in_context("ret00001", "workflow-authoring", transcript_path=path))

    def test_any_skill_with_no_fixed_list(self):
        path = self.compacted("ret00002")
        self.assertTrue(_hook_state.skill_in_context("ret00002", "sync_subsystems", transcript_path=path))

    def test_a_truncated_copy_loses_its_marker(self):
        path = self.compacted("ret00003", "instruction_quality", "plan_check")
        self.assertFalse(_hook_state.skill_in_context("ret00003", "instruction_quality", transcript_path=path))
        self.assertFalse(_hook_state.skill_in_context("ret00003", "plan_check", transcript_path=path))

    def test_a_dropped_skill_loses_its_marker(self):
        path = self.compacted("ret00004", "autolearn")
        self.assertFalse(_hook_state.skill_in_context("ret00004", "autolearn", transcript_path=path))

    def test_an_attachment_from_before_the_clear_does_not_count(self):
        path = self.compacted("ret00005", "orchestration", boundary_after_clear=False)
        self.assertFalse(_hook_state.skill_in_context("ret00005", "orchestration", transcript_path=path))

    def test_a_subagent_context_is_not_the_attachment(self):
        path = self.compacted("ret00006", "orchestration")
        self.assertFalse(_hook_state.skill_in_context("ret00006", "orchestration", "agent1", transcript_path=path))

    def test_without_the_transcript_nothing_is_predicted(self):
        self.compacted("ret00007", "orchestration")
        self.assertFalse(_hook_state.skill_in_context("ret00007", "orchestration"))

    def test_the_edit_guard_agrees_with_the_attachment(self):
        truncated = self.compacted("ret00008", "instruction_quality")
        edit = {"tool_name": "Edit", "session_id": "ret00008", "transcript_path": truncated,
                "tool_input": {"file_path": str(CLAUDE / "rules" / "harness_tooling.md")}}
        self.assertIn("deny", harness_edit_skill_reminder.process(edit) or {})

        _hook_state.mark_skill_in_context("ret00011", "instruction_quality")
        _hook_state.clear_compaction_keys("ret00011")
        attached = current_attachment("instruction_quality")
        rows = [row("boundary_manual", timestamp=iso(time.time() + 60)), row("compact_summary"), attached]
        whole = Planted(self, rows).path
        edit = dict(edit, session_id="ret00011", transcript_path=whole)
        self.assertNotIn("deny", harness_edit_skill_reminder.process(edit) or {})

    def test_the_dispatch_gate_agrees_with_the_attachment(self):
        path = self.compacted("ret00009", "orchestration", attachment=current_attachment("orchestration"))
        deny, _ = model_ladder_gate.doctrine_check({"session_id": "ret00009", "transcript_path": path,
                                                    "cwd": str(CLAUDE.parent)})
        self.assertNotIn(model_ladder_gate.SKILL_STEP, deny)
        self.assertIn("ladder", deny)

    def test_the_dispatch_gate_still_denies_a_truncated_skill(self):
        _hook_state.mark_skill_in_context("ret00010", "orchestration")
        _hook_state.clear_compaction_keys("ret00010")
        rows = segment(iso(time.time() + 60), attachment="invoked_skills_prev")
        path = Planted(self, rows).path
        deny, _ = model_ladder_gate.doctrine_check({"session_id": "ret00010", "transcript_path": path,
                                                    "cwd": str(CLAUDE.parent)})
        self.assertIn(model_ladder_gate.SKILL_STEP, deny)


class FreshnessTest(unittest.TestCase):
    """A re-attached copy earns its marker only while its body still matches the skill's SKILL.md on disk.
    The stale arm replays the attachment the client wrote at transcript line 2799, trimmed to `orchestration`."""

    ROOT = str(CLAUDE.parent)

    def planted(self, sid, attachment):
        _hook_state.clear_compaction_keys(sid)
        rows = [row("boundary_manual", timestamp=iso(time.time() + 60)), row("compact_summary"), attachment]
        return Planted(self, rows).path

    def test_the_captured_stale_copy_is_not_credited(self):
        path = self.planted("fresh001", row("invoked_skills_stale"))
        self.assertFalse(_hook_state.skill_in_context("fresh001", "orchestration", transcript_path=path,
                                                      root=self.ROOT))

    def test_the_current_bytes_are_credited(self):
        path = self.planted("fresh002", current_attachment("orchestration"))
        self.assertTrue(_hook_state.skill_in_context("fresh002", "orchestration", transcript_path=path,
                                                     root=self.ROOT))

    def test_a_truncated_copy_of_the_current_bytes_is_not_credited(self):
        truncated = skill_copy("instruction_quality")[:800] + \
            "\n[... truncated for compaction; use Read on the skill path ...]"
        attachment = {"type": "attachment", "attachment": {"type": "invoked_skills", "skills": [
            {"name": "instruction_quality", "path": "projectSettings:instruction_quality", "content": truncated}]}}
        path = self.planted("fresh003", attachment)
        self.assertFalse(_hook_state.skill_in_context("fresh003", "instruction_quality", transcript_path=path,
                                                      root=self.ROOT))

    def test_an_edited_body_is_not_credited(self):
        content = skill_copy("orchestration").replace("a pipe, `head`", "an OLD SENTENCE", 1)
        attachment = {"type": "attachment", "attachment": {"type": "invoked_skills", "skills": [
            {"name": "orchestration", "path": "projectSettings:orchestration", "content": content}]}}
        path = self.planted("fresh004", attachment)
        self.assertFalse(_hook_state.skill_in_context("fresh004", "orchestration", transcript_path=path,
                                                      root=self.ROOT))


class ReattachTest(unittest.TestCase):
    """A session whose compaction cleared every marker; the client re-attached `orchestration` whole after the
    clear when `whole`, else not at all. The ladder read is armed so only the skill decides."""

    def planted(self, sid, whole):
        state = tempfile.mkdtemp(prefix="reattach_state_")
        self.addCleanup(shutil.rmtree, state, True)
        env = patch.dict(os.environ, {"HARNESS_HOOK_STATE_DIR": state, "CLAUDE_PROJECT_DIR": str(CLAUDE.parent)})
        env.start()
        self.addCleanup(env.stop)
        now = time.time()
        _hook_state.clear_compaction_keys(sid)
        attachment = current_attachment("orchestration") if whole else "invoked_skills_prev"
        rows = (segment(iso(now - 3600), attachment="invoked_skills_prev")
                + segment(iso(now + 60), attachment=attachment))
        path = Planted(self, rows).path
        arm_ladder({"HARNESS_HOOK_STATE_DIR": state}, sid)
        import dispatch_table
        for target in (patch.object(dispatch_table, "_transcript", lambda session_id, plan=None: str(path)),
                       patch.object(sidecar, "tracking_session", lambda: sid)):
            target.start()
            self.addCleanup(target.stop)
        return path

    def test_fanout_doctrine_refusal_sees_a_skill_the_client_reattached(self):
        for whole in (True, False):
            with self.subTest(whole=whole):
                sid = "reattf%d" % int(whole)
                path = self.planted(sid, whole)
                contract = patch.object(sidecar, "fanout_contract", lambda fanout, root: [])
                contract.start()
                self.addCleanup(contract.stop)
                refusal = sidecar.fanout_doctrine_refusal(["jobs.json"], os.path.dirname(path))
                if whole:
                    self.assertIsNone(refusal, refusal)
                else:
                    self.assertTrue(refusal and 'Skill(skill="orchestration")' in refusal, refusal)

    def test_launcher_launch_sees_a_skill_the_client_reattached(self):
        import dispatch_table
        for whole in (True, False):
            with self.subTest(whole=whole):
                self.planted("reattl%d" % int(whole), whole)
                for target in (
                        patch.object(dispatch_table, "sidecar_entries", lambda payload: ([{"id": "x"}], [])),
                        patch.object(dispatch_table, "register_sidecars", lambda *args, **kwargs: None),
                        patch.object(sidecar, "dispatch_checks", lambda jobs, kinds=None: None),
                        patch.object(sidecar, "take_checks", lambda *args: (False, True))):
                    target.start()
                    self.addCleanup(target.stop)
                launcher = os.path.join(REPO, "scripts", "anthropic_sidecar.sh")
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    code = sidecar.launcher_launch(launcher, ["-m", "fake", "-f", "brief.md", "-G", "author"])
                if whole:
                    self.assertEqual(code, 0, err.getvalue())
                else:
                    self.assertTrue(code == 2 and 'Skill(skill="orchestration")' in err.getvalue(), err.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=1)
