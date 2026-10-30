#!/usr/bin/env python3
"""Proof for tools/score_routing_battery.py's transcript signal: a row with an agent id reads that
subagent's transcript, a session-only row (a headless battery child) reads the session's own
transcript, and a row with neither reads nothing.

The transcript tree is planted under a temp login (`CLAUDE_CONFIG_DIR`); each case runs in a child
process so the extractor resolves its root from that environment.

    python3 .claude/tests/test_score_routing_battery.py
"""
import json
import os
import subprocess
import sys
import tempfile

TOOLS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
SID = "7b7b7b7b-0000-4000-8000-000000000003"
AGENT = "a0a0a0a0a0a0a0a01"
failures = []


def ck(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else "\n        " + str(detail)[-300:]))
    if not cond:
        failures.append(label)


def call(row, n):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": n, "input": {}}]}}


def score(login, info):
    code = ("import json, sys; sys.path.insert(0, %r); import score_routing_battery as s; "
            "print(json.dumps(s.extract_tools_used('x', %r)))" % (TOOLS, info))
    p = subprocess.run([sys.executable, "-c", code], env=dict(os.environ, CLAUDE_CONFIG_DIR=login),
                       capture_output=True, text=True, timeout=60)
    return p.returncode, json.loads(p.stdout or "null"), p.stderr


def main():
    with tempfile.TemporaryDirectory() as login:
        proj = os.path.join(login, "projects", "C--planted-project")
        os.makedirs(os.path.join(proj, SID, "subagents"))
        with open(os.path.join(proj, SID + ".jsonl"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(call(None, "Grep")) + "\n")
        with open(os.path.join(proj, SID, "subagents", "agent-%s.jsonl" % AGENT), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(call(None, "Read")) + "\n")

        rc, out, err = score(login, {"agent_id": AGENT, "session_id": SID})
        ck("an agent id reads that subagent's transcript", rc == 0 and out == [["Read"], "transcript"], (rc, out, err))
        rc, out, err = score(login, {"agent_id": None, "session_id": SID})
        ck("a session-only row reads the session's own transcript", rc == 0 and out == [["Grep"], "transcript"], (rc, out, err))
        rc, out, err = score(login, {"agent_id": None, "session_id": None})
        ck("a row with neither id reads nothing", rc == 0 and out == [[], "none"], (rc, out, err))
    print("test_score_routing_battery: %d failed" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
