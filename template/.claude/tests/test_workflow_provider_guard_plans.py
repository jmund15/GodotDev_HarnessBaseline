#!/usr/bin/env python3
"""Proof that workflow_provider_guard keys who-pays on the session PLAN, not its route.

A plan (`planOf`) pays for its route's rows through its own launcher, so a hot 5-hour window on
one Anthropic plan can move a job to the other. `_width_steps` builds each job's next step from
`model_registry.for_role`; this plants a registry with a host plan sibling and reads the step text.
The RULE 2 cases feed real payloads to `main()` with the session plan taken from a planted
CLAUDE_CONFIG_DIR and assert on the emitted `permissionDecision`.

Run: python3 .claude/tests/test_workflow_provider_guard_plans.py
"""
import contextlib
import copy
import importlib.util
import io
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "workflow_provider_guard.py")
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
spec = importlib.util.spec_from_file_location("wpg_plans", HOOK)
wpg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wpg)

HOST_LAUNCHER = ".claude/scripts/host_sidecar.sh"
PLAN_LAUNCHER = ".claude/scripts/host_b_sidecar.sh"
SYN = {
    "transports": {
        "host": {"costModel": "plan-quota", "launcher": HOST_LAUNCHER},
        "host-b": {"costModel": "plan-quota", "planOf": "host", "launcher": PLAN_LAUNCHER,
                   "status": {"state": "available"}},
    },
    "models": [
        {"transport": "host", "id": "host-exec-1", "alias": "h-exec", "roles": ["executor"]},
    ],
}
PINS = [("j0", "h-exec", "executor", False)]


def steps(seat, data):
    return "; ".join(wpg._width_steps(PINS, [], "12:00", data, seat))


PLAN_SYN = {
    "transports": {
        "host": {"costModel": "plan-quota", "launcher": HOST_LAUNCHER, "configDir": "~/.claude-host"},
        "host-b": {"costModel": "plan-quota", "planOf": "host", "launcher": PLAN_LAUNCHER,
                   "configDir": "~/.claude-host-b", "status": {"state": "available"}},
    },
    "models": [
        {"transport": "host", "id": "host-exec-1", "alias": "h-exec", "roles": ["executor"]},
    ],
}


def reading(used, left=0.5):
    return {"status": "available", "quota": {"routingWindow": "seven_day", "windows": [
        {"name": "seven_day", "usedPercent": used, "resetsAt": 1e12, "windowDurationMins": 10080}]}}


