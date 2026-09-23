"""Proof cases for the ladder injection on Skill(orchestration) (hooks/skill_load_marker.py) and the
Workflow-time fallback it replaces (hooks/workflow_provider_guard.py): the rows arrive BEFORE a pin is
chosen, and only once per session."""
import json
import os
import subprocess
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _transport_fixture import hook_env  # noqa: E402

HOOKS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")
MARKER = os.path.join(HOOKS, "skill_load_marker.py")
GUARD = os.path.join(HOOKS, "workflow_provider_guard.py")


def run(hook, payload):
    out = subprocess.run([sys.executable, hook], env=hook_env(), input=json.dumps(payload),
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout).get("hookSpecificOutput", {}) if out.stdout.strip() else {}


def test_orchestration_load_injects_ladder_and_workflow_then_stays_quiet():
    sid = uuid.uuid4().hex     # state files key on the first 8 chars of the id — a shared prefix collides
    h = run(MARKER, {"tool_name": "Skill", "session_id": sid, "tool_input": {"skill": "orchestration"}})
    ctx = h.get("additionalContext", "")
    assert h.get("hookEventName") == "PostToolUse" and "[role ladder" in ctx and "luna:" in ctx, h  # family names: a new version never churns them
    # compact rows `model: tier, first-listed effort` plus the pointer to the full table; never a cut effort clause
    assert "model_ladder_evidence.md" in ctx and "not the registry roster" in ctx, ctx
    assert "fable: orchestrator, high" in ctx and "opus: executor, medium" in ctx and "sonnet: fanout, high" in ctx, ctx
    assert "astra (excluded):" in ctx, ctx
    assert "(effort:" not in ctx and len(ctx) < 700, (len(ctx), ctx)
    # the same session's Workflow call no longer repeats the rows
    g = run(GUARD, {"tool_name": "Workflow", "session_id": sid, "tool_input": {"name": "review-fanout"}})
    assert "[role ladder" not in g.get("additionalContext", ""), g


def test_other_skills_inject_nothing():
    h = run(MARKER, {"tool_name": "Skill", "session_id": "ladder-proof-other", "tool_input": {"skill": "testing"}})
    assert h == {}, h


def test_workflow_without_the_skill_still_gets_the_fallback():
    sid = uuid.uuid4().hex     # state files key on the first 8 chars of the id — a shared prefix collides
    g = run(GUARD, {"tool_name": "Workflow", "session_id": sid, "tool_input": {"name": "review-fanout"}})
    ctx = g.get("additionalContext", "")
    # the fallback prints the same compact rows as the skill-load injection
    assert "[role ladder" in ctx and "opus: executor, medium" in ctx and "(effort:" not in ctx, g


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK")
