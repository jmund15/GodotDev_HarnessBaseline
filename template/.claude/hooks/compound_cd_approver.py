#!/usr/bin/env python3
"""
Hook: PreToolUse - Auto-approve compound cd commands (allowlist-gated)

Claude Code's permission wildcard (*) cannot match across && boundaries.
This means `Bash(cd *)` does NOT match `cd /path && git show ...`, causing
repeated permission prompts for safe commands.

IMPORTANT semantics: a PreToolUse `permissionDecision: allow` approves the
ENTIRE command string — the segments after && are NOT individually
re-checked by the permission system. So this hook only auto-approves when
EVERY segment after the leading `cd <path>` starts with an allowlisted
read-only/VCS command. Interpreters (python/node/npx) are deliberately
excluded — auto-approving them would approve arbitrary inline code.
Anything else falls through to the normal permission flow (print "{}",
exit 0) — a prompt, not a block.

Note: This is a convenience net. The preferred approach is to avoid compound
cd commands entirely (use absolute paths, git -C, etc.) per CLAUDE.md rules.
"""

import json
import re
import sys

# Pattern: command starts with cd (possibly quoted path) followed by &&
CD_COMPOUND_PATTERN = re.compile(r'^cd\s+(".*?"|\'.*?\'|\S+)\s*&&')

# First-word allowlist for post-cd segments: read-only inspection + VCS/build
# commands only. Deliberately excludes interpreters (python/node/npx — inline
# `-c` code would ride the approval) and anything destructive (rm/del/mv/cp).
# Excluded commands fall through to the normal permission prompt.
SAFE_SEGMENT_COMMANDS = frozenset({
    "cd", "git", "dotnet", "ls", "dir", "pwd", "cat", "head", "tail",
    "grep", "find", "echo", "wc", "sort", "uniq", "tree", "stat", "test",
    "where", "which",
})

# Segment separators: &&, ||, ;, | (pipe last so || is consumed first).
_SEGMENT_SPLIT = re.compile(r'&&|\|\||;|\|')


def _all_segments_safe(command: str) -> bool:
    """True when every segment's first word is allowlisted."""
    for segment in _SEGMENT_SPLIT.split(command):
        words = segment.strip().split()
        if not words:
            continue
        if words[0].lower() not in SAFE_SEGMENT_COMMANDS:
            return False
    return True


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("{}")
        sys.exit(0)

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    if tool_name != "Bash":
        print("{}")
        sys.exit(0)

    command = tool_input.get("command", "")

    if CD_COMPOUND_PATTERN.match(command) and _all_segments_safe(command):
        result = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": (
                    "Auto-approved: cd compound command — every segment starts "
                    "with an allowlisted read-only/VCS command"
                ),
            }
        }
        print(json.dumps(result))
        sys.exit(0)

    print("{}")
    sys.exit(0)


if __name__ == "__main__":
    main()
