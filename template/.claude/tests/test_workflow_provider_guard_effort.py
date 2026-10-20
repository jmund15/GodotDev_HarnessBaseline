"""Proof cases for workflow_provider_guard RULE 0 against the LIVE registry: a review_fanout pin at a
rung its row lists in `effort.nonThinking` is denied on the review engine and nowhere else
(harness_tooling: every guard has a re-runnable proof; the negative cases are the point).

This suite reads the live registry and ladder on purpose: it proves the shipped registry carries a
non-thinking declaration the live hook enforces. The pins are derived from that data, never written
as model names; the synthetic-roster cases for the rule live in test_workflow_provider_guard.py.
Every pass case declares its tier, which RULE 3 requires in every band. Currency (RULE 4) only
advises, so no live reading can turn a pass case into a deny."""
import json
import os
import subprocess
import tempfile
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _transport_fixture import hook_env

CLAUDE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(CLAUDE, "hooks", "workflow_provider_guard.py")
sys.path.insert(0, os.path.join(CLAUDE, "hooks"))
import _session_transport  # noqa: E402  (puts tools/ on sys.path)
import model_registry  # noqa: E402
from _dispatch_pins import native_pin, tier_rows  # noqa: E402

HOST = _session_transport.HOST_TRANSPORT
# One host pin per tier for the fixtures: the first row claiming it. A test subject, not a default.
_PINS = {tier: native_pin(rows[0], HOST) for tier in model_registry.TIER_ORDER
         if (rows := tier_rows(tier, HOST, model_registry.load()))}


def _planted_registry():
    """The live registry with the fanout pin's row declaring `low` non-thinking, so RULE 0 has a
    subject whatever the live rows declare. The hook child reads it through the registry override."""
    data = model_registry.load()
    pin = _PINS["fanout"]
    row = next(m for m in data["models"] if m["transport"] == HOST and pin in (m["id"], m.get("alias")))
    row.setdefault("effort", {}).update(nonThinking=["low"], nonThinkingRef="planted by this proof")
    path = os.path.join(tempfile.mkdtemp(prefix="effort-proof-"), "external_models.json")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh)
    os.environ[model_registry.ENV_OVERRIDE] = path
    return data


_DATA = _planted_registry()


def _non_thinking_row():
    """(pin, tier, non-thinking rung, a thinking rung) for the first host tier pin whose row declares one."""
    for tier, pin in _PINS.items():
        row = next(m for m in _DATA["models"] if m["transport"] == HOST and pin in (m["id"], m.get("alias")))
        rungs = model_registry.non_thinking(row)
        if rungs:
            thinking = [r for r in model_registry.effort_values(row, _DATA) if r not in rungs]
            return pin, tier, rungs[0], thinking[0]
    raise AssertionError("no host tier pin declares effort.nonThinking in the live registry")


PIN, TIER, COLD, WARM = _non_thinking_row()
OTHER_TIER, OTHER = next((t, p) for t, p in _PINS.items() if p != PIN)


def run_hook(payload, transport=HOST):
    """Run the guard on a PINNED seat. Defaults to the host because RULE 0 is an effort rule and
    these cases assert what it does with a legal host pin; on a provider seat the vocabulary rule
    denies the host alias first and the effort rule is never reached."""
    out = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                         text=True, timeout=30, env=hook_env(transport), encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout).get("hookSpecificOutput", {}) if out.stdout.strip() else {}


def wf(script, agents):
    return {"tool_name": "Workflow", "session_id": "effort-proof", "tool_input": {"scriptPath": script, "args": {"agents": agents}}}


def test_non_thinking_rung_on_review_fanout_is_denied_and_names_the_key():
    h = run_hook(wf(".claude/workflows/review_fanout.js", [{"key": "sa-testability", "model": PIN, "effort": COLD},
                                                           {"key": "sa-design", "model": OTHER, "effort": COLD}]))
    assert h.get("permissionDecision") == "deny", h
    reason = h["permissionDecisionReason"]
    assert "sa-testability" in reason and "sa-design" not in reason, reason
    assert "effort.nonThinking" in reason, reason


def test_thinking_rung_and_other_rows_on_review_fanout_pass():
    h = run_hook(wf(".claude/workflows/review_fanout.js", [{"key": "a", "model": PIN, "effort": WARM, "tier": TIER},
                                                           {"key": "b", "model": OTHER, "effort": COLD, "tier": OTHER_TIER}]))
    assert h.get("permissionDecision") != "deny", h


def test_the_fixture_actually_controls_the_seat():
    """The load-bearing case: prove the pin BITES by flipping it.

    Every other case here pins the host transport, which on a host session is indistinguishable from
    pinning nothing. Running one identical payload on a provider seat must deny on VOCABULARY -- if
    this passes, the fixture is inert and the suite silently re-inherits the host."""
    payload = wf(".claude/workflows/review_fanout.js", [{"key": "a", "model": PIN, "effort": WARM, "tier": TIER}])
    assert run_hook(payload, HOST).get("permissionDecision") != "deny"
    provider = next(t for t in model_registry.transports(_DATA) if model_registry.route_of(t, _DATA) != HOST)
    h = run_hook(payload, provider)
    assert h.get("permissionDecision") == "deny", h
    assert PIN in h["permissionDecisionReason"]


