"""Proof for hooks/pre_read_dispatch.py, the PreToolUse read/search chain: a block exits 2 with stderr, a rewrite
and notes share one hookSpecificOutput, and a member fault stays fail-open and named."""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))
import pre_read_dispatch as dispatcher  # noqa: E402

PAYLOAD = {"tool_name": "Read", "session_id": "rd1", "tool_input": {"file_path": "C:/repo/a.txt"}}


def spec(out):
    return json.loads(out)["hookSpecificOutput"]


def test_deny_and_block_exit_2_with_stderr():
    for key in ("deny", "block"):
        rc, out, err = dispatcher.dispatch(PAYLOAD, chain=(("m", lambda p, k=key: {k: "stop here"}),))
        assert (rc, out) == (2, "") and "stop here" in err


def test_rewrites_merge_and_notes_join_into_one_output():
    rc, out, _ = dispatcher.dispatch(PAYLOAD, chain=(
        ("a", lambda p: {"rewrite": {"limit": 5}, "note": "n1"}),
        ("b", lambda p: {"rewrite": {"offset": 2}, "advisory": "n2"}),
    ))
    assert rc == 0 and spec(out)["updatedInput"] == {"limit": 5, "offset": 2}
    assert spec(out)["additionalContext"] == "n1\nn2" and spec(out)["hookEventName"] == "PreToolUse"


def test_a_clean_call_emits_nothing():
    assert dispatcher.dispatch(PAYLOAD, chain=(("m", lambda p: None),)) == (0, "", "")


def test_a_member_fault_is_named_and_later_members_still_run():
    def boom(_p):
        raise RuntimeError("planted")
    rc, out, _ = dispatcher.dispatch(PAYLOAD, chain=(("bad", boom), ("late", lambda p: {"note": "later"})))
    assert rc == 0 and "bad failed" in spec(out)["additionalContext"] and "later" in spec(out)["additionalContext"]


def test_an_unimportable_member_names_its_repair():
    rc, out, _ = dispatcher.dispatch(PAYLOAD, chain=(("no_such_member", None),))
    assert rc == 0 and "Repair hooks/no_such_member.py" in spec(out)["additionalContext"]


def test_shared_chain_is_the_scope_guard_alone():
    assert [n for n, _ in dispatcher.CHAIN] == ["semantic_search_scope_guard"]


def test_the_real_chain_runs_without_a_fault_on_a_search(tmp_path):
    payload = {"tool_name": "mcp__plugin_semantic-search_semantic-search__search", "session_id": "rd2",
               "cwd": str(tmp_path), "tool_input": {"query": "x", "searchDir": str(tmp_path)}}
    rc, out, err = dispatcher.dispatch(payload)
    assert rc in (0, 2) and "failed" not in out and "Traceback" not in err  # the verdict is test_semantic_search_scope_guard's


def test_entry_point_exits_clean_on_a_read_and_a_malformed_payload(tmp_path):
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=str(tmp_path), PYTHONIOENCODING="utf-8")
    entry = str(HOOKS / "pre_read_dispatch.py")
    for text in (json.dumps(PAYLOAD), "not json"):
        done = subprocess.run([sys.executable, entry], input=text, capture_output=True, text=True, env=env, timeout=120)
        assert done.returncode == 0 and done.stdout.strip() == "" and done.stderr == ""
