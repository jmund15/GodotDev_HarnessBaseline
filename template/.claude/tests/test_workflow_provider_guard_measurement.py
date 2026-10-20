"""Proof: `args.measurement` is the one declared configuration a guard reads from the call (plan
self-attested-guard-fields-audit, Classification). No observable fact tells a benchmark arm from a
normal job, so a call that declares itself a measurement skips implied-tier fit (RULE 3). Negative cases: the same call without the declaration still denies on fit,
and a declaration under the minimum length is no declaration."""
import os
import sys

CLAUDE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(CLAUDE, "hooks"))
import workflow_provider_guard as g  # noqa: E402

DATA = g._load_registry()
HOST = "anthropic"
SCOUT_PIN = next(p for p in ("haiku",) if p)  # a scout-tier host alias; the lens below implies architect


def call(measurement=None):
    args = {"agents": [{"key": "plc-memory-alignment", "model": SCOUT_PIN, "effort": "low"}]}
    if measurement is not None:
        args["measurement"] = measurement
    return {"tool_name": "Workflow", "tool_input": {"scriptPath": ".claude/workflows/review_fanout.js", "args": args}}


def test_implied_tier_fit_denies_without_a_declaration():
    verdict, body, _ = g.decide_tiers(call(), HOST, DATA)
    assert verdict == "deny" and "architect" in body, (verdict, body)


def test_a_measurement_skips_implied_tier_fit():
    verdict, body, _ = g.decide_tiers(call("rail battery arm: haiku on a plan-review probe"), HOST, DATA)
    assert verdict is None and any("measurement" in n for n in body), (verdict, body)


def test_a_short_declaration_is_no_declaration():
    verdict, _body, _ = g.decide_tiers(call("bench"), HOST, DATA)
    assert verdict == "deny", verdict


def test_a_measurement_call_seating_a_lone_supplementary_seat_returns_no_deny():
    import copy
    data = copy.deepcopy(DATA)
    for row in data["models"]:
        if row["alias"] == "haiku":
            row["supplementary"] = ["architect"]
    plain, _body, _ = g.decide_tiers(call(), HOST, data)
    assert plain == "deny", plain
    verdict, body, _ = g.decide_tiers(call("rail battery arm: haiku on a plan-review probe"), HOST, data)
    assert verdict is None and any("measurement" in n for n in body), (verdict, body)


def test_tier_alias_rewrites_once_through_the_emitted_input(monkeypatch, capsys):
    import copy
    import json
    data = {"transports": {HOST: {}}, "models": [
        {"transport": HOST, "id": "claude-haiku-5-5", "alias": "haiku55",
         "family": "haiku", "roles": ["fanout"]},
        {"transport": HOST, "id": "claude-sonnet-5-5", "alias": "sonnet",
         "family": "sonnet", "roles": ["fanout"]},
    ]}
    payload = call()
    payload["tool_input"]["args"]["agents"][0]["key"] = "sa-testability"
    original = copy.deepcopy(payload)
    monkeypatch.setattr(g, "lens_tiers", lambda args: {"sa-testability": "fanout"})
    monkeypatch.setattr(g, "_load_registry", lambda: data)
    monkeypatch.setattr(g._session_transport, "_registry", lambda: data)
    monkeypatch.setattr(g._session_transport, "resolve", lambda: (HOST, "proof"))
    monkeypatch.setattr(g, "_session_plan", lambda: HOST)
    monkeypatch.setattr(g.provider_capacity_guard, "refusal", lambda plan: None)
    monkeypatch.setattr(g, "band_and_pressure", lambda *args: (None, None))
    monkeypatch.setattr(g, "five_hour_rule", lambda *args: (None, None))
    monkeypatch.setattr(g, "_session_record", lambda payload: (None, {"skills_loaded": ["orchestration"]}, None))
    monkeypatch.setattr(g.citer_coverage, "workflow_reason", lambda payload: None)
    for wire_string in (False, True):
        p = copy.deepcopy(payload)
        if wire_string:
            p["tool_input"]["args"] = json.dumps(p["tool_input"]["args"])
        g.check(p)
        h = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
        assert h.get("permissionDecision") != "deny", h
        args = h["updatedInput"]["args"]
        assert isinstance(args, str) == wire_string
        args = json.loads(args) if wire_string else args
        assert args["agents"][0]["model"] == "claude-haiku-5-5"
        assert "__transport" in args
        assert "haiku → claude-haiku-5-5" in h["additionalContext"]
    assert payload == original
    for rows in ([], [dict(data["models"][0], id="another-haiku")]):
        ambiguous = copy.deepcopy(data)
        ambiguous["models"] = rows + data["models"] if rows else [data["models"][1]]
        monkeypatch.setattr(g, "_load_registry", lambda: ambiguous)
        monkeypatch.setattr(g._session_transport, "_registry", lambda: ambiguous)
        g.check(payload)
        h = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
        assert h.get("permissionDecision") == "deny", h
        assert "updatedInput" not in h


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
