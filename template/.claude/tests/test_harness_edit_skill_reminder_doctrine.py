#!/usr/bin/env python3
"""Doctrine is exactly the markdown subset of the edit reminder's governed family."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import harness_edit_skill_reminder as reminder


class DoctrinePaths(unittest.TestCase):
    def test_shared_predicate_admits_doctrine_not_code_or_governed_elsewhere(self):
        for path, expected in (
            (".claude/CLAUDE.core.md", True), (".claude/rules/x.md", True),
            (".claude/commands/x.md", True), (".claude/reference/x.md", True),
            (".claude/pending_harness_edits.md", True), (".claude/auto-memory/x.md", False),
            (".claude/plans/x.md", False), (".claude/scratch/x.md", False),
            (".claude/worklog-titles.md", False), (".claude/hooks/x.py", False),
            (".claude/settings.json", False), ("README.md", False),
            ("C:\\repo\\.claude\\rules\\x.md", True),
            (".claude/worktrees/peer/.claude/rules/x.md", True),
            # Agent-read roots stay governed, each spelling.
            (".claude/skills/a/SKILL.md", True), (".claude/guards/common.md", True),
            (".claude/agents/x.md", True), (".claude/output-styles/terse.md", True),
            (".claude/user_scope/global_claude.md", True), (".claude/CLAUDE.interactive.md", True),
            (".claude/harness-goal.md", True), (".claude/workflows/worklog_relevance.prompt.md", True),
            ("C:/repo/.claude/guards/common.md", True), ("C:\\Repo\\.claude\\Rules\\X.MD", True), ("C:\\repo\\.claude\\skills\\a\\SKILL.md", True),
            # Evidence, state, generated and tool docs are not agent-facing text, and an unnamed root stays out.
            (".claude/work/x.md", False), (".claude/work/overhaul/x.md", False),
            (".claude/logs/x.md", False), (".claude/sessions/x.md", False),
            (".claude/generated/x.md", False), (".claude/cache/x.md", False),
            (".claude/scripts/rail_battery/rules.md", False), (".claude/tools/x.md", False),
            (".claude/hooks/README.md", False), (".claude/prototype_registry.md", False),
            (".claude/newroot/x.md", False),
            (".claude/workflows/dispatch.js", False), (".claude/CLAUDE.md.bak", False),
            ("C:\\repo\\.claude\\work\\x.md", False), ("C:/repo/.claude/logs/x.md", False)):
            with self.subTest(path=path):
                self.assertEqual(reminder.is_doctrine_path(path), expected)


if __name__ == "__main__":
    unittest.main()
