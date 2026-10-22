"""Proof for hooks/rail_probe_guard.py: an armed battery subagent's write outside its cell is refused (exit 2); inside passes.

A planted marker in a temp state dir arms the guard. Real PreToolUse payloads on stdin.

    python3 .claude/tests/test_rail_probe_guard.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "rail_probe_guard.py")
FAILURES = []
ROOT = tempfile.mkdtemp(prefix="rail_probe_proof_").replace("\\", "/")
STATE = os.path.join(ROOT, "state")
CELL = ROOT + "/cell-a"
SECRET = ROOT + "/secret.txt"
SESSION = "proof-rail-1"
os.makedirs(STATE)
os.makedirs(CELL)
with open(os.path.join(STATE, "wsg-%s.json" % SESSION[:8]), "w", encoding="utf-8") as fh:
    json.dump({"cells": [CELL], "protected": [SECRET], "searchDenied": [], "expires": time.time() + 3600}, fh)


def run(payload):
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=STATE)
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                       text=True, encoding="utf-8", timeout=30, env=env)
    return r.returncode, r.stdout, r.stderr


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s :: %s" % (label, str(detail)[:300]))


def call(tool, tool_input, agent=True):
    payload = {"session_id": SESSION, "tool_name": tool, "tool_input": tool_input, "cwd": ROOT}
    if agent:
        payload["agent_id"] = "agent-proof-1"
    return payload


rc, out, err = run(call("Write", {"file_path": ROOT + "/outside.txt", "content": "x"}))
check("an armed subagent write outside its cell is refused", rc == 2 and "Permission denied" in err, out + err)

rc, out, err = run(call("Write", {"file_path": CELL + "/answer.md", "content": "x"}))
check("an armed subagent write inside its cell passes", rc == 0 and err.strip() == "", out + err)

rc, out, err = run(call("Read", {"file_path": SECRET}))
check("an armed subagent read of a protected path is refused", rc == 2, out + err)

rc, out, err = run(call("Write", {"file_path": ROOT + "/outside.txt", "content": "x"}, agent=False))
check("an orchestrator call on the same session passes", rc == 0 and err.strip() == "", out + err)

print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
for f in FAILURES:
    print("  " + f)
sys.exit(1 if FAILURES else 0)
