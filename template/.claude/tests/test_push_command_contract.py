#!/usr/bin/env python3
"""Structural proof for the push and pull commands: each frontmatter `allowed-tools` grants the calls its steps make.

Claude Code parses `allowed-tools` from the command's frontmatter; a missing grant makes the step ask or fail at run time.

    python3 .claude/tests/test_push_command_contract.py
"""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
COMMANDS = ROOT / ".claude" / "commands"
GRANTS = {
    "commit_push.md": ("Bash(python3:*)",),
    "clean_push.md": ("Bash(python3:*)",),
    "clean_pull.md": ("Bash(git ls-tree:*)", "Bash(git ls-files:*)", "Bash(git submodule:*)",
                      "Bash(git -C:*)", "Bash(mv:*)"),
}


def allowed_tools(text):
    front = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    tools = re.search(r"^allowed-tools: (.*)$", front.group(1), re.MULTILINE) if front else None
    return tools.group(1) if tools else ""


def main():
    failed = 0
    for name, grants in GRANTS.items():
        path = COMMANDS / name
        tools = allowed_tools(path.read_text(encoding="utf-8")) if path.exists() else ""
        for grant in grants:
            ok = grant in tools
            print(("ok   " if ok else "FAIL ") + "%s grants %s" % (name, grant))
            failed += not ok
    print("push command contract: %d failed" % failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
