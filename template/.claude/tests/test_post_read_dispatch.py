"""Proof for hooks/post_read_dispatch.py, the PostToolUse read/search chain: member notes share one payload, a
fault stays fail-open, and the reaper runs only for tools the standalone reaper entry does not cover."""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))
import post_read_dispatch as dispatcher  # noqa: E402

PAYLOAD = {"tool_name": "Read", "session_id": "pr1", "tool_input": {"file_path": "C:/repo/a.txt"}}


def test_notes_join_in_order_and_a_fault_does_not_erase_them():
    def boom(_p):
        raise RuntimeError("planted")
    text = dispatcher.dispatch(PAYLOAD, chain=(("a", lambda p: {"context": "one"}), ("bad", boom),
                                              ("b", lambda p: {"context": "two"})))
    assert text == "one\n\ntwo"


def test_a_clean_call_has_no_note():
    assert dispatcher.dispatch(PAYLOAD, chain=(("m", lambda p: None),)) == ""


def test_reaper_runs_only_for_tools_outside_the_standalone_entry():
    ran = []
    chain = (("runaway_scan_reaper", lambda p: ran.append(p["tool_name"])),)
    for tool in ("Read", "Grep", "mcp__ai-worker__write_doc", "LSP", "mcp__plugin_x__y"):
        dispatcher.dispatch(dict(PAYLOAD, tool_name=tool), chain=chain)
    assert ran == ["Read", "Grep"]


def test_shared_chain_is_the_reaper_alone():
    assert [n for n, _ in dispatcher.CHAIN] == ["runaway_scan_reaper"]


def test_entry_point_exits_clean_and_prints_nothing_for_a_read(tmp_path):
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=str(tmp_path), HARNESS_REAPER_CACHE_DIR=str(tmp_path),
               PYTHONIOENCODING="utf-8")
    entry = str(HOOKS / "post_read_dispatch.py")
    for text in (json.dumps(PAYLOAD), "not json"):
        done = subprocess.run([sys.executable, entry], input=text, capture_output=True, text=True, env=env, timeout=120)
        assert done.returncode == 0 and done.stdout.strip() == ""
