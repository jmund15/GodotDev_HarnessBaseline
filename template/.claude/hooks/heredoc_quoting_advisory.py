#!/usr/bin/env python3
r"""pre_bash_dispatch sub-hook: advise against a quoted heredoc body that mixes a quote with a hazard.

Class: process (advisory). It never denies. A quoted heredoc whose body mixes a quote with a
backtick, `${` or a run of two or more backslashes can reach bash altered, and bash then reports
`unexpected EOF while looking for matching`. The advice names the route that parses: write the
program to a file with the Write tool, then run that file.

Channel: hookSpecificOutput.additionalContext.
Proof: tests/test_heredoc_quoting_advisory.py.
"""
import json
import re
import os
import sys

QUOTE = re.compile(r"['\"]")
HAZARD = re.compile(r"`|\$\{|\\\\+")
# A quoted opener, with a redirect or pipe allowed after it. Text after the opener holding a quote
# is a quoted mention (`echo "<<'EOF' ..."`), not an opener.
OPENER = re.compile(r"<<-?\s*(['\"])([A-Za-z_][A-Za-z0-9_]*)\1[^'\"\n]*$")
HERESTRING = re.compile(r"<<<")
ADVICE = ("[heredoc-quoting] This quoted heredoc body mixes a quote with a backtick, `${` or a backslash "
          "run. Bash can report `unexpected EOF while looking for matching`. Write the program to a file "
          "with the Write tool (under .claude/scratch/), then run that file; do not resend it as a heredoc.")


def quoted_bodies(command):
    """The body of each quoted-delimiter heredoc in `command`."""
    lines = command.split("\n")
    i = 0
    while i < len(lines):
        m = None if HERESTRING.search(lines[i]) else OPENER.search(lines[i])
        i += 1
        if not m:
            continue
        body = []
        while i < len(lines) and lines[i].strip() != m.group(2):
            body.append(lines[i])
            i += 1
        i += 1
        yield "\n".join(body)


def hazardous(command):
    return any(QUOTE.search(body) and HAZARD.search(body) for body in quoted_bodies(command))


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
            return 0
        command = (payload.get("tool_input") or {}).get("command")
        if not isinstance(command, str) or "<<" not in command or not hazardous(command):
            return 0
    except Exception:
        return 0
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": ADVICE}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
