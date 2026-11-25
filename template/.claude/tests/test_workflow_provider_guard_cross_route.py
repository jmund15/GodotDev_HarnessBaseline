#!/usr/bin/env python3
"""Proof for `hooks/workflow_provider_guard.py` `_cross_plan_route`: the other plans a capacity-refused native job
can launch through. An unmetered plan has no room reading by design, and still takes the job.

    python3 .claude/tests/test_workflow_provider_guard_cross_route.py
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "workflow_provider_guard.py")
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))


def load():
    spec = importlib.util.spec_from_file_location("workflow_provider_guard", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def route(guard, plans):
    bands = guard._dispatch_checks.provider_bands
    saved = (bands.facts, bands.plan_entry, guard.call_pins, guard._dispatch_checks.read_capacity)
    guard._dispatch_checks.read_capacity = lambda transport, data=None: None
    bands.facts = lambda *a, **k: {"plans": plans}
    bands.plan_entry = lambda transport, reading: transport
    guard.call_pins = lambda payload: []
    try:
        data = {"transports": {"antigravity": {"launcher": "scripts/antigravity_sidecar.sh"},
                               "codex": {"launcher": "scripts/codex_proxy_sidecar.sh"}}}
        return guard._cross_plan_route({}, "anthropic", data)
    finally:
        bands.facts, bands.plan_entry, guard.call_pins, guard._dispatch_checks.read_capacity = saved


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def an_unmetered_plan_is_named_as_a_route():
    got = route(load(), [{"transport": "antigravity", "room": None, "unmetered": True, "why": "unmetered"}])
    return "antigravity via scripts/antigravity_sidecar.sh" in got, got


@case
def a_plan_dropped_without_a_reading_is_not_named():
    got = route(load(), [{"transport": "codex", "room": None, "why": "exhausted"}])
    return "codex" not in got and "no cross-transport row" in got, got


@case
def a_plan_with_room_is_named():
    got = route(load(), [{"transport": "codex", "room": 0.5}])
    return "codex via scripts/codex_proxy_sidecar.sh" in got, got


failed = 0
for fn in CASES:
    try:
        ok, detail = fn()
    except Exception as exc:  # a crash is a failure
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    print(("PASS " if ok else "FAIL ") + fn.__name__ + ("" if ok else f"   [{str(detail)[:300]}]"))
    failed += not ok
print(f"{len(CASES) - failed}/{len(CASES)} passed")
sys.exit(1 if failed else 0)
