#!/usr/bin/env python3
"""Proof for the skill marker a compaction keeps (`_hook_state.skill_in_context` with a transcript).

A skill keeps its in-context marker across a compaction exactly when the client's `invoked_skills` attachment
after the boundary carries it whole; a truncated or dropped copy, an attachment from before the PreCompact clear,
and a subagent's context do not count. A re-attached copy counts only while its body still matches the skill's
`SKILL.md` on disk. The fixture is real rows cut from a captured session transcript
(tests/fixtures/compaction_state).

    python3 .claude/tests/test_compaction_state.py
"""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
CLAUDE = HERE.parent
sys.path[:0] = [str(CLAUDE / "hooks")]
STATE = tempfile.mkdtemp(prefix="compaction_state_")
os.environ["HARNESS_HOOK_STATE_DIR"] = STATE
os.environ.pop("CLAUDE_CODE_SIDECAR", None)

import _hook_state  # noqa: E402

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
    SKILL.md body with its frontmatter removed."""
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
    attachment, then (once the hooks ran and the session continued) a SessionStart hook row and a turn."""
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


class RetentionTest(unittest.TestCase):
    """Latest attachment (cut from line 43100): orchestration, workflow-authoring, self_evaluate and
    sync_subsystems whole; plan_check and instruction_quality truncated. The earlier one (line 40781) carried
    autolearn whole, which the latest dropped."""

    def compacted(self, sid, *invoked, boundary_after_clear=True, attachment="invoked_skills"):
        for name in invoked:
            _hook_state.mark_skill_in_context(sid, name)
        now = time.time()
        if boundary_after_clear:
            _hook_state.clear_compaction_keys(sid)
            latest = iso(now + 60)
        else:
            latest = iso(now - 60)
            _hook_state.clear_compaction_keys(sid)
        rows = segment(iso(now - 3600), attachment="invoked_skills_prev") + segment(latest, attachment=attachment)
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

    # The fallback root is `CLAUDE_PROJECT_DIR`, which a Git Bash child spells `/c/Users/...`; Windows Python
    # cannot open that. An unreadable root must never read as a current copy.
    def test_an_msys_spelled_project_dir_still_finds_the_skill(self):
        if os.name != "nt":
            self.skipTest("the `/c/...` spelling is a Windows one")
        drive = self.ROOT.replace("\\", "/")
        msys = "/" + drive[0].lower() + drive[2:]
        path = self.planted("fresh005", row("invoked_skills_stale"))
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": msys}):
            self.assertFalse(_hook_state.skill_in_context("fresh005", "orchestration", transcript_path=path))
        path = self.planted("fresh006", current_attachment("orchestration"))
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": msys}):
            self.assertTrue(_hook_state.skill_in_context("fresh006", "orchestration", transcript_path=path))

    def test_a_root_that_is_no_directory_credits_no_copy(self):
        path = self.planted("fresh007", current_attachment("orchestration"))
        gone = os.path.join(tempfile.gettempdir(), "no_such_project_root_fresh007")
        self.assertFalse(_hook_state.skill_in_context("fresh007", "orchestration", transcript_path=path, root=gone))


if __name__ == "__main__":
    unittest.main()
