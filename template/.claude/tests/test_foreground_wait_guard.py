"""Proof for hooks/foreground_wait_guard.py: the two wait checks a never-woken caller gets.

    python -m pytest .claude/tests/test_foreground_wait_guard.py -q --durations=0

Decision cases call the hook in process with a real payload; one spawned case proves the registered command runs.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
import _hook_call  # noqa: E402

HOOK = TESTS.parent / "hooks" / "foreground_wait_guard.py"
SUBAGENT_MS = 270000
HANDBACK_MS = 1800000
LOOP = "until test -f done.flag; do sleep 30; done"
# Each clamped loop wait adds 270 s; the total is read before a call, so the call after the 1800 s total is the deny.
CALLS_TO_HANDBACK = HANDBACK_MS // SUBAGENT_MS + 2


@pytest.fixture
def env(tmp_path):
    return {"HARNESS_HOOK_STATE_DIR": str(tmp_path), "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}


def _payload(command, tool="Bash", timeout=None, background=False, agent_id=None):
    tool_input = {"command": command}
    if timeout is not None:
        tool_input["timeout"] = timeout
    if background:
        tool_input["run_in_background"] = True
    payload = {"session_id": "wait-proof", "tool_name": tool, "tool_input": tool_input}
    if agent_id:
        payload["agent_id"] = agent_id
    return payload


def _call(env, payload):
    code, _out, err, output = _hook_call.call(HOOK, payload, env=env)
    assert code == 0, err
    return output


def test_woken_orchestrator_passes_a_long_foreground_call_untouched(env):
    woken = dict(env, CLAUDE_CODE_ENTRYPOINT="cli")
    assert _call(woken, _payload("make build", timeout=600000)) == {}


def test_never_woken_long_call_is_clamped_and_bash_gets_the_exit_line(env):
    out = _call(env, _payload("make build", timeout=600000))
    assert out["updatedInput"]["timeout"] == SUBAGENT_MS
    assert out["updatedInput"]["command"] == 'make build\necho "EXIT $?"'
    assert "Clamped" in out["additionalContext"]


def test_never_woken_powershell_is_clamped_without_the_exit_line(env):
    out = _call(env, _payload("Build-Thing", tool="PowerShell", timeout=600000))
    assert out["updatedInput"]["timeout"] == SUBAGENT_MS
    assert out["updatedInput"]["command"] == "Build-Thing"


def test_never_woken_call_within_the_bound_passes(env):
    assert _call(env, _payload("make build", timeout=SUBAGENT_MS)) == {}


def test_background_call_is_not_clamped(env):
    assert _call(env, _payload("make build", timeout=600000, background=True)) == {}


def test_handback_denies_the_wait_once_loop_waits_pass_thirty_minutes(env):
    outs = [_call(env, _payload(LOOP, timeout=SUBAGENT_MS)) for _ in range(CALLS_TO_HANDBACK)]
    assert all(out == {} for out in outs[:-1])
    assert outs[-1]["permissionDecision"] == "deny"


def test_a_non_wait_call_resets_the_hand_back_total(env):
    for _ in range(CALLS_TO_HANDBACK):
        _call(env, _payload(LOOP, timeout=SUBAGENT_MS))
    _call(env, _payload("git status"))
    assert _call(env, _payload(LOOP, timeout=SUBAGENT_MS)) == {}


def test_a_loop_inside_a_heredoc_body_is_not_a_wait(env):
    script = "cat <<'EOF'\nuntil x; do sleep 1; done\nEOF"
    for _ in range(CALLS_TO_HANDBACK):
        assert _call(env, _payload(script, timeout=SUBAGENT_MS)) == {}


def test_never_woken_follows_the_caller_kind(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    spec = importlib.util.spec_from_file_location("fwg_under_test", HOOK)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HOOK.parent))
    spec.loader.exec_module(module)
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")
    meta_dir = tmp_path / "session" / "subagents"
    meta_dir.mkdir(parents=True)
    (meta_dir / "agent-a1.meta.json").write_text("{}", encoding="utf-8")
    assert module.never_woken({"agent_id": "a1", "transcript_path": str(transcript), "agent_type": "general"}) is False
    assert module.never_woken({"agent_id": "w9", "agent_type": "workflow-subagent"}) is True
    assert module.never_woken({}) is False


def test_the_registered_command_runs_and_clamps_once(tmp_path):
    done = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(_payload("make build", timeout=600000)),
                          capture_output=True, text=True, encoding="utf-8",
                          env=dict(os.environ, CLAUDE_CODE_ENTRYPOINT="sdk-cli", HARNESS_HOOK_STATE_DIR=str(tmp_path)))
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["hookSpecificOutput"]["updatedInput"]["timeout"] == SUBAGENT_MS
