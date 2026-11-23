"""Proof for hooks/_chain.py, the one runner behind the pre_edit, post_edit, pre_read and post_read dispatchers (the
prompt row has its own file): the member contract, each event's fault posture and the entry stubs. Planted members
stand in for project hooks."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))
import _chain  # noqa: E402

EVENTS = ("pre_edit", "post_edit", "pre_read", "post_read")
PAYLOAD = {"tool_name": "Edit", "session_id": "ch1", "tool_input": {"file_path": "C:/repo/a.txt", "new_string": "x"}}


def member(name, fn):
    fn.__name__ = name
    return fn


def says(text):
    return member("noisy", lambda p: {"context": text})


def boom(_payload):
    raise RuntimeError("planted fault")


def spec(out):
    return json.loads(out)["hookSpecificOutput"] if out else {}


@pytest.mark.parametrize("event", EVENTS)
def test_advisories_merge_into_one_payload_in_chain_order(event):
    rc, out, _ = _chain.run(event, PAYLOAD, members=(says("one"), says("two")))
    assert rc == 0 and spec(out)["additionalContext"] == "one\n\ntwo"
    assert spec(out)["hookEventName"] == _chain.EVENTS[event].hook_event


@pytest.mark.parametrize("event", ["pre_edit", "pre_read"])
def test_block_aborts_with_stderr_and_drops_advisories(event):
    blocker = member("blocker", lambda p: {"block": "BLOCKED: planted"})
    for members in ((blocker, says("x")), (says("x"), blocker)):
        rc, out, err = _chain.run(event, PAYLOAD, members=members)
        assert rc == 2 and "BLOCKED: planted" in err and out == ""


@pytest.mark.parametrize("event", ["pre_edit", "pre_read"])
def test_deny_aborts_with_a_permission_decision(event):
    denier = member("denier", lambda p: {"deny": "planted deny"})
    rc, out, _ = _chain.run(event, PAYLOAD, members=(says("advisory-text"), denier))
    assert rc == 0 and spec(out)["permissionDecision"] == "deny" and "advisory-text" not in out


@pytest.mark.parametrize("event", ["post_edit", "post_read"])
def test_a_post_chain_ignores_block_deny_and_rewrite(event):
    for verdict in ({"block": "x"}, {"deny": "x"}, {"updatedInput": {"a": 1}}):
        rc, out, _ = _chain.run(event, PAYLOAD, members=(says("kept"), member("late", lambda p, v=verdict: v)))
        assert rc == 0 and spec(out) == {"hookEventName": "PostToolUse", "additionalContext": "kept"}


@pytest.mark.parametrize("event", EVENTS)
def test_a_fault_names_the_member_and_keeps_prior_and_later_output(event):
    members = (says("before"), member("bad", boom), member("bad2", lambda p: "not an object"), says("after"))
    rc, out, err = _chain.run(event, PAYLOAD, members=members)
    text = spec(out)["additionalContext"]
    assert rc == 0 and "before" in text and "after" in text and "bad" in text and "bad2" in err


def test_an_unimportable_member_is_a_named_fault_that_keeps_later_output():
    rc, out, _ = _chain.run("pre_read", PAYLOAD, members=("no_such_member", says("after")))
    assert rc == 0 and "no_such_member" in spec(out)["additionalContext"] and "after" in out


def test_only_a_pre_chain_floor_member_blocks_on_its_own_fault():
    boom.FAIL_CLOSED = True
    try:
        pre = _chain.run("pre_edit", PAYLOAD, members=(boom,))
        post = _chain.run("post_edit", PAYLOAD, members=(boom,))
    finally:
        del boom.FAIL_CLOSED
    assert pre[0] == 2 and pre[1] == "" and post[0] == 0


def test_a_rewrite_reaches_later_members_and_stdout():
    seen = []
    fixer = member("fixer", lambda p: {"updatedInput": {"new_string": "fixed"}, "context": "fixed note"})
    observer = member("observer", lambda p: seen.append(p["tool_input"]))
    rc, out, _ = _chain.run("pre_edit", PAYLOAD, members=(fixer, observer))
    assert rc == 0 and seen == [{"file_path": "C:/repo/a.txt", "new_string": "fixed"}]
    assert spec(out)["updatedInput"] == seen[0]


def test_a_line_spent_by_a_member_is_told_again_after_a_later_block(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path))
    import _hook_state
    spender = member("spender", lambda p: {"context": "once-line"}
                     if _hook_state.fire_once_since_compaction("latch-edit", "k") else None)
    blocker = member("blocker", lambda p: {"block": "no"})
    _chain.run("pre_edit", PAYLOAD, members=(spender, blocker))
    assert "once-line" in _chain.run("pre_edit", PAYLOAD, members=(spender,))[1]
    assert "once-line" not in _chain.run("pre_edit", PAYLOAD, members=(spender,))[1]


def test_a_hung_post_member_times_out_without_losing_prior_output(monkeypatch):
    monkeypatch.setitem(_chain.EVENTS, "post_edit", _chain.EVENTS["post_edit"]._replace(timeout=0.2))
    rc, out, err = _chain.run("post_edit", PAYLOAD, members=(says("kept"), member("slow", lambda p: time.sleep(2))))
    assert rc == 0 and "kept" in out and "slow" in err and "timeout" in err


def test_an_oversize_context_is_bounded_and_prior_output_survives():
    big = "y" * (_chain.MAX_CONTEXT_CHARS + 500)
    rc, out, err = _chain.run("post_read", PAYLOAD, members=(says("kept"), member("big", lambda p: {"context": big})))
    assert "kept" in out and len(spec(out)["additionalContext"]) < len(big) and "truncated" in err


def test_shared_members_per_event_and_the_reaper_in_none():
    assert {e: r.shared for e, r in _chain.EVENTS.items() if e != "prompt"} == {
        "pre_edit": ("running_script_edit_guard",), "pre_read": ("semantic_search_scope_guard",),
        "post_edit": ("compact_directive_anchor",), "post_read": ()}
    assert not any("runaway_scan_reaper" in r.shared for r in _chain.EVENTS.values())


def test_the_real_chains_are_silent_on_a_plain_call(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path))
    for event in EVENTS:
        assert _chain.run(event, dict(PAYLOAD, cwd=str(tmp_path))) == (0, "", "")


@pytest.mark.parametrize("event", EVENTS)
def test_entry_stub_exits_clean_on_a_call_and_a_malformed_payload(event, tmp_path):
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=str(tmp_path), PYTHONIOENCODING="utf-8")
    entry = str(HOOKS / (event + "_dispatch.py"))
    for text in (json.dumps(dict(PAYLOAD, cwd=str(tmp_path))), "not json"):
        done = subprocess.run([sys.executable, entry], input=text, capture_output=True, text=True, env=env, timeout=120)
        assert done.returncode == 0 and done.stdout.strip() == "" and done.stderr == ""


def test_every_dispatcher_runs_the_one_chain_and_every_event_has_a_stub():
    """A new `*_dispatch.py` with its own loop fails here; `_floor` and `pre_workflow_dispatch` are the named exceptions."""
    own_loop = {"pre_workflow_dispatch.py"}
    stubs = {p.name for p in HOOKS.glob("*_dispatch.py")} - own_loop
    assert not {name for name in stubs if "_chain.main(" not in (HOOKS / name).read_text(encoding="utf-8")}
    assert stubs == {event + "_dispatch.py" for event in _chain.EVENTS}
