#!/usr/bin/env python3
"""Proof of hooks/task_brief_facts.py: at the write of a scratch brief holding a ```task block, the brief's own defect
and the loads a dispatch will be denied for, as one context line each, and nothing else.

Cases run in process against a planted session state; the dispatcher case drives the registered chain end to end.

    python3 -m pytest -q .claude/tests/test_task_brief_facts.py
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(CLAUDE, "hooks"), os.path.join(CLAUDE, "tools")]
import _hook_state as state  # noqa: E402
import post_edit_dispatch  # noqa: E402
import task_brief_facts as sub  # noqa: E402

GOOD = '# Brief\n\n```task\n{"tier": "executor", "check": ["python3 x.py"], "writeSet": ["x.py"]}\n```\n'
NO_TIER = '# Brief\n\n```task\n{"check": ["python3 x.py"]}\n```\n'
EMPTY_CHECK = '# Brief\n\n```task\n{"tier": "executor", "check": []}\n```\n'
BAD_WRITE_SET = EMPTY_CHECK.replace('"check": []', '"check": ["x"], "writeSet": "x"')
TWO_BLOCKS = GOOD + GOOD


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CODE_SIDECAR", raising=False)
    return tmp_path


def met(sid):
    """Both loads a dispatch is denied without are in this session's context."""
    state.mark_skill_in_context(sid, "orchestration")
    assert state.mark_ladder_slice(sid, ["executor"])


def brief(env, text, name="b.md"):
    path = env / ".claude" / "scratch" / "x" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def write(path, sid="s1", tool="Write"):
    return sub.process({"tool_name": tool, "session_id": sid, "cwd": os.path.dirname(path),
                        "tool_input": {"file_path": path, "content": "x"}})


def context(result):
    return (result or {}).get("context") or ""


def test_well_formed_brief_adds_no_line_once_the_loads_are_met(env):
    met("s1")
    assert write(brief(env, GOOD)) is None


def test_a_block_naming_no_tier_is_not_news(env):
    met("s1")
    assert write(brief(env, NO_TIER)) is None


def test_malformed_blocks_name_their_defect(env):
    met("s1")
    for index, (text, defect) in enumerate([(EMPTY_CHECK, "empty check"), (BAD_WRITE_SET, "writeSet"),
                                            (TWO_BLOCKS, "2 blocks")]):
        line = context(write(brief(env, text, "m%d.md" % index)))
        assert sub.TAG in line and defect in line


def test_a_defect_is_told_once_per_brief_and_window_and_again_when_it_changes(env):
    met("s1")
    path = brief(env, EMPTY_CHECK)
    assert context(write(path))
    assert write(path) is None
    assert context(write(brief(env, EMPTY_CHECK, "other.md")))
    assert context(write(path, sid="s2"))
    brief(env, BAD_WRITE_SET)
    assert "writeSet" in context(write(path, tool="Edit"))
    state.clear_compaction_keys("s1")
    brief(env, EMPTY_CHECK)
    assert context(write(path))


def test_the_dispatch_prep_notice_rides_the_first_brief_of_a_window_only(env):
    path = brief(env, GOOD)
    line = context(write(path))
    assert 'Skill(skill="orchestration")' in line and "for-role" in line
    assert write(path) is None
    state.clear_compaction_keys("s1")
    assert context(write(path))


def test_notice_and_defect_arrive_together_and_the_notice_alone_when_both_loads_are_met_elsewhere(env):
    both = context(write(brief(env, EMPTY_CHECK)))
    assert sub.TAG in both and "empty check" in both and "for-role" in both
    met("s2")
    assert "for-role" not in context(write(brief(env, EMPTY_CHECK, "c.md"), sid="s2"))


def test_only_a_scratch_write_of_a_task_block_is_judged(env, tmp_path):
    plain = tmp_path / "notes.md"
    plain.write_text(EMPTY_CHECK)
    assert write(str(plain)) is None
    assert write(brief(env, "# No fence here\n")) is None
    assert sub.process({"tool_name": "Read", "session_id": "s1",
                        "tool_input": {"file_path": brief(env, EMPTY_CHECK, "r.md")}}) is None
    assert write(str(env / ".claude" / "scratch" / "gone.md")) is None


def test_dispatcher_delivers_it_as_post_tool_use_context(env):
    assert "task_brief_facts" in [name for name, _ in post_edit_dispatch.CHAIN]
    path = brief(env, EMPTY_CHECK)
    code, out, _err = post_edit_dispatch.dispatch(
        {"tool_name": "Write", "session_id": "d1", "tool_input": {"file_path": path, "content": "x"}})
    specific = json.loads(out)["hookSpecificOutput"]
    assert code == 0 and specific["hookEventName"] == "PostToolUse" and "empty check" in specific["additionalContext"]
