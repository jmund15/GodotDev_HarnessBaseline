#!/usr/bin/env python3
"""Proof that Codex rate-limit RPC results use the shared capacity schema."""
import importlib.util
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "..", "scripts", "codex_quota_probe.py")
spec = importlib.util.spec_from_file_location("codex_quota_probe_test", PATH)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


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
    ]
    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
