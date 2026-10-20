#!/usr/bin/env python3
"""Proof that Codex rate-limit RPC results use the shared capacity schema, and that a successful
reading appends one plan-usage sample per window (HARNESS_USAGE_SAMPLES redirects the file)."""
import calendar
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "..", "scripts", "codex_quota_probe.py")
spec = importlib.util.spec_from_file_location("codex_quota_probe_test", PATH)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def run_main(argv, samples, reading=None, error=None):
    """probe.main with the binary and the RPC planted; returns (stdout, sample rows)."""
    def read_rate_limits(_binary):
        if error is not None:
            raise error
        return reading

    saved = (probe.find_codex, probe.read_rate_limits, os.environ.get("HARNESS_USAGE_SAMPLES"))
    probe.find_codex, probe.read_rate_limits = (lambda: "planted-codex"), read_rate_limits
    os.environ["HARNESS_USAGE_SAMPLES"] = samples
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            probe.main(argv)
    finally:
        probe.find_codex, probe.read_rate_limits = saved[0], saved[1]
        if saved[2] is None:
            os.environ.pop("HARNESS_USAGE_SAMPLES", None)
        else:
            os.environ["HARNESS_USAGE_SAMPLES"] = saved[2]
    rows = []
    if os.path.exists(samples):
        with open(samples, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    return out.getvalue(), rows


def sample_cases(raw):
    fresh = lambda: os.path.join(tempfile.mkdtemp(prefix="cqp_samples_"), "samples.jsonl")  # noqa: E731
    now = time.time()
    live = json.loads(json.dumps(raw))
    live["rateLimits"]["primary"]["resetsAt"] = now + 600
    live["rateLimits"]["secondary"]["resetsAt"] = now + 86400
    out, rows = run_main([], fresh(), reading=live)
    observed = json.loads(out)["observedAt"]
    ts = calendar.timegm(time.strptime(observed, "%Y-%m-%dT%H:%M:%SZ"))
    _raw_out, raw_rows = run_main(["--raw"], fresh(), reading=live)
    _fail_out, fail_rows = run_main([], fresh(), error=RuntimeError("account/rateLimits/read timed out"))
    return [
        ("a successful reading appends one sample per window",
         sorted(r.get("window") for r in rows) == ["five_hour", "seven_day"]
         and all(r.get("provider") == "codex" for r in rows)),
        ("each Codex sample carries observedAt as ts, and the window's duration and use",
         rows and all(r.get("ts") == ts for r in rows)
         and {r["window"]: (r.get("windowDurationMins"), r.get("used")) for r in rows}
         == {"five_hour": (300, 100.0), "seven_day": (10080, 50.0)}),
        ("--raw appends no sample", raw_rows == []),
        ("a failed reading appends no sample", fail_rows == []),
    ]


NOW = 1_800_000_000  # fixed clock: these cases must not read the wall clock


def valid(reading):
    """The shared validator's errors for a Codex reading."""
    path = os.path.join(HERE, "..", "tools", "provider_capacity.py")
    cap_spec = importlib.util.spec_from_file_location("provider_capacity_for_codex", path)
    capacity = importlib.util.module_from_spec(cap_spec)
    cap_spec.loader.exec_module(capacity)
    return capacity.validate_reading(reading, "codex", "plan-quota")


def headroom_cases():
    """A 99%-used weekly cap 22 hours before reset is Hot, though its raw pace is ~1.14.

    Run-out ratio: elapsed = 1 - 79200/604800 = 0.869, rate = 99/0.869 = 113.9 %-per-week,
    so the last 1% lasts 0.0088 week against 0.1310 week to reset -> 0.067.
    """
    credited = probe.summarize({
        "rateLimits": {
            "planType": "plus",
            "primary": {"usedPercent": 10.0, "windowDurationMins": 300,
                        "resetsAt": NOW + 4 * 3600},
            "secondary": {"usedPercent": 99.0, "windowDurationMins": 10080,
                          "resetsAt": NOW + 22 * 3600},
            "credits": {"hasCredits": True, "unlimited": False, "balance": "4.50"},
        }
    }, NOW)
    quota = credited.get("quota") or {}
    weekly = next((w for w in quota.get("windows") or [] if w.get("name") == "seven_day"), {})
    ratio = weekly.get("runoutRatio")
    near_expected = (isinstance(ratio, (int, float)) and not isinstance(ratio, bool)
                     and 0.065 <= ratio <= 0.070)

    partial = probe.summarize({
        "rateLimits": {
            "planType": "plus",
            "primary": {"usedPercent": 10.0, "windowDurationMins": 300, "resetsAt": None},
            "secondary": {"usedPercent": 99.0, "windowDurationMins": 10080,
                          "resetsAt": NOW + 22 * 3600},
            "credits": {"hasCredits": False, "unlimited": False, "balance": None},
        }
    }, NOW)

    single = probe.summarize({
        "rateLimits": {"primary": {"usedPercent": 10.0, "windowDurationMins": 300,
                                   "resetsAt": NOW + 3600}}
    }, NOW)
    single_rows = (single.get("quota") or {}).get("windows") or []
    invalid = probe.summarize({"rateLimits": {
        "primary": {"usedPercent": True, "windowDurationMins": 300,
                    "resetsAt": NOW + 3600},
        "secondary": {"usedPercent": 20.0, "windowDurationMins": 10080,
                      "resetsAt": NOW + 2 * 86400},
    }}, NOW)
    def limits(five, week):
        return probe.summarize({"rateLimits": {
            "primary": dict(five, windowDurationMins=300),
            "secondary": dict(week, windowDurationMins=10080),
        }}, NOW)

    dormant = {"usedPercent": 0.0, "resetsAt": None}
    beside = limits(dormant, {"usedPercent": 20.0, "resetsAt": NOW + 2 * 86400})
    beside_five = next((w for w in (beside.get("quota") or {}).get("windows") or []
                        if w.get("name") == "five_hour"), {})
    fresh = limits(dormant, dormant)
    fresh_quota = fresh.get("quota") or {}
    idle_week = limits({"usedPercent": 60.0, "resetsAt": NOW + 2 * 3600}, dormant)
    idle_week_quota = idle_week.get("quota") or {}
    idle_week_five = next((w for w in idle_week_quota.get("windows") or []
                           if w.get("name") == "five_hour"), {})
    return [
        ("a dormant window is a Surplus row at pressure 0 beside a live week",
         beside.get("status") == "available" and beside_five.get("band") == "Surplus"
         and beside_five.get("pressure") == 0.0 and beside_five.get("runoutRatio") is None
         and valid(beside) == []),
        ("a fresh allowance with every window dormant is available at Surplus, never malformed",
         fresh.get("status") == "available" and fresh_quota.get("dispatchBand") == "Surplus"
         and fresh_quota.get("routingBand") == "Surplus" and valid(fresh) == []),
        ("a dormant week routes on the live five-hour window",
         idle_week.get("status") == "available"
         and idle_week_quota.get("routingWindow") == "five_hour"
         and idle_week_quota.get("routingBand") == idle_week_five.get("band") is not None
         and valid(idle_week) == []),
        ("a positive credit balance keeps a 99%-weekly / 22h capture available",
         credited.get("status") == "available"
         and (quota.get("credits") or {}).get("available") is True),
        ("a 99%-used week 22h from reset routes Hot on the weekly risk band",
         quota.get("routingBand") == "Hot" and quota.get("routingWindow") == "seven_day"),
        ("the weekly row grades Hot and carries its projected run-out ratio (~0.067)",
         weekly.get("band") == "Hot" and near_expected),
        ("the headroom-Hot week is the dispatch and binding window",
         quota.get("dispatchBand") == "Hot" and quota.get("bindingWindow") == "seven_day"),
        ("a reported window without reset time fails closed despite a valid weekly sibling",
         partial.get("status") == "malformed" and partial.get("quota") is None),
        ("an absent second window leaves the reported cap standing alone",
         [w.get("name") for w in single_rows] == ["five_hour"]
         and single.get("status") == "available"),
        ("boolean use in a reported window is malformed despite a valid sibling",
         invalid.get("status") == "malformed" and invalid.get("quota") is None),
    ]


def main():
    now = 1_800_000_000
    raw = {
        "rateLimits": {
            "planType": "plus",
            "primary": {"usedPercent": 100.0, "windowDurationMins": 300, "resetsAt": now + 600},
            "secondary": {"usedPercent": 50.0, "windowDurationMins": 10080, "resetsAt": now + 86400},
            "credits": {"hasCredits": False, "unlimited": False, "balance": None},
        }
    }
    exhausted = probe.summarize(raw, now)
    raw["rateLimits"]["credits"] = {"hasCredits": True, "unlimited": False, "balance": "4.50"}
    credited = probe.summarize(raw, now)
    malformed = probe.summarize({"rateLimits": {}}, now)
    timeout = probe.failure(RuntimeError("account/rateLimits/read timed out"))
    auth = probe.failure(RuntimeError("RPC error: 401 unauthorized"))
    limited = probe.failure(RuntimeError("RPC error: 429 Too Many Requests"))
    cases = [
        ("shared schema version", exhausted.get("schemaVersion") == 1),
        ("transport and cost model", exhausted.get("transport") == "codex"
         and exhausted.get("costModel") == "plan-quota"),
        ("live source and expected outcome", exhausted.get("sourceKind") == "live"
         and exhausted.get("status") == "exhausted"),
        ("spent short window binds dispatch", exhausted.get("quota", {}).get("bindingWindow") == "five_hour"
         and exhausted.get("quota", {}).get("dispatchBand") == "Exhausted"),
        ("seven-day window owns routing band", exhausted.get("quota", {}).get("routingBand")
         == exhausted.get("quota", {}).get("windows", [None, {}])[1].get("band")),
        ("credits remain a typed second axis", credited.get("status") == "available"
         and credited.get("quota", {}).get("credits", {}).get("available") is True),
        ("successful result has no error payload", exhausted.get("error") is None
         and exhausted.get("liveFailure") is None),
        ("missing windows are a typed malformed outcome", malformed.get("status") == "malformed"),
        ("RPC timeout is a typed network outcome", timeout.get("status") == "network-error"),
        ("RPC 401 is a typed authentication outcome", auth.get("status") == "auth-error"),
        ("RPC 429 is a typed rate-limit outcome", limited.get("status") == "network-error"
         and limited.get("error", {}).get("kind") == "rate-limit"),
    ]
    cases.extend(sample_cases(raw))
    cases.extend(headroom_cases())
    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
