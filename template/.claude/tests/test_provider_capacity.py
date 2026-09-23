#!/usr/bin/env python3
"""Proof for the cross-provider capacity runner and normalized cache contract."""
import importlib.util
import json
import os
import sys
import tempfile
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
                "resetsAt": 1_800_000_000, "windowDurationMins": 10080,
                "pressure": 0.5, "band": band,
            }],
            "dispatchBand": band,
            "routingBand": band,
            "bindingWindow": "seven_day",
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

    fallback = pc.reading("anthropic", data=data, now=now + 60, cache_path=str(cache),
                          runner=network_failure)
    cases.append(("network failure may use fresh normalized cache",
                  fallback.get("sourceKind") == "cache"
                  and fallback.get("liveFailure", {}).get("kind") == "network"))

    def auth_failure(_spec, _transport):
        out = network_failure(None, None)
        out["status"] = "auth-error"
        out["error"] = {"kind": "auth", "message": "re-login"}
        return out

    no_auth_fallback = pc.reading("anthropic", data=data, now=now + 60,
                                  cache_path=str(cache), runner=auth_failure)
    cases.append(("authentication failure never uses an available cache",
                  no_auth_fallback.get("status") == "auth-error"
                  and no_auth_fallback.get("sourceKind") == "live"))

    os.environ["PROVIDER_CAPACITY_ADVISORY"] = "1"
    advisory_calls = []
    advisory = pc.reading("anthropic", data=data, now=now + 60, cache_path=str(cache),
                          runner=lambda *_args: advisory_calls.append(True))
    os.environ.pop("PROVIDER_CAPACITY_ADVISORY", None)
    cases.append(("advisory startup may reuse a fresh normalized cache without a live call",
                  advisory.get("sourceKind") == "live" and advisory_calls == []))

    stale = pc.reading("anthropic", data=data, now=now + 601, cache_path=str(cache),
                       runner=network_failure)
    cases.append(("cache older than ten minutes cannot authorize capacity",
                  stale.get("status") == "network-error" and stale.get("sourceKind") == "live"))

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

    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
