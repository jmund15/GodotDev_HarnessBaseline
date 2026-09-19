#!/usr/bin/env python3
"""Proof for the recursive-grep deny in hooks/unbounded_scan_guard.py, fed through the real
PreToolUse entry hooks/pre_bash_dispatch.py.

Deny arms are the two commands that reached 14.3 GB and 21.5 GB on 2026-09-14 plus the shapes
that run a recursive grep indirectly. Allow arms mention a recursive grep without running one.
A dispatcher exit outside {0, 2}, or a traceback on stderr, is CRASH, never allow.

    python3 .claude/tests/test_recursive_grep_deny.py
"""
import json
import os
import subprocess
import sys
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
DISPATCH = os.path.join(REPO, ".claude", "hooks", "pre_bash_dispatch.py")

DENY = [
    ("Bash", "grep -rln ProjectSuite ."),
    ("Bash", "grep -rn noclobber .claude/"),
    ("Bash", "grep -R foo src"),
    ("Bash", "cd x && grep -rn foo ."),
    ("Bash", "find . -type f | xargs grep -l foo"),
    ("Bash", "find . -name '*.md' -exec grep -l foo {} +"),
    ("PowerShell", "Get-ChildItem -Recurse | Select-String foo"),
    ("Bash", "egrep --recursive foo Tests"),
    ("Bash", "grep -d recurse foo ."),
    ("Bash", "grep --directories=recurse foo ."),
    ("Bash", "git status || fgrep -rl foo ."),
    ("Bash", "ls; grep -nr foo src | wc -l"),
    ("Bash", "printf '%s\\n' a b | xargs -n1 grep -r foo"),
    ("Bash", "find . -exec grep -l foo {} \\;"),
    ("Bash", "bash -c 'grep -rn foo .'"),
    ("Bash", "echo start\ngrep -rn foo ."),
    ("PowerShell", "gci -r -Filter *.cs | sls foo"),
    ("PowerShell", "Select-String -Pattern foo -Path (Get-ChildItem -Recurse -File)"),
    ("PowerShell", "grep -rn foo ."),
]

ALLOW = [
    ("Bash", "rg -n foo"),
    ("Bash", "git grep -n foo"),
    ("Bash", "grep -n foo a.py"),
    ("Bash", "grep -c foo a b"),
    ("Bash", 'echo "grep -r"'),
    ("Bash", 'git commit -m "fix grep -r guard"'),
    ("Bash", "cat > notes.md <<'EOF'\ngrep -rn foo .\nEOF"),
    ("Bash", "printf '%s' 'grep -rln x .'"),
    ("Bash", "grep -e -r foo a.py"),
    ("Bash", "git ls-files | xargs grep -l foo"),
    ("PowerShell", "Get-ChildItem -Recurse -Filter *.cs"),
    ("PowerShell", "Select-String -Pattern foo -Path a.py"),
    ("PowerShell", "Write-Output 'Get-ChildItem -Recurse | Select-String foo'"),
]


def dispatch(tool, command, env):
    payload = json.dumps({"tool_name": tool, "session_id": "rgd" + uuid.uuid4().hex[:8],
                          "hook_event_name": "PreToolUse", "cwd": REPO,
                          "tool_input": {"command": command}})
    p = subprocess.run([sys.executable, DISPATCH], input=payload, capture_output=True, text=True,
                       encoding="utf-8", timeout=90, env=env, cwd=REPO)
    if p.returncode not in (0, 2) or "Traceback" in (p.stderr or ""):
        return "CRASH", "exit %s: %s" % (p.returncode, (p.stderr or "")[-300:])
    if p.returncode == 2:
        return "deny", p.stderr
    try:
        hso = (json.loads(p.stdout) if p.stdout.strip() else {}).get("hookSpecificOutput") or {}
    except ValueError:
        return "CRASH", "unparseable stdout: " + p.stdout[:200]
    if hso.get("permissionDecision") == "deny":
        return "deny", hso.get("permissionDecisionReason") or ""
    return "allow", hso.get("additionalContext") or ""


def main():
    tmp = tempfile.mkdtemp(prefix="rgd_")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=tmp, CLAUDE_PROJECT_DIR=REPO, PYTHONIOENCODING="utf-8")
    failures = []

    def check(label, ok, detail=""):
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label + (" :: " + detail[:300] if detail else ""))

    for tool, command in DENY:
        verdict, text = dispatch(tool, command, env)
        label = "deny  %-10s %r" % (tool, command)
        check(label, verdict == "deny", verdict + " " + text)
        if verdict == "deny":
            check(label + " names the Grep tool, rg, git grep and CLAUDE.md, never `| head`",
                  "Grep tool" in text and "rg" in text and "git grep" in text
                  and "Tool Routing" in text and "head -" not in text and "| head" not in text, text)

    for tool, command in ALLOW:
        verdict, text = dispatch(tool, command, env)
        # Another guard may deny for its own reason (git_guardrails on a stale stamp); the arm
        # asserts only that the recursive-grep deny did not fire.
        check("allow %-10s %r (no recursive-grep deny; verdict %s)" % (tool, command, verdict),
              verdict != "CRASH" and "RECURSIVE GREP DENIED" not in text, verdict + " " + text)
        check("allow %-10s %r carries no `| head` advice" % (tool, command),
              "| head" not in text and "head -" not in text, text)

    print("\n%d failure(s)" % len(failures) if failures else "\nall ok")
    for f in failures:
        print("  " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
