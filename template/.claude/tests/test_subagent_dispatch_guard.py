"""Proof for hooks/subagent_dispatch_guard.py: a delegate's Agent or Workflow call is denied; a main-session call passes.

Real PreToolUse payloads on stdin. Writes nothing.

    python -m pytest .claude/tests/test_subagent_dispatch_guard.py -q
"""
import json
import os
import subprocess
import sys

import pytest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "subagent_dispatch_guard.py")
MAIN = "/proof/root/projects/session/session.jsonl"
DELEGATE = "/proof/root/projects/session/subagents/agent-1.jsonl"


def run(payload, sidecar=False, kind=None):
    """Spawned: the hook's stdin entry point is the subject."""
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_SIDECAR", "CLAUDE_CODE_SIDECAR_KIND")}
    if sidecar:
        env["CLAUDE_CODE_SIDECAR"] = "1"
    if kind:
        env["CLAUDE_CODE_SIDECAR_KIND"] = kind
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                       text=True, encoding="utf-8", timeout=30, env=env)
    return r.returncode, r.stdout, r.stderr


def denied(stdout):
    try:
        return json.loads(stdout).get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    except ValueError:
        return False


def test_an_agent_call_from_a_delegate_transcript_is_denied():
    rc, out, err = run({"tool_name": "Agent", "transcript_path": DELEGATE})
    assert rc == 0 and denied(out), out + err


def test_a_workflow_call_from_a_sidecar_child_is_denied():
    rc, out, err = run({"tool_name": "Workflow", "transcript_path": MAIN}, sidecar=True)
    assert rc == 0 and denied(out), out + err


@pytest.mark.parametrize("tool", ["Workflow", "Agent"])
def test_a_sidecar_call_is_denied_whatever_kind_its_environment_names(tool):
    rc, out, err = run({"tool_name": tool, "transcript_path": MAIN}, sidecar=True, kind="anything")
    assert rc == 0 and denied(out), out + err


def test_an_agent_call_from_the_main_session_passes_silently():
    rc, out, err = run({"tool_name": "Agent", "transcript_path": MAIN})
    assert rc == 0 and out == "", out + err


def test_a_non_dispatch_tool_in_a_delegate_passes_silently():
    rc, out, err = run({"tool_name": "Read", "transcript_path": DELEGATE})
    assert rc == 0 and out == "", out + err
