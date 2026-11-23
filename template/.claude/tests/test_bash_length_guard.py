#!/usr/bin/env python3
"""Re-runnable proof for hooks/bash_length_guard.py, driven through the real floor entry.

Deny arm: a Bash command longer than the tool's 8191-character cut, heredoc or not, quoted or not; the deny
names the write-to-file route and its rule's home.
Must-allow arms: a command at the limit, a short quoted heredoc mixing a quote with a backtick, a plain heredoc,
a here-string, and a non-Bash tool.

    python -m pytest .claude/tests/test_bash_length_guard.py -q
"""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS_DIR = os.path.normpath(os.path.join(HERE, "..", "hooks"))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
DISPATCH = os.path.join(HOOKS_DIR, "pre_bash.py")
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


# (id, command, denied)
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


def run_dispatch(command, tool="Bash"):
    payload = {"tool_name": tool, "tool_input": {"command": command} if tool == "Bash" else {"file_path": "x"},
               "cwd": REPO}
    proc = subprocess.run([sys.executable, DISPATCH], input=json.dumps(payload), capture_output=True,
                          text=True, timeout=120, encoding="utf-8", cwd=REPO)
    assert proc.returncode in (0, 2), "CRASH exit %s %s" % (proc.returncode, proc.stderr[-400:])
    return decision(proc)


def decision(proc):
    """(permissionDecision, reason) of the dispatcher's output; ("", "") when it emits none."""
    text = proc.stdout.strip()
    if not text:
        return "", ""
    parsed = json.loads(text)
    hso = parsed.get("hookSpecificOutput", {}) if isinstance(parsed, dict) else {}
    return hso.get("permissionDecision", "") or "", hso.get("permissionDecisionReason", "") or ""


@pytest.mark.parametrize("label,command,want", CASES, ids=[c[0] for c in CASES])
def test_the_length_deny_fires_past_the_limit_only(label, command, want):
    assert (run_dispatch(command)[0] == "deny") == want


def test_the_deny_names_the_write_to_file_route_and_its_rules_home():
    reason = run_dispatch(CASES[0][1])[1]
    assert ".claude/scratch" in reason and "CLAUDE.core.md" in reason, reason[:300]


def test_a_non_bash_tool_gets_no_length_deny():
    assert run_dispatch("", tool="Read")[0] != "deny"
