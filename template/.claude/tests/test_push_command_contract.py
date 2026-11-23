#!/usr/bin/env python3
"""Structural proof for the push and pull commands: each frontmatter `allowed-tools` grants the calls its steps make.

Claude Code parses `allowed-tools` from the command's frontmatter; a missing grant makes the step ask or fail at run time.

    python -m pytest .claude/tests/test_push_command_contract.py -q
"""
import re
from pathlib import Path

import pytest

COMMANDS = Path(__file__).resolve().parents[1] / "commands"
GRANTS = [
    ("commit_push.md", "Bash(python3:*)"),
    ("clean_pull.md", "Bash(git ls-tree:*)"),
    ("clean_pull.md", "Bash(git ls-files:*)"),
    ("clean_pull.md", "Bash(git submodule:*)"),
    ("clean_pull.md", "Bash(git -C:*)"),
    ("clean_pull.md", "Bash(mv:*)"),
]


def allowed_tools(text):
    front = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    tools = re.search(r"^allowed-tools: (.*)$", front.group(1), re.MULTILINE) if front else None
    return tools.group(1) if tools else ""


@pytest.mark.parametrize("name,grant", GRANTS)
def test_command_grants(name, grant):
    path = COMMANDS / name
    assert path.exists(), name
    assert grant in allowed_tools(path.read_text(encoding="utf-8"))
