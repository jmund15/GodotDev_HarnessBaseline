"""Proof that hooks/dispatch_mechanism_guard.py admits a Workflow call on a per-tier ladder slice.

Each case feeds a real PreToolUse payload to the hook and reads the emitted channel. The state is the
compaction-scoped record `model_registry.py ladder-slice` writes; a full Read still admits the call.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
HOOK = HOOKS / "dispatch_mechanism_guard.py"
SLICE_CMD = "python3 .claude/tools/model_registry.py ladder-slice"


def _slice_embedded(reason):
    """The deny carries the missing tiers' slice itself, not the command that prints it."""
    return "## Role definitions" in reason and SLICE_CMD not in reason.split("## Role definitions")[0]


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(state))
    sys.path.insert(0, str(HOOKS))
    import _hook_state
    return dict(os.environ, HARNESS_HOOK_STATE_DIR=str(state)), _hook_state


def _workflow(sid, tier="architect", model="planted-pin"):
    args = {"jobs": [{"label": "j1", "model": model}]}
    if tier:
        args["tier"] = tier
    return {"tool_name": "Workflow", "session_id": sid, "tool_input": {"args": args}}


def _run(payload, env):
    result = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True,
                            text=True, timeout=60, env=env)
    assert result.returncode in (0, 2) and "Traceback" not in result.stderr, result.stderr[-800:]
    out = result.stdout.strip()
    if not out or out == "{}":
        return "allow", ""
    hook = json.loads(out).get("hookSpecificOutput") or {}
    if hook.get("permissionDecision") == "deny":
        return "deny", hook.get("permissionDecisionReason", "")
    return "allow", hook.get("additionalContext", "")


def _session(env_pair, sid, *, slice_tiers=(), full=False):
    env, hook_state = env_pair
    os.environ["HARNESS_HOOK_STATE_DIR"] = env["HARNESS_HOOK_STATE_DIR"]
    hook_state.mark_skill_in_context(sid, "orchestration")
    if slice_tiers:
        assert hook_state.mark_ladder_slice(sid, list(slice_tiers))
    if full:
        hook_state.update_json_locked(hook_state.state_path(sid),
                                      lambda s: s.__setitem__("model_ladder_ready", True))


def test_a_slice_of_the_pinned_tier_admits_the_workflow(env):
    _session(env, "dmgsl001", slice_tiers=["architect"])
    assert _run(_workflow("dmgsl001"), env[0])[0] == "allow"


def test_a_slice_of_another_tier_denies_with_the_slice_for_the_pinned_tier(env):
    _session(env, "dmgsl002", slice_tiers=["executor"])
    decision, reason = _run(_workflow("dmgsl002"), env[0])
    assert decision == "deny" and _slice_embedded(reason), reason


def test_no_ladder_at_all_denies_with_the_slice_for_a_tiered_call(env):
    _session(env, "dmgsl003")
    decision, reason = _run(_workflow("dmgsl003", tier="validation"), env[0])
    assert decision == "deny" and _slice_embedded(reason), reason


def test_a_call_without_a_tier_still_needs_the_full_read(env):
    _session(env, "dmgsl004", slice_tiers=["architect"])
    decision, reason = _run(_workflow("dmgsl004", tier=None), env[0])
    assert decision == "deny" and "model_ladder_evidence.md" in reason and SLICE_CMD not in reason, reason


def test_a_full_read_admits_any_tier(env):
    _session(env, "dmgsl005", full=True)
    assert _run(_workflow("dmgsl005", tier="scout"), env[0])[0] == "allow"


def test_a_compaction_withdraws_the_slice(env):
    _session(env, "dmgsl006", slice_tiers=["architect"])
    env[1].clear_compaction_keys("dmgsl006")
    env[1].mark_skill_in_context("dmgsl006", "orchestration")
    decision, reason = _run(_workflow("dmgsl006"), env[0])
    assert decision == "deny" and _slice_embedded(reason), reason


def test_the_skill_and_the_slice_are_named_in_one_deny(env):
    decision, reason = _run(_workflow("dmgsl007"), env[0])
    assert decision == "deny" and 'Skill(skill="orchestration")' in reason and _slice_embedded(reason), reason


def _plan_check(sid, fold_files):
    """A review_fanout plan check: one architect lens seat, and a fold whose pin carries no written tier."""
    args = {"agents": [{"key": "plc-doctrine-consistency", "label": "seat", "model": "sonnet", "effort": "xhigh"}],
            "fold": {"model": "haiku", "effort": "medium",
                     "subjects": [{"root": ".", "snapshot": "snap.json", "files": list(fold_files)}]}}
    return {"tool_name": "Workflow", "session_id": sid,
            "tool_input": {"scriptPath": ".claude/workflows/review_fanout.js", "args": args}}


def test_a_fold_over_a_markdown_plan_is_met_by_the_fanout_slice(env):
    # plan_check Phase 4: a fold over a markdown plan is fanout-tier work. Before this, the untiered fold pin made
    # the whole call's tiers unknown, so the gate demanded the full ladder after the slices were printed (2026-10-14).
    _session(env, "dmgsl008", slice_tiers=["architect", "fanout"])
    decision, reason = _run(_plan_check("dmgsl008", [".claude/plans/x.md"]), env[0])
    assert decision == "allow", reason


def test_a_fold_over_a_markdown_plan_without_the_fanout_slice_names_it(env):
    _session(env, "dmgsl009", slice_tiers=["architect"])
    decision, reason = _run(_plan_check("dmgsl009", [".claude/plans/x.md"]), env[0])
    assert decision == "deny" and _slice_embedded(reason), reason


def test_a_fold_over_code_needs_the_executor_slice(env):
    _session(env, "dmgsl010", slice_tiers=["architect", "fanout"])
    decision, reason = _run(_plan_check("dmgsl010", [".claude/plans/x.md", ".claude/tools/x.py"]), env[0])
    assert decision == "deny" and _slice_embedded(reason), reason
