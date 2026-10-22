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

print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
for f in FAILURES:
    print("  " + f)
sys.exit(1 if FAILURES else 0)
