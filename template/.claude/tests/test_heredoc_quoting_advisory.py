#!/usr/bin/env python3
"""Re-runnable proof for hooks/heredoc_quoting_advisory.py, driven through the real dispatcher.

Advisory arms: a quoted heredoc whose body mixes a quote with a backtick, `${` or a backslash run.
Must-allow arms: a plain heredoc, a quoted heredoc with no hazard, a mention that opens no heredoc,
and a non-Bash tool. The advisory never denies.

    python3 .claude/tests/test_heredoc_quoting_advisory.py
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS_DIR = os.path.normpath(os.path.join(HERE, "..", "hooks"))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
DISPATCH = os.path.join(HOOKS_DIR, "pre_bash_dispatch.py")
MARK = "[heredoc-quoting]"

BS = "\\"
BT = "`"
DQ = '"'

CASES = [
    ("advise: single quote + backtick in quoted heredoc",
     "python3 - <<'EOF'\nprint(\"it's " + BT + "date" + BT + "\")\nEOF", True),
    ("advise: double quote + backslash run in quoted heredoc",
     "python3 - <<\"PY\"\nx = " + DQ + "a" + BS * 2 + "b" + DQ + "\nPY", True),
    ("advise: quote + ${ in quoted heredoc",
     "cat <<'EOF' > out.txt\necho " + DQ + "${HOME}" + DQ + "\nEOF", True),
    ("advise: indented terminator, quoted heredoc",
     "python3 - <<-'EOF'\n\tprint(" + DQ + "it's " + BT + "x" + BT + DQ + ")\n\tEOF", True),
    ("allow: plain heredoc, same hazardous body",
     "python3 - <<EOF\nprint(\"it's " + BT + "date" + BT + "\")\nEOF", False),
    ("allow: quoted heredoc, no hazard",
     "python3 - <<'EOF'\nprint(\"hi\")\nEOF", False),
    ("allow: quoted heredoc, backtick but no quote",
     "cat <<'EOF'\necho " + BT + "date" + BT + "\nEOF", False),
    ("allow: here-string, not a heredoc",
     "grep -c <<< \"it's " + BT + "x" + BT + "\" file", False),
    ("allow: mention, opener not at end of line",
     "echo \"<<'EOF' it's " + BT + "x" + BT + "\"", False),
]


def run_dispatch(payload_text):
    return subprocess.run([sys.executable, DISPATCH], input=payload_text, capture_output=True,
                          text=True, timeout=120, encoding="utf-8", cwd=REPO)


def context_of(proc):
    text = proc.stdout.strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except ValueError:
        return "<unparseable stdout>"
    hso = parsed.get("hookSpecificOutput", {}) if isinstance(parsed, dict) else {}
    return hso.get("additionalContext", "") or ""


def main():
    failures = []
    for label, command, want in CASES:
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": REPO})
        proc = run_dispatch(payload)
        if proc.returncode not in (0, 2):
            failures.append("CRASH %s: exit %s %s" % (label, proc.returncode, proc.stderr[-400:]))
            continue
        ctx = context_of(proc)
        got = MARK in ctx
        if '"permissionDecision": "deny"' in proc.stdout:
            failures.append("DENY %s: advisory must never deny" % label)
        if got != want:
            failures.append("%s: expected advisory=%s, got %s" % (label, want, got))
        else:
            print("ok   %s" % label)

    advice_payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": CASES[0][1]}, "cwd": REPO})
    advice = context_of(run_dispatch(advice_payload))
    for needle in ("Write", ".claude/scratch"):
        if needle not in advice:
            failures.append("advice does not name the write-to-file route (missing %r)" % needle)

    other = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "x"}, "cwd": REPO})
    if MARK in context_of(run_dispatch(other)):
        failures.append("non-Bash tool got the heredoc advisory")

    for line in failures:
        print("FAIL " + line)
    print("%d/%d cases passed" % (len(CASES) + 2 - len(failures), len(CASES) + 2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
