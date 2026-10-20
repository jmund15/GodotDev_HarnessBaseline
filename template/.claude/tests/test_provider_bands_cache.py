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
            "bindingWindow": "seven_day", "routingWindow": "seven_day",
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
        assert hasattr(pb.provider_capacity, "reading"), "provider_capacity.reading is the patched seam"
        pb.provider_capacity.reading = fake
        got = pb.reading("codex", ttl=999, data={"marker": True})
        cases.append(("provider capacity is the sole reader", calls == [("codex", {"marker": True})]))
        cases.append(("routing band and pressure are projected",
                      got.get("band") == "On pace" and got.get("pressure") == 1.0))
        cases.append(("source and observation stay visible",
                      got.get("sourceKind") == "live" and got.get("observedAt")))

        def go_reading(*_args, **_kwargs):
            result = capacity(routing="Hot")
            result["quota"].update(bindingWindow="thirty_day", routingWindow="thirty_day", windows=[
                {"name": "seven_day", "band": "Surplus", "pressure": 0.1},
                {"name": "thirty_day", "band": "Hot", "pressure": 1.9},
            ])
            return result

        pb.provider_capacity.reading = go_reading
        cases.append(("Go monthly binding pressure outranks low weekly pressure",
                      pb.reading("go").get("pressure") == 1.9))

        def headroom_reading(*_args, **_kwargs):
            result = capacity(routing="Hot")
            result["quota"].update(bindingWindow="five_hour", routingWindow="seven_day", windows=[
                {"name": "five_hour", "band": "Ahead", "pressure": 1.4, "runoutRatio": 0.3},
                {"name": "seven_day", "band": "Hot", "pressure": 1.14, "runoutRatio": 0.067},
            ])
            return result

        pb.provider_capacity.reading = headroom_reading
        selected = pb.reading("anthropic") or {}
        cases.append(("display pressure keeps binding while routing compares weekly headroom",
                      selected.get("pressure") == 1.4
                      and selected.get("routingPressure") == 1.14
                      and selected.get("runoutRatio") == 0.067))

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
