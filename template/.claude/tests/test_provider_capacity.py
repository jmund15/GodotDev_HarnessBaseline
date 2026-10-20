#!/usr/bin/env python3
"""Proof for the cross-provider capacity runner and normalized cache contract."""
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
TOOL = HERE.parent / "tools" / "provider_capacity.py"


def load_tool():
    if not TOOL.is_file():
        return None
    spec = importlib.util.spec_from_file_location("provider_capacity_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def quota(status="available", source="live", band="On pace"):
    return {
        "schemaVersion": 1,
        "transport": "anthropic",
        "costModel": "plan-quota",
        "sourceKind": source,
        "observedAt": "2027-01-15T08:00:00Z",
        "status": status,
        "error": None,
        "liveFailure": None,
        "quota": {
            "windows": [{
                "name": "seven_day", "usedPercent": 50.0,
                "resetsAt": 1_800_000_000 + 3 * 86400, "windowDurationMins": 10080,
                "pressure": 0.5, "band": band, "runoutRatio": 0.5,
            }],
            "dispatchBand": band,
            "routingBand": band,
            "bindingWindow": "seven_day",
            "routingWindow": "seven_day",
            "credits": {"enabled": False, "available": False, "balance": None},
        },
        "balance": None,
    }


def balance(amount=5.0):
    return {
        "schemaVersion": 1,
        "transport": "deepseek",
        "costModel": "marginal-usd",
        "sourceKind": "live",
        "observedAt": "2027-01-15T08:00:00Z",
        "status": "available" if amount > 0 else "insufficient",
        "error": None,
        "liveFailure": None,
        "quota": None,
        "balance": {"amount": amount, "currency": "USD"},
    }


def main():
    pc = load_tool()
    if pc is None:
        print("FAIL provider_capacity.py exists")
        return 1

    root = Path(tempfile.mkdtemp(prefix="provider_capacity_"))
    cache = root / "capacity.json"
    now = 1_800_000_000.0
    data = {
        "transports": {
            "anthropic": {
                "costModel": "plan-quota",
                "capacityProbe": {"command": "python", "args": ["anthropic.py"], "kind": "quota"},
            },
            "deepseek": {
                "costModel": "marginal-usd",
                "capacityProbe": {"command": "python", "args": ["deepseek.py"], "kind": "balance"},
            },
            "opencode": {
                "costModel": "marginal-usd",
                "capacityUnsupported": {"reason": "No provider amount endpoint."},
            },
        },
        "models": [
            {"transport": "anthropic", "gate": {"minBand": "Surplus"}},
            {"transport": "deepseek", "gate": {"minBalanceUSD": 1.0}},
            {"transport": "opencode", "gate": {"minBalanceUSD": 0.0}},
        ],
    }

    live_calls = []

    def live(_spec, transport):
        live_calls.append(transport)
        return quota() if transport == "anthropic" else balance()

    cases = []
    got = pc.reading("anthropic", data=data, now=now, cache_path=str(cache), runner=live)
    cases.append(("live quota is normalized and returned", got.get("status") == "available"))
    cases.append(("the live adapter is called before cache", live_calls == ["anthropic"]))
    cases.append(("successful live readings write the normalized cache", cache.is_file()))

    cached_blob = json.loads(cache.read_text(encoding="utf-8"))
    cases.append(("the cache contains no credential-shaped keys",
                  not any(k.lower().endswith("token") or "credential" in k.lower()
                          for k in json.dumps(cached_blob).split('"'))))

    def network_failure(_spec, _transport):
        return {
            "schemaVersion": 1, "transport": "anthropic", "costModel": "plan-quota",
            "sourceKind": "live", "observedAt": "2027-01-15T08:01:00Z",
            "status": "network-error", "error": {"kind": "network", "message": "offline"},
            "liveFailure": None, "quota": None, "balance": None,
        }

    fallback = pc.reading("anthropic", data=data, now=now + 700, cache_path=str(cache),
                          runner=network_failure)
    cases.append(("network failure may use fresh normalized cache",
                  fallback.get("sourceKind") == "cache"
                  and fallback.get("liveFailure", {}).get("kind") == "network"))

    def auth_failure(_spec, _transport):
        out = network_failure(None, None)
        out["status"] = "auth-error"
        out["error"] = {"kind": "auth", "message": "re-login"}
        return out

    no_auth_fallback = pc.reading("anthropic", data=data, now=now + 700,
                                  cache_path=str(cache), runner=auth_failure)
    cases.append(("authentication failure never uses an available cache",
                  no_auth_fallback.get("status") == "auth-error"
                  and no_auth_fallback.get("sourceKind") == "live"))

    def iso(epoch):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))

    def aged(seconds):
        entry = quota()
        entry["observedAt"] = iso(now - seconds)
        return entry

    def call_counting(reading):
        calls = []

        def runner(spec, transport):
            calls.append(transport)
            return reading(spec, transport)

        return calls, runner

    fresh_100 = root / "fresh_100.json"
    fresh_100.write_text(json.dumps({"anthropic": aged(100)}), encoding="utf-8")
    calls_100, runner_100 = call_counting(live)
    got_100 = pc.reading("anthropic", data=data, now=now, cache_path=str(fresh_100), runner=runner_100)
    cases.append(("a 100 s old reading is returned as stored with no runner call",
                  calls_100 == [] and got_100.get("sourceKind") == "live"
                  and got_100.get("observedAt") == iso(now - 100)))

    old_700 = root / "old_700.json"
    old_700.write_text(json.dumps({"anthropic": aged(700)}), encoding="utf-8")
    calls_700, runner_700 = call_counting(live)
    pc.reading("anthropic", data=data, now=now, cache_path=str(old_700), runner=runner_700)
    cases.append(("a 700 s old reading triggers exactly one runner call", len(calls_700) == 1))

    reset_past = aged(100)
    reset_past["quota"]["windows"][0]["name"] = "five_hour"
    reset_past["quota"]["windows"][0]["resetsAt"] = now - 10
    reset_past["quota"]["bindingWindow"] = reset_past["quota"]["routingWindow"] = "five_hour"
    reset_cache = root / "reset_past.json"
    reset_cache.write_text(json.dumps({"anthropic": reset_past}), encoding="utf-8")
    reset_hit = pc.cached("anthropic", data=data, now=now, cache_path=str(reset_cache))
    calls_reset, runner_reset = call_counting(live)
    pc.reading("anthropic", data=data, now=now, cache_path=str(reset_cache), runner=runner_reset)
    cases.append(("a 100 s old reading whose five-hour reset has passed triggers a runner call",
                  len(calls_reset) == 1 and reset_hit is None))

    import threading
    race_cache = root / "race.json"
    race_calls = []

    def slow_runner(_spec, transport):
        race_calls.append(transport)
        time.sleep(1.0)
        return quota()

    race_out = []
    racers = [threading.Thread(target=lambda: race_out.append(
        pc.reading("anthropic", data=data, now=now, cache_path=str(race_cache), runner=slow_runner)))
        for _ in range(2)]
    for thread in racers:
        thread.start()
    for thread in racers:
        thread.join()
    cases.append(("two concurrent reads on a cold cache make one runner call and agree",
                  len(race_calls) == 1 and len(race_out) == 2
                  and race_out[0] == race_out[1]))

    lock_path = getattr(pc, "_live_lock_path", lambda *_a: None)("anthropic", str(root / "held.json"))
    held_cache = root / "held.json"
    held_cache.write_text(json.dumps({"anthropic": aged(700)}), encoding="utf-8")
    spec_hooks = importlib.util.spec_from_file_location(
        "held_file_lock", HERE.parent / "hooks" / "_file_lock.py")
    file_lock = importlib.util.module_from_spec(spec_hooks)
    spec_hooks.loader.exec_module(file_lock)
    handle = file_lock.acquire(lock_path, 1) if lock_path else None
    held_calls, held_runner = call_counting(live)
    real_budget = getattr(pc, "LIVE_BUDGET_SECONDS")
    pc.LIVE_BUDGET_SECONDS = 0.3
    try:
        held = pc.reading("anthropic", data=data, now=now, cache_path=str(held_cache), runner=held_runner)
    finally:
        pc.LIVE_BUDGET_SECONDS = real_budget
        file_lock.release(handle)
    cases.append(("a caller that cannot take the lock in time gets the stale fallback, no runner call",
                  handle is not None and held_calls == []
                  and held.get("sourceKind") == "cache"
                  and (held.get("liveFailure") or {}).get("kind") == "network"))

    from types import SimpleNamespace
    from unittest.mock import patch
    wait_cache = root / "waited.json"
    wait_cache.write_text(json.dumps({"anthropic": aged(700)}), encoding="utf-8")
    wait_lock = pc._live_lock_path("anthropic", str(wait_cache))
    wait_handle = file_lock.acquire(wait_lock, 1)
    assert wait_handle is not None

    def acquire_after_contention(path, timeout):
        nonlocal wait_handle
        handle = file_lock.acquire(path, timeout)
        if timeout == 0 and handle is None and wait_handle is not None:
            file_lock.release(wait_handle)
            wait_handle = None
        return handle

    def slow_lock_module():
        time.sleep(0.5)
        return SimpleNamespace(acquire=acquire_after_contention, release=file_lock.release)

    waited_calls, waited_runner = call_counting(live)
    pc.LIVE_BUDGET_SECONDS = 2.0
    started = time.monotonic()
    try:
        with patch.object(pc, "_load_file_lock", slow_lock_module):
            waited = pc.reading("anthropic", data=data, now=now, cache_path=str(wait_cache), runner=waited_runner)
    finally:
        if wait_handle is not None:
            file_lock.release(wait_handle)
        pc.LIVE_BUDGET_SECONDS = real_budget
    cases.append(("a caller that waited for the lock never probes: fallback, no runner call, within one budget",
                  waited_calls == [] and waited.get("sourceKind") == "cache"
                  and (waited.get("liveFailure") or {}).get("kind") == "network"
                  and "another" in (waited.get("liveFailure") or {}).get("message", "")
                  and time.monotonic() - started < 2.0))

    stale = pc.reading("anthropic", data=data, now=now + pc.STALE_FALLBACK_SECONDS + 1,
                       cache_path=str(cache), runner=network_failure)
    cases.append(("a reading older than TTL + cooldown cannot authorize capacity",
                  stale.get("status") == "network-error" and stale.get("sourceKind") == "live"))

    # A rate-limited usage endpoint (HTTP 429) answers 429 for minutes, so a second probe during
    # that window only extends it: the transport is put in cooldown and the reading falls back to a
    # cache entry older than the fresh-cache TTL, up to CACHE_TTL_SECONDS + RATE_LIMIT_COOLDOWN_SECONDS.
    def rate_limited(_spec, _transport):
        return {
            "schemaVersion": 1, "transport": "anthropic", "costModel": "plan-quota",
            "sourceKind": "live", "observedAt": iso(now), "status": "network-error",
            "error": {"kind": "rate-limit",
                      "message": "Anthropic usage endpoint returned HTTP 429 (rate limited)"},
            "liveFailure": None, "quota": None, "balance": None,
        }

    cases.append(("a cache fallback may carry a rate-limit liveFailure",
                  pc.validate_reading(dict(aged(700), sourceKind="cache", liveFailure={
                      "kind": "rate-limit", "message": "rate limited"}),
                      "anthropic", "plan-quota") == []))
    cases.append(("the stale fallback window is the cache TTL plus the cooldown",
                  pc.STALE_FALLBACK_SECONDS == pc.CACHE_TTL_SECONDS + pc.RATE_LIMIT_COOLDOWN_SECONDS))

    retry_calls, retrying_runner = call_counting(network_failure)
    rate_calls, rate_runner = call_counting(rate_limited)
    real_sleep = pc.RETRY_SLEEP_SECONDS
    pc.RETRY_SLEEP_SECONDS = 0.0
    try:
        retried = pc.live_reading({"timeoutSeconds": 1}, "anthropic", {}, runner=retrying_runner)
        limited = pc.live_reading({"timeoutSeconds": 1}, "anthropic", {}, runner=rate_runner)
    finally:
        pc.RETRY_SLEEP_SECONDS = real_sleep
    cases.append(("a rate-limited live read answers at once while a network failure still retries",
                  (retried.get("error") or {}).get("kind") == "network"
                  and len(retry_calls) == pc.LIVE_TRIES
                  and (limited.get("error") or {}).get("kind") == "rate-limit"
                  and len(rate_calls) == 1))

    rl_cache = root / "rate_limited.json"
    rl_cache.write_text(json.dumps({"anthropic": aged(700)}), encoding="utf-8")
    rl_calls, rl_runner = call_counting(rate_limited)
    limited_reading = pc.reading("anthropic", data=data, now=now, cache_path=str(rl_cache),
                                 runner=rl_runner)
    rl_blob = json.loads(rl_cache.read_text(encoding="utf-8"))
    cases.append(("a rate-limited read probes once, falls back and records the cooldown",
                  len(rl_calls) == 1
                  and limited_reading.get("sourceKind") == "cache"
                  and (limited_reading.get("liveFailure") or {}).get("kind") == "rate-limit"
                  and (rl_blob.get("_rateLimitedUntil") or {}).get("anthropic")
                  == now + pc.RATE_LIMIT_COOLDOWN_SECONDS))
    cases.append(("a rate-limited read's fallback names the reading's age",
                  "700" in ((limited_reading.get("liveFailure") or {}).get("message") or "")))

    cooldown_calls, cooldown_runner = call_counting(rate_limited)
    during = pc.reading("anthropic", data=data, now=now + 60, cache_path=str(rl_cache),
                        runner=cooldown_runner)
    cases.append(("a cooldown never starts another probe",
                  cooldown_calls == [] and during.get("sourceKind") == "cache"
                  and (during.get("liveFailure") or {}).get("kind") == "rate-limit"))

    # The prober stores its result before it releases the single-reader lock: a waiter that takes
    # the lock re-reads the cache (or the cooldown) and never probes again. A slow store widens the
    # window between release and write so an out-of-lock store fails every run, not sometimes.
    def racing_pair(tag, runner_reading, cache_name):
        calls, runner = call_counting(runner_reading)

        def slow_runner(spec, transport):
            time.sleep(0.3)
            return runner(spec, transport)

        out = []
        threads = [threading.Thread(target=lambda: out.append(pc.reading(
            "anthropic", data=data, now=now, cache_path=str(root / cache_name), runner=slow_runner)))
            for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return calls, out

    real_write, real_record = pc._write_cache, pc._record_rate_limit
    pc._write_cache = lambda *a: (time.sleep(0.5), real_write(*a))[1]
    pc._record_rate_limit = lambda *a: (time.sleep(0.5), real_record(*a))[1]
    try:
        stored_calls, stored_out = racing_pair("store", live, "race_store.json")
        limit_calls, limit_out = racing_pair("limit", rate_limited, "race_limit.json")
    finally:
        pc._write_cache, pc._record_rate_limit = real_write, real_record
    cases.append(("a waiter reads the cache the prober stored under the lock, no second probe",
                  len(stored_calls) == 1 and len(stored_out) == 2))
    cases.append(("a waiter honors the cooldown the prober recorded under the lock, no second probe",
                  len(limit_calls) == 1 and len(limit_out) == 2))

    no_cached = root / "cooldown_uncached.json"
    no_cached.write_text(json.dumps({"_rateLimitedUntil": {"anthropic": now + 300}}), encoding="utf-8")
    uncached_calls, uncached_runner = call_counting(rate_limited)
    uncached = pc.reading("anthropic", data=data, now=now, cache_path=str(no_cached),
                          runner=uncached_runner)
    cases.append(("a cooldown with no usable reading stays a typed refusal naming its end",
                  uncached_calls == [] and uncached.get("status") == "network-error"
                  and uncached.get("sourceKind") == "live"
                  and (uncached.get("error") or {}).get("kind") == "rate-limit"
                  and iso(now + 300) in ((uncached.get("error") or {}).get("message") or "")))

    stale_cache = root / "stale_fallback.json"
    stale_cache.write_text(json.dumps({"anthropic": aged(700)}), encoding="utf-8")
    fallback_700 = pc.reading("anthropic", data=data, now=now, cache_path=str(stale_cache),
                              runner=network_failure)
    cases.append(("a plain network failure reaches the stale fallback window",
                  fallback_700.get("sourceKind") == "cache"
                  and (fallback_700.get("liveFailure") or {}).get("kind") == "network"
                  and "700" in ((fallback_700.get("liveFailure") or {}).get("message") or "")))

    # The dispatch path (provider_bands.read_capacity) takes no runner and reads the clock itself,
    # so this case plants a wall-clock cooldown; its probe spec is a stub so that a regression
    # cannot reach the network.
    sys.path.insert(0, str(TOOL.parent))
    import provider_bands

    band_data = json.loads(json.dumps(data))
    band_data["transports"]["anthropic"]["capacityProbe"] = {
        "command": sys.executable,
        "args": ["-c", "print(%r)" % json.dumps(rate_limited(None, None))],
        "timeoutSeconds": 1,
    }
    wall = time.time()
    band_entry = quota()
    band_entry["observedAt"] = iso(wall - 700)
    band_cache = root / "band_cooldown.json"
    band_cache.write_text(json.dumps({"_rateLimitedUntil": {"anthropic": wall + 300},
                                      "anthropic": band_entry}), encoding="utf-8")
    os.environ["PROVIDER_CAPACITY_CACHE"] = str(band_cache)
    try:
        band_reading = provider_bands.read_capacity("anthropic", data=band_data, live=True)
    finally:
        os.environ.pop("PROVIDER_CAPACITY_CACHE", None)
    cases.append(("the dispatch path serves the fallback instead of probing during a cooldown",
                  band_reading is not None and band_reading.get("sourceKind") == "cache"
                  and (band_reading.get("liveFailure") or {}).get("kind") == "rate-limit"))

    fresh_cache = root / "fresh_only.json"
    fresh_cache.write_text(json.dumps({"anthropic": aged(700)}), encoding="utf-8")
    cases.append(("a fresh-cache read still rejects a 700 s old entry",
                  pc._read_cache(str(fresh_cache), "anthropic", "plan-quota", now) is None
                  and pc.cached("anthropic", data=data, now=now, cache_path=str(fresh_cache)) is None))

    cleared = root / "cleared.json"
    cleared.write_text(json.dumps({"_rateLimitedUntil": {"anthropic": now - 1},
                                   "anthropic": aged(700)}), encoding="utf-8")
    recovered = pc.reading("anthropic", data=data, now=now, cache_path=str(cleared), runner=live)
    cleared_blob = json.loads(cleared.read_text(encoding="utf-8"))
    cases.append(("a successful live read clears the transport's cooldown",
                  recovered.get("status") == "available"
                  and "anthropic" not in (cleared_blob.get("_rateLimitedUntil") or {})))

    unsupported = pc.reading("opencode", data=data, now=now, cache_path=str(cache), runner=live)
    cases.append(("explicit unsupported amount source stays distinct",
                  unsupported.get("status") == "unsupported"
                  and unsupported.get("sourceKind") == "unsupported"))

    cases.append(("valid balance envelope passes validation",
                  pc.validate_reading(balance(), "deepseek", "marginal-usd") == []))
    bad = balance()
    bad["quota"] = quota()["quota"]
    cases.append(("quota and balance together are rejected",
                  bool(pc.validate_reading(bad, "deepseek", "marginal-usd"))))
    bad_status = quota(status="mystery")
    cases.append(("unknown status is rejected",
                  bool(pc.validate_reading(bad_status, "anthropic", "plan-quota"))))

    cases.append(("a routing window naming a reported row validates",
                  pc.validate_reading(quota(), "anthropic", "plan-quota") == []))
    forged = quota()
    forged["quota"]["routingWindow"] = "monthly"
    cases.append(("a routing window naming no reported row is rejected",
                  bool(pc.validate_reading(forged, "anthropic", "plan-quota"))))
    blank = quota()
    blank["quota"]["routingWindow"] = ""
    cases.append(("a blank routing window is rejected",
                  bool(pc.validate_reading(blank, "anthropic", "plan-quota"))))
    mismatched = quota()
    mismatched["quota"]["windows"][0]["band"] = "Hot"
    cases.append(("an available routing band disagreeing with its named row is rejected",
                  bool(pc.validate_reading(mismatched, "anthropic", "plan-quota"))))
    spent = quota(status="exhausted", band="Exhausted")
    spent["quota"]["windows"][0]["band"] = "Hot"
    cases.append(("Exhausted overrides availability, so a spent capture is no band mismatch",
                  pc.validate_reading(spent, "anthropic", "plan-quota") == []))

    dormant_row = {"name": "five_hour", "usedPercent": 0.0, "resetsAt": None,
                   "windowDurationMins": 300, "pressure": 0.0, "band": "Surplus",
                   "runoutRatio": None}
    dormant = quota()
    dormant["quota"]["windows"].insert(0, dict(dormant_row))
    cases.append(("a dormant row is Surplus at pressure 0 beside a valid routing window",
                  pc.validate_reading(dormant, "anthropic", "plan-quota") == []))
    bandless = quota()
    bandless["quota"]["windows"].insert(0, dict(dormant_row, pressure=None, band=None))
    cases.append(("a bandless row is rejected even at zero use",
                  bool(pc.validate_reading(bandless, "anthropic", "plan-quota"))))
    invalid_active = quota()
    invalid_active["quota"]["windows"].insert(0, dict(dormant_row, usedPercent=1.0,
                                                      pressure=None, band=None))
    cases.append(("positive use cannot hide an uncomputable row behind a valid week",
                  bool(pc.validate_reading(invalid_active, "anthropic", "plan-quota"))))
    banded_no_reset = quota()
    banded_no_reset["quota"]["windows"].insert(0, dict(dormant_row, usedPercent=1.0, pressure=0.1))
    cases.append(("a banded row with use but no reset is rejected",
                  bool(pc.validate_reading(banded_no_reset, "anthropic", "plan-quota"))))
    dormant_only = quota(band="Surplus")
    dormant_only["quota"]["windows"] = [dict(dormant_row)]
    dormant_only["quota"]["routingWindow"] = "five_hour"
    dormant_only["quota"]["bindingWindow"] = "five_hour"
    cases.append(("a dormant-only capture is an available Surplus reading",
                  pc.validate_reading(dormant_only, "anthropic", "plan-quota") == []))

    # The cache is one file with one entry per transport, written by concurrent probes (a fan-out,
    # a gate preflight, two sessions). The write therefore goes through hooks/_hook_state.py's
    # locked read-modify-write, and a lock that cannot be taken writes NOTHING: a fallback write
    # would drop an entry a sibling probe saved in between.
    lock_cache = root / "locked.json"
    lock_cache.write_text(json.dumps({"deepseek": balance()}), encoding="utf-8")
    cases.append(("a cache write keeps the entry another transport already stored",
                  pc._write_cache(str(lock_cache), "anthropic", quota()) is True
                  and sorted(json.loads(lock_cache.read_text(encoding="utf-8"))) ==
                  ["anthropic", "deepseek"]))
    before = lock_cache.read_text(encoding="utf-8")
    real_loader = pc._load_hook_state
    pc._load_hook_state = lambda: None
    try:
        wrote = pc._write_cache(str(lock_cache), "opencode", quota())
    finally:
        pc._load_hook_state = real_loader
    cases.append(("a cache write with no lock owner writes nothing",
                  wrote is False and lock_cache.read_text(encoding="utf-8") == before))

    # A probe that may refresh a token first gets the refresh turn's time on top of the usage
    # read's, never past the registry's 35 s probe cap.
    timeout_of = getattr(pc, "probe_timeout", None)
    cases.append(("a probe with a refresh pin gets its refresh time on top, capped at 35 s",
                  timeout_of is not None
                  and timeout_of({"timeoutSeconds": 20}) == 20
                  and timeout_of({"timeoutSeconds": 20, "refresh": {"timeoutSeconds": 10}}) == 30
                  and timeout_of({"timeoutSeconds": 20, "refresh": {"timeoutSeconds": 15}}) == 35
                  and timeout_of({"timeoutSeconds": 30, "refresh": {"timeoutSeconds": 15}}) == 35))

    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
