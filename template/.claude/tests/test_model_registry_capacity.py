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

    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
