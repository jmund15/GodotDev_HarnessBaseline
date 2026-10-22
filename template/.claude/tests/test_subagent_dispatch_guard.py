"""Proof for hooks/subagent_dispatch_guard.py: a delegate's Agent or Workflow call is denied; a main-session call passes.

Real PreToolUse payloads on stdin. Writes nothing.

    python3 .claude/tests/test_subagent_dispatch_guard.py
"""
import json
import os
import subprocess
import sys

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "subagent_dispatch_guard.py")
FAILURES = []


def run(payload, sidecar=False):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SIDECAR"}
    if sidecar:
        env["CLAUDE_CODE_SIDECAR"] = "1"
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                       text=True, encoding="utf-8", timeout=30, env=env)
    return r.returncode, r.stdout, r.stderr


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s :: %s" % (label, str(detail)[:300]))


def denied(stdout):
    try:
        return json.loads(stdout).get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    except ValueError:
        return False


rc, out, err = run({"tool_name": "Agent", "transcript_path": "/proof/root/projects/session/subagents/agent-1.jsonl"})
check("an Agent call from a delegate transcript is denied", rc == 0 and denied(out), out + err)

rc, out, err = run({"tool_name": "Workflow", "transcript_path": "/proof/root/projects/session/session.jsonl"}, sidecar=True)
check("a Workflow call from a sidecar child is denied", rc == 0 and denied(out), out + err)

rc, out, err = run({"tool_name": "Agent", "transcript_path": "/proof/root/projects/session/session.jsonl"})
check("an Agent call from the main session passes silently", rc == 0 and out == "", out + err)

rc, out, err = run({"tool_name": "Read", "transcript_path": "/proof/root/projects/session/subagents/agent-1.jsonl"})
check("a non-dispatch tool in a delegate passes silently", rc == 0 and out == "", out + err)

print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
for f in FAILURES:
    print("  " + f)
sys.exit(1 if FAILURES else 0)