def run_plan(config_dir, exhausted, rooms, data=PLAN_SYN):
    """(permissionDecision, reason, plans the capacity gate was asked about, additionalContext) for one
    executor job."""
    st, mr, checks = wpg._session_transport, wpg.model_registry, wpg._dispatch_checks
    asked = []

    def refusal(plan, *_a, **_k):
        asked.append(plan)
        return ("Provider capacity exhausted for %s; native Workflow/Agent dispatch is blocked" % plan
                if plan == exhausted else None)

    payload = {"tool_name": "Workflow", "session_id": "plan0001", "tool_input": {
        "scriptPath": ".claude/workflows/dispatch.js",
        "args": {"jobs": [{"label": "j0", "model": "h-exec", "tier": "executor", "effort": "high",
                           "promptPath": "x", "agentType": "Explore"}]}}}
    state_dir = tempfile.mkdtemp(prefix="wpg_plan_")
    with open(os.path.join(state_dir, "plan0001.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"skills_loaded": ["orchestration"]}, fh)
    saved = [(st, "_registry"), (st, "HOST_TRANSPORT"), (wpg, "band_and_pressure"),
             (wpg.provider_capacity_guard, "refusal"), (mr, "load"), (mr, "role_tiers"),
             (wpg._hook_state, "state_path"), (checks, "read_capacity"), (sys, "stdin")]
    saved = [(o, a, getattr(o, a)) for o, a in saved]
    env_saved = {k: os.environ.get(k) for k in ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_TRANSPORT",
                                                  "ANTHROPIC_BASE_URL")}
    out = io.StringIO()
    try:
        os.environ.pop("CLAUDE_CODE_TRANSPORT", None)
        os.environ.pop("ANTHROPIC_BASE_URL", None)
        os.environ["CLAUDE_CONFIG_DIR"] = config_dir
        st._registry = lambda: data
        st.HOST_TRANSPORT = "host"
        wpg.band_and_pressure = lambda *_a, **_k: ("On pace", None)
        wpg.provider_capacity_guard.refusal = refusal
        mr.load = lambda *_a, **_k: data
        mr.role_tiers = lambda *_a, **_k: {"executor": ["h-exec"]}
        wpg._hook_state.state_path = lambda s: os.path.join(state_dir, str(s or "default")[:8] + ".json")
        checks.read_capacity = lambda t, *_a, **_k: rooms.get(t)
        sys.stdin = io.StringIO(json.dumps(payload))
        with contextlib.redirect_stdout(out):
            wpg.main()
    finally:
        for o, a, v in saved:
            setattr(o, a, v)
        for k, v in env_saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    raw = out.getvalue().strip()
    hso = (json.loads(raw).get("hookSpecificOutput") if raw else {}) or {}
    return (hso.get("permissionDecision"), hso.get("permissionDecisionReason") or "", asked,
            hso.get("additionalContext") or "")


def main():
    cases = []

    def case(name, ok, detail):
        cases.append((name, bool(ok), detail))

    text = steps("host", SYN)
    case("a hot primary plan's job is offered the other plan's launcher",
         "bash " + PLAN_LAUNCHER in text and "-l j0" in text and "bash " + HOST_LAUNCHER not in text, text)

    text = steps("host-b", SYN)
    case("a hot secondary plan's job is offered the primary launcher",
         "bash " + HOST_LAUNCHER in text and "bash " + PLAN_LAUNCHER not in text, text)

    mixed = copy.deepcopy(SYN)
    mixed["transports"]["off"] = {"costModel": "marginal-usd", "launcher": ".claude/scripts/off_sidecar.sh"}
    mixed["models"].append({"transport": "off", "id": "off-exec-1", "alias": "o-exec", "roles": ["executor"]})
    text = steps("host", mixed)
    case("the other plan of the session's route is offered ahead of a cross-transport row (same model)",
         "bash " + PLAN_LAUNCHER in text and "off_sidecar.sh" not in text, text)

    off = copy.deepcopy(SYN)
    off["transports"]["host-b"]["status"] = {"state": "unavailable", "reason": "planted"}
    text = steps("host", off)
    case("an unavailable plan is not offered; the job waits",
         PLAN_LAUNCHER not in text and "no cross-transport row serves executor" in text, text)

    home = os.path.expanduser("~")
    rooms = {"host": reading(40), "host-b": None}
    decision, reason, asked, _ctx = run_plan(os.path.join(home, ".claude-host-b"), "host-b", rooms)
    case("a secondary session with the secondary exhausted is denied natively and offered the primary",
         decision == "deny" and "host-b" in reason and HOST_LAUNCHER in reason, repr((decision, asked, reason[:400])))

    decision, reason, asked, _ctx = run_plan(os.path.join(home, ".claude-host-b"), "host",
                                       {"host": None, "host-b": reading(40)})
    case("the primary exhausted does not deny a secondary session",
         decision != "deny" and "host-b" in asked, repr((decision, asked, reason[:300])))

    odd = os.path.join(home, ".claude-elsewhere")
    decision, reason, asked, _ctx = run_plan(odd, None, rooms)
    case("an unknown config dir is denied, printing the dir and only the registry configDir remedy",
         decision == "deny" and odd in reason and "configDir" in reason
         and "CLAUDE_CODE_TRANSPORT" not in reason, repr((decision, reason[:500])))

    st = wpg._session_transport
    real_plan, real_unknown = st.session_plan, st.UNKNOWN

    def broken(*_a, **_k):
        raise RuntimeError("planted resolver failure")
    try:
        st.session_plan = broken
        decision, reason, asked, _ctx = run_plan(os.path.join(home, ".claude-host-b"), None, rooms)
    finally:
        st.session_plan = real_plan
    case("a session_plan failure denies as an unknown plan instead of gating the route's plan",
         decision == "deny" and "Unknown plan" in reason and asked == [], repr((decision, asked, reason[:300])))

    try:
        st.UNKNOWN = "planted-unknown"
        st.session_plan = lambda *_a, **_k: "planted-unknown"
        decision, reason, asked, _ctx = run_plan(os.path.join(home, ".claude-host-b"), None, rooms)
    finally:
        st.session_plan, st.UNKNOWN = real_plan, real_unknown
    case("the guard's unknown plan is _session_transport.UNKNOWN, not a second literal",
         decision == "deny" and "Unknown plan" in reason, repr((decision, asked, reason[:300])))

    # Probation, cheaper claimants and the plan rooms are not call-time notes: an admitted call carries none.
    marked = copy.deepcopy(PLAN_SYN)
    marked["models"][0]["probation"] = [{"tier": "executor", "text": "planted mark", "evidenceRef": "fixture"}]
    secondary = os.path.join(home, ".claude-host-b")
    decision, reason, asked, ctx = run_plan(secondary, None, rooms, data=marked)
    case("a Workflow pin the owner marked for its tier is admitted with no probation note",
         decision != "deny" and "probation" not in ctx and "planted mark" not in ctx,
         repr((decision, reason[:200], ctx[:400])))
    decision, reason, asked, ctx = run_plan(secondary, None, rooms)
    case("a claimed pin is admitted with no note of any kind",
         decision != "deny" and "[provider check]" not in ctx, repr((decision, ctx[:400])))
    case("the guard keeps no currency, probation or cheaper-claimant note builders",
         not any(hasattr(wpg, name) for name in ("currency_notes", "probation_notes", "cheaper_notes")), "")

    failed =[n for n, ok, _d in cases if not ok]
    for name, ok, detail in cases:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok else "   [%s]" % detail[:500]))
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
