#!/usr/bin/env python3
"""Proof that provider_bands is a thin view over live-first provider_capacity."""
import importlib.util
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "..", "tools", "provider_bands.py")
spec = importlib.util.spec_from_file_location("pb", MOD)
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)


def capacity(status="available", source="live", routing="On pace"):
    return {
        "status": status,
        "sourceKind": source,
        "observedAt": "2027-01-15T08:00:00Z",
        "quota": {
            "routingBand": routing,
            "dispatchBand": "Exhausted" if status == "exhausted" else routing,
            "bindingWindow": "seven_day",
            "windows": [{"name": "seven_day", "band": routing, "pressure": 1.0}],
        },
        "planType": "test",
    }


def main():
    calls = []
    original = pb.provider_capacity.reading

    def fake(transport, data=None):
        calls.append((transport, data))
        return capacity()

    cases = []
    try:
        pb.provider_capacity.reading = fake
        got = pb.reading("codex", ttl=999, data={"marker": True})
        cases.append(("provider capacity is the sole reader", calls == [("codex", {"marker": True})]))
        cases.append(("routing band and pressure are projected",
                      got.get("band") == "On pace" and got.get("pressure") == 1.0))
        cases.append(("source and observation stay visible",
                      got.get("sourceKind") == "live" and got.get("observedAt")))

        pb.provider_capacity.reading = lambda *_args, **_kwargs: capacity("exhausted", routing="Hot")
        cases.append(("exhaustion is never reduced to a burn-rate band",
                      pb.reading("codex").get("band") == "Exhausted"))

        pb.provider_capacity.reading = lambda *_args, **_kwargs: {
            "status": "unsupported", "sourceKind": "unsupported", "quota": None,
        }
        cases.append(("a transport without quota has no provider band",
                      pb.reading("opencode") is None))

        def broken(*_args, **_kwargs):
            raise RuntimeError("probe failed")
        pb.provider_capacity.reading = broken
        cases.append(("runner failure is unknown, never a permissive band",
                      pb.reading("codex") is None))
    finally:
        pb.provider_capacity.reading = original

    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
