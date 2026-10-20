#!/usr/bin/env python3
"""Proof for `tools/quota_dashboard.py`: one owner-facing view over the live quota and model owners.

Every case plants the registry (a dict, never the live registry) and the capacity readings (a reader
function), so no case depends on this machine's quota. The dashboard stores nothing: each value
must come from a planted owner, which is what the cases assert.

    python3 .claude/tests/test_quota_dashboard.py
"""
import importlib.util
import os
import sys

from _display_words import banned_in

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "quota_dashboard.py")
NOW = 1_800_000_000.0
WEEK_MIN = 7 * 24 * 60


def plan_reading(transport, used, left, plan_type, band="Surplus", status="available"):
    win = {"name": "seven_day", "usedPercent": used, "resetsAt": NOW + left * WEEK_MIN * 60,
           "windowDurationMins": WEEK_MIN, "pressure": 0.5, "band": band}
    return {"schemaVersion": 1, "transport": transport, "costModel": "plan-quota", "status": status,
            "planType": plan_type,
            "quota": {"windows": [win], "routingWindow": "seven_day", "bindingWindow": "seven_day",
                      "routingBand": band, "dispatchBand": band}}


REGISTRY = {
    "transports": {
        "anthropic": {"costModel": "plan-quota", "status": {"state": "available", "since": "2026-09-04"}},
        "codex": {"costModel": "plan-quota"},
        "deepseek": {"costModel": "marginal-usd", "status": {"state": "available", "since": "2026-09-15"}},
        "opencode": {"costModel": "marginal-usd",
                     "status": {"state": "unavailable", "since": "2026-09-27", "reason": "free tier blocked"}},
    },
    "models": [
        {"transport": "anthropic", "id": "claude-a", "alias": "a-exec", "roles": ["executor", "architect"],
         "lifecycle": "current"},
        {"transport": "codex", "id": "codex-c", "alias": "c-fan", "roles": ["fanout"], "lifecycle": "current"},
        {"transport": "deepseek", "id": "ds-f", "alias": "d-exec", "roles": ["executor"], "lifecycle": "current"},
        {"transport": "codex", "id": "codex-old", "alias": "old", "roles": [], "lifecycle": "superseded"},
    ],
}
READINGS = {
    "anthropic": plan_reading("anthropic", 61, 0.5, "max_20x", band="Ahead"),
    "codex": plan_reading("codex", 40, 0.2, "prolite"),
    "deepseek": {"costModel": "marginal-usd", "status": "available",
                 "balance": {"amount": 13.01, "currency": "USD"}},
}


def read(transport, _data=None):
    return READINGS.get(transport)


