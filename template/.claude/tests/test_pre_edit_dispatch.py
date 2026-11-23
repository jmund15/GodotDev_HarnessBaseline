"""Proof for hooks/pre_edit_dispatch.py, the PreToolUse Write|Edit chain: ordering, channels and fault posture
over a planted chain, plus the shared members through the real CHAIN. Planted members stand in for project hooks."""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))
import pre_edit_dispatch as dispatcher  # noqa: E402

PAYLOAD = {"tool_name": "Edit", "session_id": "pe1", "tool_input": {"file_path": "C:/repo/a.txt", "new_string": "x"}}
NOISY = ("noisy", lambda p: {"context": "advisory-from-noisy"})
BLOCKER = ("blocker", lambda p: {"block": "BLOCKED: planted"})
DENIER = ("denier", lambda p: {"deny": "planted deny"})


def boom(_payload):
    raise RuntimeError("planted fault")


def decision(out):
    spec = json.loads(out)["hookSpecificOutput"] if out else {}
    return spec.get("permissionDecision"), spec.get("additionalContext", "")


def test_block_aborts_in_either_position_and_drops_advisories():
    for chain in ((BLOCKER, NOISY), (NOISY, BLOCKER)):
        rc, out, err = dispatcher.dispatch(PAYLOAD, chain=chain)
        assert rc == 2 and "BLOCKED: planted" in err and out == ""


def test_deny_aborts_with_a_permission_decision():
    rc, out, _ = dispatcher.dispatch(PAYLOAD, chain=(NOISY, DENIER))
    assert rc == 0 and decision(out)[0] == "deny" and "advisory-from-noisy" not in out


def test_non_floor_faults_allow_name_the_hook_and_keep_later_output():
    for member in (boom, lambda p: "not an object"):
        rc, out, _ = dispatcher.dispatch(PAYLOAD, chain=(("bad", member), NOISY))
        verdict, context = decision(out)
        assert rc == 0 and verdict is None and "bad" in context and "advisory-from-noisy" in context


def test_import_fault_allows_and_names_the_hook(monkeypatch):
    def fail(name):
        raise ImportError("planted import fault")
    monkeypatch.setattr(dispatcher.importlib, "import_module", fail)
    rc, out, _ = dispatcher.dispatch(PAYLOAD, chain=(("missing_hook", "advisory"), NOISY))
    assert rc == 0 and "missing_hook" in decision(out)[1] and "advisory-from-noisy" in out


def test_floor_member_fault_blocks():
    boom.FAIL_CLOSED = True
    try:
        rc, out, err = dispatcher.dispatch(PAYLOAD, chain=(("floor", boom),))
    finally:
        del boom.FAIL_CLOSED
    assert rc == 2 and out == "" and "floor" in err


def test_a_rewrite_reaches_later_members_and_stdout():
    seen = []
    rewritten = dict(PAYLOAD["tool_input"], new_string="fixed")
    rc, out, _ = dispatcher.dispatch(PAYLOAD, chain=(
        ("fixer", lambda p: {"updatedInput": rewritten, "context": "fixed note"}),
        ("observer", lambda p: seen.append(p["tool_input"]["new_string"])),
    ))
    assert rc == 0 and seen == ["fixed"]
    assert json.loads(out)["hookSpecificOutput"]["updatedInput"] == rewritten


def test_a_line_spent_by_a_member_is_told_again_after_a_later_block(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path))
    import _hook_state

    def spender(p):
        return {"context": "once-line"} if _hook_state.fire_once_since_compaction("latch-edit", "k") else None
    dispatcher.dispatch(PAYLOAD, chain=(("spender", spender), BLOCKER))
    assert "once-line" in dispatcher.dispatch(PAYLOAD, chain=(("spender", spender),))[1]
    assert "once-line" not in dispatcher.dispatch(PAYLOAD, chain=(("spender", spender),))[1]


def test_shared_chain_is_the_running_script_guard_alone():
    assert [n for n, _ in dispatcher.CHAIN] == ["running_script_edit_guard"]


def test_the_real_chain_is_silent_on_an_edit_of_a_file_that_is_not_a_script():
    assert dispatcher.dispatch(PAYLOAD) == (0, "", "")


def test_entry_point_exits_clean_on_a_clean_edit_and_a_malformed_payload(tmp_path):
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=str(tmp_path), PYTHONIOENCODING="utf-8")
    entry = str(HOOKS / "pre_edit_dispatch.py")
    for text in (json.dumps(PAYLOAD), "not json"):
        done = subprocess.run([sys.executable, entry], input=text, capture_output=True, text=True, env=env, timeout=120)
        assert done.returncode == 0 and done.stdout.strip() in ("", "{}") and done.stderr == ""
