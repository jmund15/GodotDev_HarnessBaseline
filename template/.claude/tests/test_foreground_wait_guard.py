"""Proof for hooks/foreground_wait_guard.py: a Monitor watch past the tool's own ceiling is denied; one under it passes.

Real PreToolUse payloads on stdin. The Monitor branch reads no state, so the proof plants none.

    python3 .claude/tests/test_foreground_wait_guard.py
"""
import json
import os
import subprocess
import sys

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "foreground_wait_guard.py")
MONITOR_CAP_MS = 1800000
FAILURES = []


def run(payload):
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                       text=True, encoding="utf-8", timeout=30)
    return r.returncode, r.stdout, r.stderr


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s :: %s" % (label, str(detail)[:300]))


def monitor(timeout_ms):
    return {"session_id": "proof-fwg-1", "hook_event_name": "PreToolUse", "tool_name": "Monitor",
            "tool_input": {"command": "tail -f run.log", "timeout_ms": timeout_ms}}


rc, out, err = run(monitor(MONITOR_CAP_MS + 1))
decision = json.loads(out).get("hookSpecificOutput", {}) if out.strip() else {}
check("a Monitor asking past the tool ceiling is denied",
      rc == 0 and decision.get("permissionDecision") == "deny", out + err)

rc, out, err = run(monitor(60000))
check("a Monitor under the ceiling passes silently", rc == 0 and out.strip() == "", out + err)


def bash(command, background=False):
    tool_input = {"command": command, "run_in_background": True} if background else {"command": command}
    return {"session_id": "proof-fwg-2", "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": tool_input}


def advisory(command, background=False):
    rc, out, err = run(bash(command, background))
    context = json.loads(out).get("hookSpecificOutput", {}).get("additionalContext", "") if out.strip() else ""
    return rc, context, out + err


for label, command in (("a subshell backgrounded with a trailing ampersand", "( pytest -q ) &"),
                       ("a line ending in a background ampersand", "pytest -q &\necho done")):
    rc, context, detail = advisory(command)
    check(label + " draws the run_in_background advisory",
          rc == 0 and "run_in_background" in context, detail)

for label, command, background in (
        ("a logical and is not a background job", "pytest -q && echo ok", False),
        ("a redirect into a descriptor is not a background job", "pytest -q 2>&1", False),
        ("an ampersand inside quotes is not a background job", 'echo "tail &"', False),
        ("an ampersand in a heredoc body is data", "cat <<'EOF'\nline &\nEOF", False),
        ("an ampersand on a call already detached with run_in_background is not repeated", "( pytest -q ) &", True)):
    rc, context, detail = advisory(command, background)
    check(label + " passes silently", rc == 0 and "run_in_background" not in context, detail)

print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
for f in FAILURES:
    print("  " + f)
sys.exit(1 if FAILURES else 0)