def main():
    cases = []

    def case(name, ok, detail=""):
        cases.append((name, bool(ok), detail))

    try:
        spec = importlib.util.spec_from_file_location("quota_dashboard", TOOL)
        qd = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(qd)
    except Exception as exc:
        case("tools/quota_dashboard.py imports", False, repr(exc))
        return report(cases)

    view = qd.build(data=REGISTRY, read=read, session_plan="anthropic", now=NOW)
    plans = {r["plan"]: r for r in view["plans"]}
    case("every plan-quota transport is a plan row", sorted(plans) == ["anthropic", "codex"], sorted(plans))
    codex = plans.get("codex", {})
    case("a plan row carries type and band, and no figure render never reads",
         codex.get("type") == "prolite" and codex.get("band") == "Surplus"
         and not {"room", "days", "spare", "left"} & set(codex), codex)
    case("a plan at or below even pace keeps its band", plans["anthropic"]["band"] == "Ahead", plans.get("anthropic"))

    pay = {r["transport"]: r for r in view["pay"]}
    case("pay-per-token transports carry their state and balance",
         pay.get("deepseek", {}).get("state") == "available" and pay["deepseek"].get("balance") == "13.01 USD", pay)
    case("an unavailable transport shows its state and reason, and no balance read",
         pay.get("opencode", {}).get("state") == "unavailable" and "free tier blocked" in pay["opencode"].get("note", "")
         and pay["opencode"].get("balance") is None, pay.get("opencode"))

    tiers = {r["tier"]: r for r in view["tiers"]}
    case("each tier names the plans able to pay for it, never one pick, and the rows serving it",
         tiers.get("fanout", {}).get("pays") == "codex" and tiers["fanout"].get("rows") == ["c-fan"]
         and set(tiers.get("executor", {}).get("rows", [])) == {"a-exec", "d-exec"}, tiers)
    case("a tier no row serves still has a row, with nothing serving it",
         tiers.get("scout", {}).get("rows") == [] and not tiers["scout"].get("pays"), tiers.get("scout"))

    models = {r["alias"]: r for r in view["models"]}
    case("models in rotation list roles and their transport's state; superseded rows are left out",
         sorted(models) == ["a-exec", "c-fan", "d-exec"] and models["a-exec"]["roles"] == ["architect", "executor"]
         and models["d-exec"]["state"] == "available", models)
    case("a transport with no recorded status shows its capacity reading's status",
         models.get("c-fan", {}).get("state") == "available", models.get("c-fan"))

    text = qd.render(view)
    case("render prints the four section headings",
         all(h in text for h in ("## Plans", "## Pay per token", "## Tiers", "## Models")), text[:300])
    case("render prints the codex plan line with quota left beside window left and the reset",
         "| codex | prolite | Surplus | 60% | 20% | 1.4d |" in text
         and "| plan | type | band | quota left | window left | reset in | note |" in text
         and not banned_in(text), text)
    case("the plan table compares quota left with window left, never a derived gap",
         "| quota left | window left | reset in |" in text and "spare" not in text.lower() and "ahead of pace" not in text,
         text[:400])

    unread = qd.build(data=REGISTRY, read=lambda t, _d=None: None, session_plan="anthropic", now=NOW)
    row = {r["plan"]: r for r in unread["plans"]}.get("codex", {})
    case("an unreadable plan says why instead of printing a number",
         "unreadable" in (row.get("note") or ""), row)

    dual = plan_reading("codex", 40, 0.2, "prolite")
    dual["quota"]["windows"].append({"name": "five_hour", "usedPercent": 96,
                                     "resetsAt": NOW + 14400, "windowDurationMins": 300, "band": "Hot"})
    dual_text = qd.render(qd.build(data=REGISTRY, read=lambda t, _d=None: dual if t == "codex" else read(t),
                                  session_plan="anthropic", now=NOW))
    case("the dashboard shows the worst band and both differing windows without a second plan row",
         "| codex | prolite | Hot | 5h 4%; 7d 60% | 5h 80%; 7d 20% |" in dual_text
         and dual_text.split("## Pay per token")[0].count("| codex |") == 1, dual_text)
    spent = plan_reading("codex", 100, 0.2, "prolite", status="exhausted")
    spent_text = qd.render(qd.build(data=REGISTRY, read=lambda t, _d=None: spent if t == "codex" else read(t),
                                   session_plan="anthropic", now=NOW))
    case("an exhausted dashboard plan still has its quota and time figures",
         "| codex | prolite | Exhausted | 0% | 20% | 1.4d |" in spent_text, spent_text)
    boom = qd.build(data=REGISTRY, read=lambda t, _d=None: (_ for _ in ()).throw(RuntimeError("probe died")),
                    session_plan="anthropic", now=NOW)
    case("a read that raised puts its cause in the plan note", "probe died" in {r["plan"]: r for r in boom["plans"]}["codex"]["note"],
         boom["plans"])
    real_pick = qd.provider_bands.pick
    qd.provider_bands.pick = lambda *a, **k: (_ for _ in ()).throw(ValueError("pick broke"))
    try:
        crashed = {r["tier"]: r for r in qd.build(data=REGISTRY, read=read, session_plan="anthropic", now=NOW)["tiers"]}
    finally:
        qd.provider_bands.pick = real_pick
    case("a plan read that raised prints its error, not `none`", "error: pick broke" in str(crashed.get("fanout", {}).get("pays")), crashed.get("fanout"))
    real_load = qd.model_registry.load
    qd.model_registry.load = lambda *a, **k: (_ for _ in ()).throw(qd.model_registry.RegistryError("bad registry"))
    try:
        code = qd.main([])
    except Exception as exc:  # noqa: BLE001
        code = repr(exc)
    finally:
        qd.model_registry.load = real_load
    case("an unreadable registry exits 2 instead of a traceback", code == 2, code)
    return report(cases)


def report(cases):
    failed = [n for n, ok, _d in cases if not ok]
    for name, ok, detail in cases:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok else "   [%s]" % str(detail)[:500]))
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
