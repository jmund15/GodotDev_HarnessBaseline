"""The playtest checklist generator's frontmatter grants the live-source calls its steps make.

Claude Code parses `allowed-tools` from the command's frontmatter, so a missing grant stops the step at run time.
A lane ships the command version its own layers carry, so this proof judges that body against that frontmatter,
never a call list only this project's newer command text has.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / ".claude/commands/pr_test_checklist.md"
GRANTS = {
    "gh pr list": "Bash(gh pr list:*)",
    "gh pr view": "Bash(gh pr view:*)",
    "git fetch": "Bash(git fetch:*)",
    "git rev-parse": "Bash(git rev-parse:*)",
    "python3 .claude/tools/playtest_checklist_lint.py": "Bash(python3 .claude/tools/playtest_checklist_lint.py:*)",
}


def ungranted(text):
    """The grants a call the body makes is missing from its frontmatter."""
    head, _, body = text.partition("\n---\n")
    return [grant for call, grant in GRANTS.items()
            if re.search(r"(?<![\w-])" + re.escape(call) + r"(?![\w-])", body) and grant not in head]


def test_generator_frontmatter_allows_its_live_source_calls():
    text = GENERATOR.read_text(encoding="utf-8")
    assert ungranted(text) == [], ungranted(text)


def test_an_ungranted_live_source_call_is_reported():
    planted = "---\nallowed-tools: Read\n---\n\nRun `gh pr list` before the checklist.\n"
    assert ungranted(planted) == ["Bash(gh pr list:*)"], ungranted(planted)


if __name__ == "__main__":
    test_generator_frontmatter_allows_its_live_source_calls()
    test_an_ungranted_live_source_call_is_reported()
    print("ok")
