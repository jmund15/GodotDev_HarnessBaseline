"""The playtest checklist generator's frontmatter grants the live-source calls its steps make.

Claude Code parses `allowed-tools` from the command's frontmatter, so a missing grant stops the step at run time.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / ".claude/commands/pr_test_checklist.md"


def test_generator_frontmatter_allows_its_live_source_calls():
    frontmatter = "\n".join(GENERATOR.read_text(encoding="utf-8").splitlines()[:5])
    for call in (
        "Bash(gh pr list:*)",
        "Bash(gh pr view:*)",
        "Bash(git fetch:*)",
        "Bash(git rev-parse:*)",
        "Bash(python3 .claude/tools/playtest_checklist_lint.py:*)",
    ):
        assert call in frontmatter, call


if __name__ == "__main__":
    test_generator_frontmatter_allows_its_live_source_calls()
    print("ok")
