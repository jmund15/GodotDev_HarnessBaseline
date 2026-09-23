"""Proof cases for workflow_provider_guard's role-ladder injection (harness_tooling: every guard
has a re-runnable proof). Feeds real PreToolUse payloads and asserts on the emitted channel."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _transport_fixture import hook_env

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "hooks", "workflow_provider_guard.py")


def run_hook(payload):
    out = subprocess.run([sys.executable, HOOK], env=hook_env(), input=json.dumps(payload),
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_workflow_call_injects_ladder_roles():
    stdout = run_hook({"tool_name": "Workflow", "tool_input": {"name": "review-fanout"}})
    assert stdout, "Workflow call must emit additionalContext"
    ctx = json.loads(stdout)["hookSpecificOutput"]["additionalContext"]
    assert "[role ladder" in ctx
    # a known ladder row must be present with its claimed tiers and its role prose
    assert "luna:" in ctx  # family names: a new version never churns them
    assert "luna: fanout/scout (test authoring" in ctx, ctx


def test_non_workflow_tool_emits_nothing():
    assert run_hook({"tool_name": "Bash", "tool_input": {"command": "echo hi"}}) == ""


if __name__ == "__main__":
    test_workflow_call_injects_ladder_roles()
    test_non_workflow_tool_emits_nothing()
    print("OK")
