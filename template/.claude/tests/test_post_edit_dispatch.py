"""Proof for hooks/post_edit_dispatch.py, the PostToolUse Write|Edit chain: advisories merge into one
additionalContext, and a fault, a hang or an oversize text never erases another member's output."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))
import post_edit_dispatch as dispatcher  # noqa: E402

PAYLOAD = {"tool_name": "Write", "session_id": "po1", "tool_input": {"file_path": "C:/repo/a.txt", "content": "x"}}
NOISY = ("noisy", lambda p: {"context": "advisory-from-noisy"})


def boom(_payload):
    raise RuntimeError("planted fault")


def context(out):
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


def test_advisories_merge_into_one_payload_in_chain_order():
    rc, out, _ = dispatcher.dispatch(PAYLOAD, chain=(("a", lambda p: {"context": "one"}),
                                                     ("b", lambda p: {"context": "two"})))
    assert rc == 0 and context(out) == "one\n\ntwo"


def test_retired_block_and_deny_results_cannot_erase_an_advisory():
    for verdict in ({"block": "x"}, {"deny": "x"}):
        rc, out, _ = dispatcher.dispatch(PAYLOAD, chain=(NOISY, ("late", lambda p, v=verdict: v)))
        assert rc == 0 and "advisory-from-noisy" in context(out)


def test_runtime_fault_names_the_member_and_keeps_prior_and_later_output():
    rc, out, err = dispatcher.dispatch(PAYLOAD, chain=(NOISY, ("bad", boom),
                                                       ("late", lambda p: {"context": "later"})))
    assert rc == 0 and "advisory-from-noisy" in context(out) and "later" in context(out) and "bad" in err


def test_import_fault_names_the_member_and_keeps_later_output():
    rc, out, err = dispatcher.dispatch(PAYLOAD, chain=(("no_such_member", None), NOISY))
    assert rc == 0 and "advisory-from-noisy" in context(out) and "no_such_member" in err


def test_a_hung_member_times_out_without_losing_prior_output(monkeypatch):
    monkeypatch.setattr(dispatcher, "SUBHOOK_TIMEOUT", 0.2)
    rc, out, err = dispatcher.dispatch(PAYLOAD, chain=(NOISY, ("slow", lambda p: time.sleep(2))))
    assert rc == 0 and "advisory-from-noisy" in context(out) and "slow" in err and "timeout" in err


def test_an_oversize_context_is_bounded_and_prior_output_survives():
    big = "y" * (dispatcher.MAX_CONTEXT_CHARS + 500)
    rc, out, err = dispatcher.dispatch(PAYLOAD, chain=(NOISY, ("big", lambda p: {"context": big})))
    text = context(out)
    assert "advisory-from-noisy" in text and len(text) < len(big) and "truncated" in err


def test_shared_chain_is_the_reaper_alone():
    assert [n for n, _ in dispatcher.CHAIN] == ["runaway_scan_reaper"]


def test_entry_point_exits_clean_on_a_write_and_a_malformed_payload(tmp_path):
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=str(tmp_path), HARNESS_REAPER_CACHE_DIR=str(tmp_path),
               PYTHONIOENCODING="utf-8")
    entry = str(HOOKS / "post_edit_dispatch.py")
    for text in (json.dumps(PAYLOAD), "not json"):
        done = subprocess.run([sys.executable, entry], input=text, capture_output=True, text=True, env=env, timeout=120)
        assert done.returncode == 0 and done.stdout.strip() in ("", "{}")
