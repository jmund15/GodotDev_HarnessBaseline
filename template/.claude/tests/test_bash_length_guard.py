#!/usr/bin/env python3
"""Re-runnable proof for hooks/bash_length_guard.py, driven through the real dispatcher.

Deny arm: a Bash command longer than the tool's 8191-character cut, heredoc or not, quoted or not; the deny
names the write-to-file route and its rule's home.
Must-allow arms: a command at the limit, a short quoted heredoc mixing a quote with a backtick, a plain heredoc,
a here-string, and a non-Bash tool.

    python3 .claude/tests/test_bash_length_guard.py
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS_DIR = os.path.normpath(os.path.join(HERE, "..", "hooks"))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
DISPATCH = os.path.join(HOOKS_DIR, "pre_bash_dispatch.py")
LIMIT = 8191

BT = "`"
NL = "\n"


def padded(prefix, suffix, total):
    """`prefix` + filler lines + `suffix`, exactly `total` characters."""
    filler = total - len(prefix) - len(suffix)
    body = ("# pad" + NL) * (filler // 6 + 1)
    return prefix + body[:filler] + suffix


def hazard_body():
    return 'print("it' + "'" + "s " + BT + "date" + BT + '")'


CASES = [
    ("deny: quoted heredoc past the limit",
     padded("python3 - <<'EOF'" + NL + hazard_body() + NL, NL + "EOF", LIMIT + 1), True),
    ("deny: plain heredoc past the limit, no hazard",
     padded("cat <<'EOF' > out.txt" + NL, NL + "EOF", LIMIT + 400), True),
    ("deny: long command with no heredoc",
     padded("echo start" + NL, NL + "echo end", LIMIT + 1), True),
    ("allow: command exactly at the limit",
     padded("cat <<'EOF' > out.txt" + NL + hazard_body() + NL, NL + "EOF", LIMIT), False),
    ("allow: short quoted heredoc mixing a quote and a backtick",
     "python3 - <<'EOF'" + NL + hazard_body() + NL + "EOF", False),
    ("allow: short plain heredoc",
     "python3 - <<EOF" + NL + hazard_body() + NL + "EOF", False),
    ("allow: here-string",
     'grep -c <<< "it' + "'" + "s " + BT + 'x' + BT + '" file', False),
]


def run_dispatch(payload_text):
    return subprocess.run([sys.executable, DISPATCH], input=payload_text, capture_output=True,
                          text=True, timeout=120, encoding="utf-8", cwd=REPO)


def decision(proc):
    """(permissionDecision, reason) of the dispatcher's output; ("", "") when it emits none."""
    text = proc.stdout.strip()
    if not text:
        return "", ""
    try:
        parsed = json.loads(text)
    except ValueError:
        return "<unparseable stdout>", ""
    hso = parsed.get("hookSpecificOutput", {}) if isinstance(parsed, dict) else {}
    return hso.get("permissionDecision", "") or "", hso.get("permissionDecisionReason", "") or ""


def main():
    failures = []
    for label, command, want in CASES:
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": REPO})
        proc = run_dispatch(payload)
        if proc.returncode not in (0, 2):
            failures.append("CRASH %s: exit %s %s" % (label, proc.returncode, proc.stderr[-400:]))
            continue
        denied = decision(proc)[0] == "deny"
        if denied != want:
            failures.append("%s: expected deny=%s, got %s" % (label, want, denied))
        else:
            print("ok   %s" % label)

    reason = decision(run_dispatch(json.dumps({"tool_name": "Bash", "tool_input": {"command": CASES[0][1]},
                                               "cwd": REPO})))[1]
    if ".claude/scratch" not in reason or "CLAUDE.core.md" not in reason:
        failures.append("deny does not name the write-to-file path and its rule's home: %r" % reason[:300])

    other = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "x"}, "cwd": REPO})
    if decision(run_dispatch(other))[0] == "deny":
        failures.append("non-Bash tool got the length deny")

    for line in failures:
        print("FAIL " + line)
    print("%d/%d cases passed" % (len(CASES) + 2 - len(failures), len(CASES) + 2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
