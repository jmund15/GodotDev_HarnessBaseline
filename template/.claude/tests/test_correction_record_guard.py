"""Proof for hooks/correction_record_guard.py: a ledger row (`autolearn_outcomes.py record`) is written by the
pass that decided it, so the call is denied unless /codify, /autolearn or /eval_dashboard is in context.

Decision cases call the hook in process (`_hook_call`); one case spawns the real Bash dispatcher to prove the
guard is wired and the deny reaches the agent.

    python3 -m pytest -q .claude/tests/test_correction_record_guard.py
"""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
import _hook_call  # noqa: E402
import _hook_state  # noqa: E402

HOOK = os.path.join(HERE, "..", "hooks", "correction_record_guard.py")
DISPATCH = os.path.join(HERE, "..", "hooks", "pre_bash_dispatch.py")
MARKER = os.path.join(HERE, "..", "hooks", "skill_load_marker.py")
RECORD = ("python3 .claude/tools/autolearn_outcomes.py record --date 2026-11-25 --session s --target t "
          "--signal g --recommended apply --outcome recorded --reason r")
SID = "recguard1"


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path))
    return str(tmp_path)


def load(name, sid=SID, agent_id=""):
    _hook_state.mark_skill_in_context(sid, name, agent_id)


def verdict(command, state, sid=SID, agent_id="", tool="Bash"):
    payload = {"tool_name": tool, "session_id": sid, "tool_input": {"command": command}}
    if agent_id:
        payload["agent_id"] = agent_id
    code, _out, err, output = _hook_call.call(HOOK, payload, env=dict(os.environ, HARNESS_HOOK_STATE_DIR=state))
    assert code == 0, err  # a crash is never an allow
    return (output or {}).get("permissionDecision"), (output or {}).get("permissionDecisionReason", "")


def test_a_hand_record_with_no_pass_loaded_is_denied_with_the_one_next_step(state):
    decision, reason = verdict(RECORD, state)
    assert decision == "deny"
    assert 'Skill(skill="codify")' in reason


@pytest.mark.parametrize("skill", ["codify", "autolearn", "eval_dashboard"])
def test_a_record_inside_a_pass_that_owns_the_ledger_is_allowed(state, skill):
    load(skill)
    assert verdict(RECORD, state)[0] is None


def test_a_skill_the_ledger_does_not_belong_to_does_not_unlock_it(state):
    load("orchestration")
    assert verdict(RECORD, state)[0] == "deny"


def test_a_compaction_between_the_load_and_the_record_denies_again(state):
    load("codify")
    assert verdict(RECORD, state)[0] is None
    _hook_state.clear_compaction_keys(SID)
    assert verdict(RECORD, state)[0] == "deny"
    load("codify")
    assert verdict(RECORD, state)[0] is None


def test_the_load_is_per_session_and_per_context(state):
    load("codify", sid="othersess")
    assert verdict(RECORD, state)[0] == "deny"
    load("codify")
    assert verdict(RECORD, state, agent_id="sub1")[0] == "deny"  # the parent's load is not the subagent's
    load("codify", agent_id="sub1")
    assert verdict(RECORD, state, agent_id="sub1")[0] is None


@pytest.mark.parametrize("command", [
    "cd /repo && " + RECORD,
    RECORD.replace("python3 ", "python "),
    "python3 -u " + RECORD.split("python3 ", 1)[1],
    "echo ready; " + RECORD,
])
def test_the_record_run_is_found_in_any_invocation_shape(state, command):
    assert verdict(command, state)[0] == "deny"


@pytest.mark.parametrize("command", [
    "python3 .claude/tools/autolearn_outcomes.py lookup --target t",
    "python3 .claude/tools/autolearn_outcomes.py history --target t",
    "python3 .claude/tools/autolearn_outcomes.py unswept",
    "python3 .claude/tools/autolearn_outcomes.py swept",
    "git log --oneline -- .claude/tools/autolearn_outcomes.py",
    "grep -n record .claude/tools/autolearn_outcomes.py",
    "echo 'autolearn_outcomes.py record is the call'",
    RECORD + " --file /tmp/scratch_ledger.jsonl",  # a scratch ledger no Step 0 history reads
    "python3 .claude/tools/other.py record",
    "ls",
])
def test_a_read_a_mention_and_a_scratch_ledger_pass_with_nothing_loaded(state, command):
    assert verdict(command, state)[0] is None


def test_a_non_shell_tool_is_not_judged(state):
    assert verdict(RECORD, state, tool="Read")[0] is None


def test_the_real_dispatcher_denies_the_hand_record_and_passes_a_loaded_one(tmp_path):
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=str(tmp_path))
    payload = json.dumps({"tool_name": "Bash", "session_id": "wired001", "tool_input": {"command": RECORD}})

    def run():
        proc = subprocess.run([sys.executable, DISPATCH], input=payload, capture_output=True, text=True,
                              timeout=120, encoding="utf-8", env=env)
        assert proc.returncode in (0, 2), proc.stderr
        return (json.loads(proc.stdout).get("hookSpecificOutput") or {}).get("permissionDecision") if proc.stdout else None

    assert run() == "deny"
    os.environ["HARNESS_HOOK_STATE_DIR"] = str(tmp_path)
    try:
        _hook_state.mark_skill_in_context("wired001", "codify")
    finally:
        del os.environ["HARNESS_HOOK_STATE_DIR"]
    assert run() is None


def type_prompt(prompt, state, sid=SID):
    """The owner submits `prompt`: the UserPromptSubmit member records a typed command's load."""
    code, _out, err, _output = _hook_call.call(MARKER, {"hook_event_name": "UserPromptSubmit", "session_id": sid,
                                                        "prompt": prompt}, env=dict(os.environ, HARNESS_HOOK_STATE_DIR=state))
    assert code == 0, err


TAGGED = "<command-message>codify</command-message>\n<command-name>/codify</command-name>\n<command-args>fix x</command-args>"


@pytest.mark.parametrize("prompt", ["/codify fix the thing", "/codify", TAGGED, "  /codify\nmore"])
def test_a_typed_codify_unlocks_the_record_until_a_compaction(state, prompt):
    assert verdict(RECORD, state)[0] == "deny"
    type_prompt(prompt, state)
    assert verdict(RECORD, state)[0] is None
    _hook_state.clear_compaction_keys(SID)
    assert verdict(RECORD, state)[0] == "deny"


def test_a_typed_orchestration_satisfies_the_dispatch_gate_check(state, monkeypatch):
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", state)
    assert not _hook_state.skill_in_context(SID, "orchestration")
    type_prompt("/orchestration run it", state)
    assert _hook_state.skill_in_context(SID, "orchestration")
    assert not _hook_state.skill_in_context(SID, "orchestration", agent_id="sub1")


@pytest.mark.parametrize("prompt", ["please run /codify now", "see `/codify` first", "/codifyx", "codify", ""])
def test_a_prompt_that_only_mentions_a_command_marks_nothing(state, prompt):
    type_prompt(prompt, state)
    assert verdict(RECORD, state)[0] == "deny"
