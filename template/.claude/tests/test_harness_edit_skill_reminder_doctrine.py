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
            (".claude/worktrees/peer/.claude/rules/x.md", True)):
            with self.subTest(path=path):
                self.assertEqual(reminder.is_doctrine_path(path), expected)


if __name__ == "__main__":
    unittest.main()
