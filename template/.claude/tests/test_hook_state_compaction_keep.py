"""Proof for the compaction clear in hooks/_hook_state.py: PreCompact clears every skill marker and the ladder
markers and stamps the clear; only the client's `invoked_skills` attachment restores a skill
(tests/test_compaction_state.py proves that read)."""
import importlib.util
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
SKILL_KEY = "skill_in_context:orchestration"


@pytest.fixture
def hs(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path / "state"))
    spec = importlib.util.spec_from_file_location("hook_state_keep_probe", HOOKS / "_hook_state.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _armed(hs, sid, *skills):
    for name in skills:
        hs.mark_skill_in_context(sid, name)
    hs.mark_ladder_slice(sid, ["architect"])
    assert hs.update_json_locked(hs.state_path(sid), lambda s: s.__setitem__("model_ladder_ready", True))[0]


def test_a_compaction_clears_every_skill_and_the_ladder_markers(hs):
    _armed(hs, "keep0001", "orchestration", "instruction_quality")
    assert hs.clear_compaction_keys("keep0001") is True
    state = hs.read_json_salvage(hs.state_path("keep0001"))
    assert "model_ladder_ready" not in state and hs.ladder_slice_tiers("keep0001") == []
    assert not hs.skill_in_context("keep0001", "orchestration")
    assert not hs.skill_in_context("keep0001", "instruction_quality")


def test_the_clear_stamps_its_time(hs):
    import time
    before = time.time()
    hs.clear_compaction_keys("keep0002")
    stamp = hs.read_json_salvage(hs.state_path("keep0002"))["compacted_at"]
    assert before <= stamp <= time.time()


def test_a_clear_with_nothing_armed_reports_no_change(hs):
    assert hs.clear_compaction_keys("keep0003") is False


def test_a_load_after_the_clear_is_in_context(hs):
    hs.clear_compaction_keys("keep0004")
    hs.mark_skill_in_context("keep0004", "orchestration")
    assert hs.skill_in_context("keep0004", "orchestration")


def test_the_live_orchestration_skill_fits_the_reattach_limit():
    spec = importlib.util.spec_from_file_location("hook_state_size_probe", HOOKS / "_hook_state.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = HOOKS.parent / "skills" / "orchestration" / "SKILL.md"
    with open(path, encoding="utf-8", newline="") as fh:
        size = len(fh.read())
    assert size <= module.SKILL_REATTACH_CHARS, (
        f"orchestration/SKILL.md is {size} characters, over SKILL_REATTACH_CHARS={module.SKILL_REATTACH_CHARS}: "
        "the client re-attaches it truncated after a compaction, so every dispatch after one needs a re-invoke; "
        "move text to skills/orchestration/reference/.")


def test_a_skill_call_and_a_full_read_both_mark_in_context(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.syspath_prepend(str(HOOKS))
    import _hook_state
    import skill_load_marker
    skill_load_marker.process({"tool_name": "Skill", "session_id": "mark0001", "tool_input": {"skill": "orchestration"}})
    fired = _hook_state.read_json_salvage(_hook_state.state_path("mark0001"))["fired_since_compaction"]
    assert fired.get(SKILL_KEY)
    skill_load_marker.process({"tool_name": "Read", "session_id": "mark0002",
                               "tool_input": {"file_path": str(HOOKS.parent / "skills" / "testing" / "SKILL.md")}})
    fired = _hook_state.read_json_salvage(_hook_state.state_path("mark0002"))["fired_since_compaction"]
    assert fired.get("skill_in_context:testing")
