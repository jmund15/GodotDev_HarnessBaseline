#!/usr/bin/env python3
"""Proof for `tools/quota_fit.py`: a job starts only when a plan's quota windows hold its expected drain.

Every case plants the capacity reading (cut from a live `codex_quota_probe.py` reading, 2026-11-18), the clock,
the live job load and the per-window drain; the registry is the tracked one plus one synthetic codex row. The drain
seam is stubbed here; `test_provider_bands_*` drive the real `job_drain`. The exclude case plants the process table.

    python3 .claude/tests/test_quota_fit.py
"""
import importlib.util
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "quota_fit.py")
sys.path.insert(0, os.path.dirname(TOOL))
spec = importlib.util.spec_from_file_location("quota_fit", TOOL)
wf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wf)

NOW = 1795020000.0
FIVE_H_RESET = NOW + 3 * 3600
WEEK_RESET = NOW + 4 * 86400


def reading(five_used, week_used, status="available"):
    windows = [
        {"name": "five_hour", "usedPercent": five_used, "windowDurationMins": 300, "resetsAt": FIVE_H_RESET,
         "pressure": 1.0, "band": "Hot"},
        {"name": "seven_day", "usedPercent": week_used, "windowDurationMins": 10080, "resetsAt": WEEK_RESET,
         "pressure": 1.0, "band": "On pace"},
    ]
    return {"schemaVersion": 1, "transport": "codex", "costModel": "plan-quota", "sourceKind": "live",
            "observedAt": "2026-11-18T16:38:14Z", "status": status, "error": None, "liveFailure": None,
            "quota": {"windows": windows, "dispatchBand": "Hot", "routingBand": "Hot", "routingWindow": "seven_day",
                      "bindingWindow": "five_hour",
                      "credits": {"enabled": False, "available": False, "balance": "0"}},
            "planType": "plus"}


def reader(five_used, week_used, status="available"):
    def read(transport, data=None):
        if transport != "codex":
            return None
        return reading(five_used, week_used, status)
    return read


def drains(per_window):
    def job_drain(plan, window, model, effort, shape, jobs=None, samples=None):
        return {"pct": per_window.get(window, 0.0), "basis": "measured", "usd": None, "rate": None, "span": None,
                "why": None}
    return job_drain


SYNTH_ID, SYNTH_ALIAS = "synthetic-bench-model", "synthbench"


def registry():
    """The tracked registry plus one synthetic current row on its `codex` transport, so no roster change moves a
    case."""
    data = json.loads(json.dumps(wf.model_registry.load()))
    template = next(m for m in data["models"] if m.get("transport") == "codex" and m.get("lifecycle") == "current")
    row = json.loads(json.dumps(template))
    row.update(id=SYNTH_ID, alias=SYNTH_ALIAS, label="Synthetic Bench Model", version=SYNTH_ID, family=SYNTH_ALIAS,
               lifecycle="current", order=9999, roles=[])
    row.pop("status", None)
    data["models"].append(row)
    return data


DATA = registry()


def run(case, five_used, week_used, per_window, load=None, status="available", alias=SYNTH_ALIAS):
    wf.provider_bands.job_drain = drains(per_window)
    return wf.decide(alias, "max", "arm", data=DATA, read=reader(five_used, week_used, status), now=NOW,
                     load=load if load is not None else {})


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def fits_with_room():
    code, text = run("room", 20, 20, {"five_hour": 22, "seven_day": 3})
    assert code == 0, (code, text)


@case
def short_window_short_of_room_waits_for_its_reset():
    code, text = run("short", 94, 20, {"five_hour": 22, "seven_day": 3})
    assert code == 10, (code, text)
    seconds = int(text.split()[0])
    assert seconds == int(FIVE_H_RESET - NOW), (seconds, FIVE_H_RESET - NOW)


@case
def running_jobs_count_against_the_window():
    running = wf.sidecar_fanout.Running((SYNTH_ID, "max", "arm"))
    code, text = run("load", 50, 20, {"five_hour": 22, "seven_day": 3}, load={"codex": [running, running]})
    assert code == 10, (code, text)


@case
def long_window_short_of_room_halts():
    code, text = run("week", 10, 98.5, {"five_hour": 22, "seven_day": 3})
    assert code == 4, (code, text)


@case
def no_reading_leaves_the_launcher_gate_to_decide():
    code, text = run("blind", 94, 20, {"five_hour": 22}, status="unknown")
    assert code == 0, (code, text)


def _launcher_row(pid, record):
    """A live launcher process row as `reap.process_table` gives it: the codex launcher running this job."""
    return {"pid": pid, "ppid": 1, "name": "bash.exe", "start": "2026-11-18 17:00:00",
            "cmd": '"C:\\Program Files\\Git\\bin\\bash.exe" .claude/scripts/codex_proxy_sidecar.sh -m %s -e max -G arm '
                   '-R %s -l probe -f brief.md' % (SYNTH_ALIAS, record)}


@case
def the_asking_launcher_never_counts_against_itself():
    """At 50% used one more job (22) fits beside one running sibling (22) but not beside two: the asking
    launcher's own process must leave the count, its sibling must stay in it."""
    wf.provider_bands.job_drain = drains({"five_hour": 22, "seven_day": 3})
    table = {1: _launcher_row(1, "C:/out/self.record.json"), 2: _launcher_row(2, "C:/out/sibling.record.json")}
    import reap
    original = reap.process_table
    reap.process_table = lambda: table
    try:
        read = reader(50, 20)
        code_self, text_self = wf.decide(SYNTH_ALIAS, "max", "arm", data=DATA, read=read, now=NOW,
                                         exclude="C:/out/self.record.json")
        code_all, text_all = wf.decide(SYNTH_ALIAS, "max", "arm", data=DATA, read=read, now=NOW)
    finally:
        reap.process_table = original
    assert code_self == 0, (code_self, text_self)
    assert code_all == 10, (code_all, text_all)


@case
def an_unset_effort_or_shape_reads_as_none():
    wf.provider_bands.job_drain = drains({"five_hour": 22, "seven_day": 3})
    code, text = wf.decide(SYNTH_ALIAS, "-", "-", data=DATA, read=reader(20, 20), now=NOW, load={})
    assert code == 0, (code, text)


@case
def unknown_alias_is_a_usage_error():
    code, text = run("alias", 20, 20, {}, alias="no-such-model")
    assert code == 2, (code, text)


def sibling_run(plan):
    """The synthetic codex route with a roomy sibling plan of its own (`codex-spare`); the paying plan is full."""
    data = registry()
    data["transports"]["codex-spare"] = dict(data["transports"]["codex"], planOf="codex")

    def read(transport, data=None):
        if transport == "codex":
            return reading(96, 20)
        if transport == "codex-spare":
            return reading(10, 10)
        return None
    wf.provider_bands.job_drain = drains({"five_hour": 22, "seven_day": 3})
    return wf.decide(SYNTH_ALIAS, "max", "arm", data=data, read=read, now=NOW, load={}, plan=plan)


@case
def a_full_plan_with_a_roomy_sibling_does_not_fit_under_plan():
    code, text = sibling_run("codex")
    assert code == 10, (code, text)


@case
def a_full_plan_with_a_roomy_sibling_fits_route_wide():
    code, text = sibling_run(None)
    assert code == 0, (code, text)


def main():
    failed = 0
    original = wf.provider_bands.job_drain
    for fn in CASES:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
        finally:
            wf.provider_bands.job_drain = original
    print(f"{len(CASES) - failed}/{len(CASES)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
