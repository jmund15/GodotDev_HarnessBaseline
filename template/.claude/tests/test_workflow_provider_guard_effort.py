"""Proof cases for workflow_provider_guard RULE 0 — sonnet·low is denied on the review engine and nowhere
else (harness_tooling: every guard has a re-runnable proof; the negative cases are the point)."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _transport_fixture import hook_env

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "workflow_provider_guard.py")


def run_hook(payload, transport="anthropic"):
    """Run the guard on a PINNED seat. Defaults to anthropic because RULE 0 is an effort rule and
    these cases assert what it does with a legal Anthropic pin; on a provider seat the vocabulary
    rule denies `sonnet` first and the effort rule is never reached."""
    out = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                         text=True, timeout=30, env=hook_env(transport), encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout).get("hookSpecificOutput", {}) if out.stdout.strip() else {}


def wf(script, agents):
    return {"tool_name": "Workflow", "session_id": "effort-proof", "tool_input": {"scriptPath": script, "args": {"agents": agents}}}


def test_sonnet_low_on_review_fanout_is_denied_and_names_the_key():
    h = run_hook(wf(".claude/workflows/review_fanout.js", [{"key": "sa-testability", "model": "sonnet", "effort": "low"},
                                                           {"key": "sa-design", "model": "opus", "effort": "low"}]))
    assert h.get("permissionDecision") == "deny", h
    assert "sa-testability" in h["permissionDecisionReason"] and "sa-design" not in h["permissionDecisionReason"]
    assert "sonnet·medium" in h["permissionDecisionReason"]


def test_sonnet_medium_and_opus_low_on_review_fanout_pass():
    h = run_hook(wf(".claude/workflows/review_fanout.js", [{"key": "a", "model": "sonnet", "effort": "medium"},
                                                           {"key": "b", "model": "opus", "effort": "low"}]))
    assert h.get("permissionDecision") != "deny", h


def test_the_fixture_actually_controls_the_seat():
    """The load-bearing case: prove the pin BITES by flipping it.

    Every other case here pins `anthropic`, which on an Anthropic host is indistinguishable from
    pinning nothing. Running one identical payload on a codex seat must deny on VOCABULARY -- if
    this passes, the fixture is inert and the suite silently re-inherits the host."""
    payload = wf(".claude/workflows/review_fanout.js", [{"key": "a", "model": "sonnet", "effort": "medium"}])
    assert run_hook(payload, "anthropic").get("permissionDecision") != "deny"
    h = run_hook(payload, "codex")
    assert h.get("permissionDecision") == "deny", h
    assert "sonnet" in h["permissionDecisionReason"]


def test_omitted_model_on_review_fanout_at_low_is_the_same_cell():
    # review_fanout's DEFAULT_MODEL is sonnet: an omitted model at effort low lands on the denied cell.
    h = run_hook(wf(".claude/workflows/review_fanout.js", [{"key": "a", "effort": "low"}]))
    assert h.get("permissionDecision") == "deny", h


def test_sonnet_low_on_dispatch_engine_is_not_denied_by_this_rule():
    # dispatch.js runs execution jobs, where the ladder allows sonnet·low.
    h = run_hook({"tool_name": "Workflow", "session_id": "effort-proof",
                  "tool_input": {"scriptPath": ".claude/workflows/dispatch.js", "args": {"jobs": [{"label": "exec", "model": "sonnet", "effort": "low"}]}}})
    assert h.get("permissionDecision") != "deny", h


def test_args_as_json_string_are_parsed():
    p = wf(".claude/workflows/review_fanout.js", [])
    p["tool_input"]["args"] = json.dumps({"agents": [{"key": "s", "model": "sonnet", "effort": "low"}]})
    assert run_hook(p).get("permissionDecision") == "deny"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK")
