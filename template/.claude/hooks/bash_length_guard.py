#!/usr/bin/env python3
"""pre_bash_dispatch sub-hook: deny a Bash command longer than the tool's 8191-character cut.

Class: process, blocking. The Bash tool runs only the first 8191 characters of a command: the cut lands inside a
quote or heredoc as `unexpected EOF while looking for matching`, or inside a heredoc body and runs silently short.
A call over it is wrong in every case, so none runs (measured: `git log -- .claude/hooks/bash_length_guard.py`).
The rule's home is `CLAUDE.core.md` §Shell Discipline (Bash calls).
Channel: hookSpecificOutput.permissionDecision.
Proof: tests/test_bash_length_guard.py.
"""
import json
import sys

LIMIT = 8191


def reason(length):
    return ("BLOCKED Bash command (CLAUDE.core.md §Shell Discipline): it is %d characters, and the Bash tool cuts a "
            "command past %d, so it would run short or fail. Write the program to a file under .claude/scratch/ "
            "with the Write tool, then run that file." % (length, LIMIT))


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
            return 0
        command = (payload.get("tool_input") or {}).get("command")
        if not isinstance(command, str) or len(command) <= LIMIT:
            return 0
    except Exception:
        return 0
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                        "permissionDecision": "deny",
                                                        "permissionDecisionReason": reason(len(command))}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
