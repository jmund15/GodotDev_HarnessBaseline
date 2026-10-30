#!/usr/bin/env python3
"""Proof for brief_deliverable_guard: the first *.brief.md Write of a session is denied once.

Run: python3 .claude/tests/test_brief_deliverable_guard.py
"""
import json
import os
import subprocess
import sys
import tempfile

import _settings_probe

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "brief_deliverable_guard.py")
SETTINGS = _settings_probe.settings_path(os.path.join(HERE, ".."))
failures = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail and not ok else ""))
    if not ok:
        failures.append(name)


def run(state_dir, session, path, tool="Write"):
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": session,
               "tool_input": {"file_path": path, "content": "x"}}
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state_dir)
    proc = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True, text=True, env=env)
    out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc.returncode, out.get("hookSpecificOutput", {})


def denied(res):
    return res[0] == 0 and res[1].get("permissionDecision") == "deny"


with tempfile.TemporaryDirectory() as state:
    first = run(state, "sess-aaaaaaaa", "C:/x/.claude/scratch/a.brief.md")
    check("first brief write is denied", denied(first), str(first))
    reason = first[1].get("permissionDecisionReason", "")
    check("deny names the goal-indexed deliverable", "write its deliverable from the goal" in reason, reason)
    check("deny cites the orchestration rule", "orchestration" in reason and "Agent Prompt Structure" in reason, reason)
    check("second brief write passes", not denied(run(state, "sess-aaaaaaaa", "C:/x/b.brief.md")))
    check("other file passes", not denied(run(state, "sess-bbbbbbbb", "C:/x/notes.md")))
    check("other tool passes", not denied(run(state, "sess-bbbbbbbb", "C:/x/c.brief.md", tool="Edit")))
    check("a non-brief write does not spend the deny", denied(run(state, "sess-bbbbbbbb", "C:\\x\\d.brief.md")))
    check("second session's first brief write is denied once", denied(run(state, "sess-cccccccc", "C:/x/e.brief.md")))
    check("second session's retry passes", not denied(run(state, "sess-cccccccc", "C:/x/e.brief.md")))

with tempfile.TemporaryDirectory() as state:
    with open(os.path.join(state, "sess-ddd.json"), "w") as fh:
        fh.write('{"fired_once": {"brief_deliverable_shape": tru')
    res = run(state, "sess-ddddddd", "C:/x/f.brief.md")
    check("torn state file does not crash", res[0] == 0, str(res))

with tempfile.TemporaryDirectory() as tmp:
    blocker = os.path.join(tmp, "notadir")
    open(blocker, "w").close()
    res = run(os.path.join(blocker, "state"), "sess-eeeeeeee", "C:/x/g.brief.md")
    check("unwritable state reports the lost check and does not deny",
          res[0] == 0 and not denied(res) and "additionalContext" in res[1], str(res))

with open(SETTINGS, encoding="utf-8") as fh:
    groups = json.load(fh).get("hooks", {}).get("PreToolUse", [])
check("registered as a PreToolUse Write hook",
      any("Write" in str(g.get("matcher", "")).split("|")
          and any("brief_deliverable_guard.py" in h.get("command", "") for h in g.get("hooks", []))
          for g in groups))

print("FAILED: %s" % failures if failures else "ALL PASS")
sys.exit(1 if failures else 0)
