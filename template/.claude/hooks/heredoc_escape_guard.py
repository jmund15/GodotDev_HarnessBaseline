#!/usr/bin/env python3
"""Deny a python program piped through a heredoc when its body carries backslash escapes.

CLAUDE.md Shell Discipline already states this: "a multi-line python program piped through a heredoc
fails on `unexpected EOF` or an escape error whenever its own body carries nested quotes. `Write` the
script and run `python3 <file>`." The rule was documented and still broken FIVE times in one session
(2026-09-08), each time silently corrupting a patch — `\\n` collapsing to a real newline, producing a
file that parses but is wrong, or a replacement that matched zero times and reported success.

A documented rule broken five times is an unenforced rule. This is the enforcement.

The trigger is narrow on purpose: a heredoc feeding python on STDIN, whose body contains a
backslash. A heredoc with no backslash round-trips fine and stays allowed — a guard that blocks
every heredoc gets worked around instead of obeyed.
"""
import json
import re
import sys

# `python3 - <<'PY'`, `python <<EOF`, `python3 - << "X"` — an interpreter reading stdin from a heredoc.
# The lookbehind keeps the interpreter a COMMAND, not a filename: `cat > triage.py <<'PY'` writes a
# script (what CLAUDE.md prescribes) and must pass, but `\b` alone treats the `.py` suffix as a match.
# `/` and `\` are deliberately NOT in the class: excluding them made a path-qualified interpreter
# (`/usr/bin/python3 - <<PY`) invisible, which is the same command with an absolute path. What marks
# a filename is the DOT before the suffix, and that is what this rejects.
#
# The operand list may hold flags and `-`, but NOT a script path: `python3 build.py <<EOF` feeds the
# heredoc to that script as DATA, where a backslash is the data's own and rewriting it is wrong.
# Only `python3 <<EOF` and `python3 - <<EOF` put the heredoc where python PARSES it.
STDIN_HEREDOC = re.compile(
    r"(?<![.\w-])(python3?|py)\b(?:\s+(?:-[A-Za-z]\w*|-))*\s*<<-?\s*"
    r"(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")


def bodies(cmd):
    """(tag, body) for each heredoc feeding python on stdin."""
    out = []
    for m in STDIN_HEREDOC.finditer(cmd):
        tag = m.group(3)
        rest = cmd[m.end():]
        end = re.search(r"^\s*%s\s*$" % re.escape(tag), rest, re.M)
        out.append((tag, rest[:end.start()] if end else rest))
    return out


def verdict(cmd):
    if "<<" not in cmd:
        return None
    bad = []
    for tag, body in bodies(cmd):
        # The backslash is the whole trigger: every observed failure was an escape that the shell
        # consumed before python ever saw it.
        escapes = sorted({m.group(0) for m in re.finditer(r"\\.", body)})
        if escapes:
            bad.append((tag, escapes[:6], len(body.splitlines())))
    if not bad:
        return None
    lines = ["BLOCKED heredoc — a python program on stdin whose body carries backslash escapes.",
             "The shell consumes them before python sees them: `\\n` becomes a real newline, and the "
             "patch either fails to match or writes a file that parses and is WRONG."]
    for tag, esc, n in bad:
        lines.append("  <<%s (%d lines) contains: %s" % (tag, n, " ".join(esc)))
    lines.append("")
    lines.append("Fix (CLAUDE.md Shell Discipline): Write the script to a file, then run it.")
    lines.append("  Write  .claude/scratch/<name>.py   ->   python3 .claude/scratch/<name>.py")
    lines.append("For a one-line edit to a known string, use the Edit tool instead — no quoting at all.")
    return "\n".join(lines)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    why = verdict((payload.get("tool_input") or {}).get("command") or "")
    if why:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": why}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
