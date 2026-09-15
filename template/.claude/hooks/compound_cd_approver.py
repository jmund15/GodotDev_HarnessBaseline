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
import os
import re
import sys

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.join(os.path.dirname(_HOOKS_DIR), "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
import adaptation  # noqa: E402

# Pattern: command starts with cd (possibly quoted path) followed by &&
CD_COMPOUND_PATTERN = re.compile(r'^cd\s+(".*?"|\'.*?\'|\S+)\s*&&')

# First-word allowlist for post-cd segments: read-only inspection + VCS/build
# commands only. Deliberately excludes interpreters (python/node/npx — inline
# `-c` code would ride the approval) and anything destructive (rm/del/mv/cp).
# Excluded commands fall through to the normal permission prompt. A project adds
# its own stack's read-only build/inspect tools (e.g. "dotnet") via `adaptation.json`
# `read_only_commands` (Design Doc §8) — never by editing this frozenset.
SAFE_SEGMENT_COMMANDS = frozenset({
    "cd", "git", "ls", "dir", "pwd", "cat", "head", "tail",
    "grep", "find", "echo", "wc", "sort", "uniq", "tree", "stat", "test",
    "where", "which",
})

# Never auto-approvable, whatever a project lists in `adaptation.json`: interpreters and
# shells (arbitrary inline code would ride the approval) and anything that deletes, moves,
# copies, writes or reaches the network.
NEVER_SAFE = frozenset({
    "python", "python3", "py", "node", "npx", "npm", "node.js",
    "bash", "sh", "zsh", "pwsh", "powershell", "cmd", "cmd.exe",
    "rm", "del", "erase", "rd", "rmdir", "mv", "move", "cp", "copy", "xcopy",
    "robocopy", "write", "tee", "curl", "wget", "ssh", "scp", "ftp", "nc",
})

_READ_ONLY_COMMAND_RE = re.compile(r"^[a-z0-9_.-]+$")

# Segment separators: &&, ||, ;, | (pipe last so || is consumed first).
_SEGMENT_SPLIT = re.compile(r'&&|\|\||;|\|')


def _effective_safe_commands() -> frozenset:
    """`SAFE_SEGMENT_COMMANDS` plus `adaptation.json` `read_only_commands`, each entry
    validated against the shape and `NEVER_SAFE` rules above; a rejected entry is skipped
    with one stderr line and never joins the allowlist."""
    extra = adaptation.get(os.path.dirname(_HOOKS_DIR), "read_only_commands")
    accepted = set(SAFE_SEGMENT_COMMANDS)
    for entry in extra:
        if not isinstance(entry, str) or not _READ_ONLY_COMMAND_RE.match(entry):
            print(
                f"compound_cd_approver: read_only_commands entry {entry!r} does not match "
                "^[a-z0-9_.-]+$ -- skipped",
                file=sys.stderr,
            )
            continue
        if entry in NEVER_SAFE:
            print(
                f"compound_cd_approver: read_only_commands entry {entry!r} is in NEVER_SAFE "
                "-- skipped",
                file=sys.stderr,
            )
            continue
        accepted.add(entry)
    return frozenset(accepted)


def _all_segments_safe(command: str, safe_commands: frozenset) -> bool:
    """True when every segment's first word is allowlisted."""
    for segment in _SEGMENT_SPLIT.split(command):
        words = segment.strip().split()
        if not words:
            continue
        if words[0].lower() not in safe_commands:
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

    if CD_COMPOUND_PATTERN.match(command) and _all_segments_safe(command, _effective_safe_commands()):
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
