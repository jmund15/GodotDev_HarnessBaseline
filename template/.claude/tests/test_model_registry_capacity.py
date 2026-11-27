#!/usr/bin/env python3
"""Proof for external-model registry capacity-source invariants."""
import copy
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "..", "tools", "model_registry.py")
spec = importlib.util.spec_from_file_location("model_registry_capacity_test", PATH)
mr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr)


def accepted(doc):
    try:
        mr._validate(doc, "fixture")
        return True
    except Exception:
        return False


def main():
    live = mr.load()
    cases = [("shipped registry validates", accepted(copy.deepcopy(live)))]

    missing = copy.deepcopy(live)
    missing["transports"]["codex"].pop("capacityProbe", None)
    cases.append(("a transport without a capacity declaration is rejected", not accepted(missing)))

    both = copy.deepcopy(live)
    both["transports"]["codex"]["capacityUnsupported"] = {"reason": "x"}
    cases.append(("two capacity declarations are rejected", not accepted(both)))

    wrong_kind = copy.deepcopy(live)
    wrong_kind["transports"]["codex"]["capacityProbe"]["kind"] = "balance"
    cases.append(("plan quota requires a quota-capable probe", not accepted(wrong_kind)))

    bad_timeout = copy.deepcopy(live)
    bad_timeout["transports"]["deepseek"]["capacityProbe"]["timeoutSeconds"] = 60
    cases.append(("probe timeout must fit dispatch preflight", not accepted(bad_timeout)))

    empty_reason = copy.deepcopy(live)
    empty_reason["transports"]["opencode"]["capacityUnsupported"] = {"reason": ""}
    cases.append(("unsupported amount needs a reason", not accepted(empty_reason)))

    cases.append(("zero-floor credential-gated OpenCode stays valid", accepted(copy.deepcopy(live))))
    positive_floor = copy.deepcopy(live)
    muse = next(row for row in positive_floor["models"] if row.get("alias") == "muse")
    muse["gate"]["minBalanceUSD"] = 1.0
    cases.append(("unsupported amount cannot satisfy a positive floor", not accepted(positive_floor)))

    # `free`: a route that spends neither plan quota nor dollars (a local GPU, a free cloud tier).
    def free_row(doc):
        return next(row for row in doc["models"] if row.get("alias") == "inkling-local")
    cases.append(("local, opencode and opencode-cli are registered free", all(
        live["transports"][name]["costModel"] == "free" for name in ("local", "opencode", "opencode-cli"))))
    cases.append(("no row on a free route declares a host-band floor", not [
        row["id"] for row in live["models"]
        if live["transports"][row["transport"]]["costModel"] == "free" and "minBand" in row["gate"]]))
    cases.append(("the local route caps itself at its one GPU slot", (mr.child_cap("local", live) or [None])[0] == 1))
    priced = copy.deepcopy(live)
    free_row(priced)["price"]["outputPer1M"] = 0.4
    cases.append(("a row with a price is refused on a free route", not accepted(priced)))
    floored = copy.deepcopy(live)
    free_row(floored)["gate"] = {"minBand": "Surplus"}
    cases.append(("a host-band floor is refused on a free route", not accepted(floored)))
    metered = copy.deepcopy(live)
    metered["transports"]["local"].pop("capacityUnsupported")
    metered["transports"]["local"]["capacityProbe"] = dict(live["transports"]["codex"]["capacityProbe"], kind="balance")
    cases.append(("a free route with an amount probe is refused", not accepted(metered)))
    unfloored = copy.deepcopy(live)
    next(row for row in unfloored["models"] if row.get("alias") == "sonnet")["gate"].pop("minBand")
    cases.append(("a row on any other route still needs its floor", not accepted(unfloored)))

    import contextlib
    import io
    facts = mr.launch_facts("inkling-local")
    cases.append(("the launcher facts carry a free row's cost model and an empty floor",
                  facts.get("SC_MIN_BAND") == "" and facts.get("SC_COST_MODEL") == "free"))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        mr.main(["available"])
    lines = out.getvalue().splitlines()
    facts = lines[next(i for i, ln in enumerate(lines) if ln.split()[:1] == ["inkling-local"]) + 1]
    cases.append(("`available` prints `free` and no dollar rate for a free row", "; free" in facts and "/1M in" not in facts))

    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