def test_non_thinking_rung_on_dispatch_engine_is_not_denied_by_this_rule():
    # dispatch.js runs execution jobs, where the ladder allows the non-thinking rung.
    h = run_hook({"tool_name": "Workflow", "session_id": "effort-proof",
                  "tool_input": {"scriptPath": ".claude/workflows/dispatch.js", "args": {"jobs": [{"label": "exec", "model": PIN, "effort": COLD, "tier": TIER}]}}})
    assert h.get("permissionDecision") != "deny", h


def test_args_as_json_string_are_parsed():
    p = wf(".claude/workflows/review_fanout.js", [])
    p["tool_input"]["args"] = json.dumps({"agents": [{"key": "s", "model": PIN, "effort": COLD}]})
    assert run_hook(p).get("permissionDecision") == "deny"


def test_model_specific_effort_is_checked_before_dispatch():
    row = model_registry.resolve("flash", _DATA)
    assert row["transport"] == "deepseek"
    allowed = model_registry.effort_values(row, _DATA)
    invalid = next(v for v in ("medium", "xhigh", "none") if v not in allowed)
    payload = {"tool_name": "Workflow", "session_id": "effort-vocab-proof",
               "tool_input": {"script": "export const meta = {name: 'effort-proof'}; return {}",
                              "args": {"model": row["id"], "effort": invalid, "tier": "executor"}}}
    denied = run_hook(payload, "deepseek")
    assert denied.get("permissionDecision") == "deny", denied
    assert invalid in denied.get("permissionDecisionReason", "") and row["id"] in denied["permissionDecisionReason"]
    payload["tool_input"]["args"]["effort"] = allowed[0]
    accepted = run_hook(payload, "deepseek")
    assert accepted.get("permissionDecision") != "deny", accepted


def test_nested_job_effort_uses_its_own_model_row():
    row = model_registry.resolve("flash", _DATA)
    invalid = next(v for v in ("medium", "xhigh", "none") if v not in model_registry.effort_values(row, _DATA))
    payload = {"tool_name": "Workflow", "session_id": "effort-nested-proof",
               "tool_input": {"scriptPath": ".claude/workflows/dispatch.js",
                              "args": {"jobs": [{"label": "invalid-rung", "model": row["id"],
                                                  "effort": invalid, "tier": "executor"}]}}}
    denied = run_hook(payload, "deepseek")
    assert denied.get("permissionDecision") == "deny", denied
    assert "invalid-rung" in denied.get("permissionDecisionReason", ""), denied


def _host_rows():
    return [m for m in _DATA["models"] if m["transport"] == HOST and m.get("lifecycle") == "current"]


def _avoid_subject():
    """(pin, tier, avoided rung) of a live host row whose registry `effort.avoid` is non-empty."""
    for row in _host_rows():
        rungs = model_registry.avoided_rungs(row)
        if rungs:
            return native_pin(row, HOST), row["roles"][0], rungs[0]
    raise AssertionError("no host row declares effort.avoid in the live registry")


def _free_subject(rung):
    """(pin, tier) of a live host row that serves `rung` and does not avoid it."""
    for row in _host_rows():
        if rung in model_registry.effort_values(row, _DATA) and rung not in model_registry.avoided_rungs(row):
            return native_pin(row, HOST), row["roles"][0]
    raise AssertionError("no live host row serves %s without avoiding it" % rung)


def _job_call(pin, tier, rung, **extra):
    return {"tool_name": "Workflow", "session_id": "avoid-proof",
            "tool_input": {"scriptPath": ".claude/workflows/dispatch.js",
                           "args": dict({"jobs": [{"label": "waste-probe", "model": pin, "effort": rung, "tier": tier}]},
                                        **extra)}}


def test_an_avoided_rung_is_denied_with_the_rows_evidence_and_no_measurement_lifts_it():
    pin, tier, rung = _avoid_subject()
    row = next(m for m in _host_rows() if native_pin(m, HOST) == pin)
    denied = run_hook(_job_call(pin, tier, rung))
    assert denied.get("permissionDecision") == "deny", denied
    reason = denied["permissionDecisionReason"]
    assert "waste-probe" in reason and rung in reason and "effort.avoid" in reason, reason
    assert model_registry.avoid_evidence(row) in reason, reason
    still = run_hook(_job_call(pin, tier, rung, measurement="effort-rung measurement: the avoided rung on purpose"))
    assert still.get("permissionDecision") == "deny" and "effort.avoid" in still["permissionDecisionReason"], still


def test_a_short_measurement_does_not_lift_an_avoided_rung():
    pin, tier, rung = _avoid_subject()
    assert run_hook(_job_call(pin, tier, rung, measurement="bench")).get("permissionDecision") == "deny"


def test_a_row_that_does_not_avoid_the_rung_passes_without_a_measurement():
    _pin, _tier, rung = _avoid_subject()
    pin, tier = _free_subject(rung)
    assert run_hook(_job_call(pin, tier, rung)).get("permissionDecision") != "deny"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK")
