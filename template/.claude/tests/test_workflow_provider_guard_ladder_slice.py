"""Proof for workflow_provider_guard's ladder-slice seam: `call_tiers` (the tiers a call implies, the gate's
slice check). The cheaper-claimants note is gone: the ladder rows are in context before the gate admits a call.

The two payloads are trimmed cuts of live tool_use blocks, planted here (the fixture they came from never ships).
"""
import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
FIXTURE = {
    "review_fanout_architect": {
        "tool_name": "Workflow",
        "tool_input": {
            "scriptPath": ".claude/workflows/review_fanout.js",
            "args": {
                "agents": [
                    {"key": "text", "promptPath": ".claude/scratch/seats/seat_text.md", "model": "m1",
                     "effort": "high", "tier": "architect"},
                    {"key": "memory", "promptPath": ".claude/scratch/seats/seat_memory.md",
                     "model": "m2", "effort": "high", "tier": "architect"},
                ],
                "contextPrefixPath": ".claude/scratch/seats/context.md",
                "spillDir": ".claude/scratch/seats/spill",
                "justification": "plan review at the architect tier's review rung",
                "tier": "architect",
            },
        },
    },
    "agent_tier_architect_opus": {
        "tool_name": "Agent",
        "tool_input": {
            "description": "Build comparator-wave run manifest",
            "subagent_type": "general-purpose",
            "model": "m1",
            "prompt": "TIER: architect\nAGENT-EXCEPTION: the scoring jobs are not enumerable yet; this job discovers "
                      "the exact cells and commands so the parent can dispatch them.\n\nRead and follow the brief "
                      "in full.",
        },
    },
}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def wpg():
    import sys
    sys.path.insert(0, str(HOOKS))
    return _load("wpg_ladder_slice", HOOKS / "workflow_provider_guard.py")


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path / "state"))
    return _load("hook_state_wpg_slice", HOOKS / "_hook_state.py")


def _payload(name):
    rec = FIXTURE[name]
    return {"tool_name": rec["tool_name"], "tool_input": rec["tool_input"]}


def test_call_tiers_names_the_tier_a_workflow_call_writes(wpg):
    tiers = wpg.call_tiers(_payload("review_fanout_architect"))
    assert tiers and "architect" in tiers and tiers == sorted(tiers)


def test_call_tier_consumers_names_each_pin_label_under_its_tier(wpg):
    call = {"tool_name": "Workflow", "tool_input": {
        "scriptPath": ".claude/workflows/review_fanout.js",
        "args": {"agents": [{"key": "lens-a", "model": "m1", "effort": "high", "tier": "fanout"}],
                 "fold": {"model": "m2", "effort": "medium"}}}}
    assert wpg.call_tier_consumers(call) == {"executor": ["fold"], "fanout": ["lens-a"]}
    assert sorted(wpg.call_tier_consumers(call)) == wpg.call_tiers(call)
    assert wpg.call_tier_consumers({"tool_name": "Bash", "tool_input": {"command": "ls"}}) is None


def test_call_tiers_reads_an_agent_calls_tier_line(wpg):
    assert "architect" in wpg.call_tiers(_payload("agent_tier_architect_opus"))


def test_call_tiers_folds_validation_onto_fanout(wpg):
    call = {"tool_name": "Workflow", "tool_input": {"args": {"tier": "validation", "jobs": [{"model": "m1"}]}}}
    assert wpg.call_tiers(call) == ["fanout"]


def test_call_tiers_is_unknown_for_a_pin_without_a_tier(wpg):
    call = {"tool_name": "Workflow", "tool_input": {"args": {"jobs": [
        {"model": "m1", "tier": "architect"}, {"model": "m2"}]}}}
    assert wpg.call_tiers(call) is None


def test_call_tiers_is_unknown_without_pins_or_for_an_illegal_tier(wpg):
    assert wpg.call_tiers({"tool_name": "Workflow", "tool_input": {"args": {"tier": "architect"}}}) is None
    assert wpg.call_tiers({"tool_name": "Bash", "tool_input": {"command": "ls"}}) is None
    bad = {"tool_name": "Workflow", "tool_input": {"args": {"tier": "wizard", "jobs": [{"model": "m1"}]}}}
    assert wpg.call_tiers(bad) is None


def test_the_guard_no_longer_builds_a_cheaper_claimants_note(wpg):
    assert not hasattr(wpg, "cheaper_notes")
