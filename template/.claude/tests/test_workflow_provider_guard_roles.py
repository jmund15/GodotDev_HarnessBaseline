"""Proof cases for workflow_provider_guard's silence on the role ladder (harness_tooling: every guard
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


def test_workflow_call_adds_no_ladder_dump():
    # The ladder rows reach the session when it loads Skill(orchestration) (skill_load_marker) and the doctrine
    # gate denies a call until they are in context, so an admitted call restates none of them.
    stdout = run_hook({"tool_name": "Workflow", "tool_input": {"name": "review-fanout"}})
    assert "[role ladder" not in stdout
    assert "model_ladder_evidence" not in stdout


def test_non_workflow_tool_emits_nothing():
    assert run_hook({"tool_name": "Bash", "tool_input": {"command": "echo hi"}}) == ""


if __name__ == "__main__":
    test_workflow_call_adds_no_ladder_dump()
    test_non_workflow_tool_emits_nothing()
    print("OK")
