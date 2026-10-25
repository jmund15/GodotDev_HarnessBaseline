#!/usr/bin/env python3
"""Proof for hooks/_dispatch_checks.py: the pre-launch check both dispatch routes share.

`tier_fit` is RULE 3's membership check on the job's own transport. `cheaper_claimants` lists the cheaper claimants of
a tier with their facts, and `claimant_text` renders one. Every case plants the registry rows (a dict) and a ladder
file. The channel cases live beside each caller: tests/test_workflow_provider_guard*.py (native) and
tests/test_sidecar_dispatch_context.py (sidecar).

    python3 .claude/tests/test_dispatch_checks.py
"""
import importlib.util
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, "..", "hooks")
sys.path.insert(0, HOOKS)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

DATA = {
    "transports": {"anthropic": {"costModel": "plan-quota"}, "go": {"costModel": "plan-quota"},
                   "codex": {"costModel": "plan-quota"}, "deepseek": {"costModel": "marginal-usd"}},
    "models": [
        {"transport": "anthropic", "id": "host-exec-1", "alias": "a-exec", "roles": ["architect", "executor"]},
        {"transport": "anthropic", "id": "host-fan-1", "alias": "a-fan", "roles": ["fanout"]},
        {"transport": "go", "id": "go-exec-1", "alias": "g-exec", "roles": ["executor", "fanout"]},
        {"transport": "go", "id": "go-fan-1", "alias": "g-fan", "roles": ["fanout"]},
        {"transport": "codex", "id": "codex-exec-1", "alias": "c-exec", "roles": ["executor"]},
        {"transport": "deepseek", "id": "paid-exec-1", "alias": "d-exec", "roles": ["executor", "fanout"]},
    ],
}

STEP = {"thresholdInputTokens": 100000, "multipliers": {"input": 5.0, "cachedInput": 5.0, "output": 5.0},
        "appliesPer": "request"}


HAIKU_PRICE = {"cacheMissPer1M": 0.1, "outputPer1M": 0.5, "cacheHitPer1M": 0.01}
SONNET_PRICE = {"cacheMissPer1M": 2, "outputPer1M": 10, "cacheHitPer1M": 0.1}


def pricier_data(**haiku):
    """A host transport with a dearer wide-window row and a cheaper row that prices past 100K, as the real pair does."""
    cheap = {"transport": "anthropic", "id": "cheap-1", "alias": "haiku", "roles": ["fanout", "scout", "executor"],
             "limits": {"contextTokens": 1000000, "maxOutputTokens": 128000, "compactAtTokens": 217000},
             "longContextTier": STEP, "price": HAIKU_PRICE}
    cheap.update(haiku)
    return {"transports": {"anthropic": {"costModel": "plan-quota"}}, "models": [
        {"transport": "anthropic", "id": "dear-1", "alias": "sonnet", "roles": ["architect", "executor", "fanout"],
         "limits": {"contextTokens": 1000000, "maxOutputTokens": 128000}, "price": SONNET_PRICE},
        cheap,
        {"transport": "anthropic", "id": "mid-1", "alias": "mid", "roles": ["executor"],
         "limits": {"contextTokens": 200000, "maxOutputTokens": 32000}}]}


def pay_data():
    """A pay-per-token transport holding a dearer and a cheaper row, so the claimant is priced in USD."""
    return {"transports": {"deepseek": {"costModel": "marginal-usd"}}, "models": [
        {"transport": "deepseek", "id": "ds-dear-1", "alias": "ds-dear", "roles": ["executor", "fanout"],
         "limits": {"contextTokens": 1000000, "maxOutputTokens": 128000}, "price": SONNET_PRICE},
        {"transport": "deepseek", "id": "ds-cheap-1", "alias": "ds-cheap", "roles": ["executor", "fanout"],
         "limits": {"contextTokens": 1000000, "maxOutputTokens": 128000}, "price": HAIKU_PRICE}]}


LADDER = ("# Ladder\n\n## Role guidance\n\n| model | cost | ± |\n|---|---|---|\n"
          "| sonnet | 7 | + the strong default |\n"
          "| haiku | 10 | + the cheapest reliable pick for read-heavy surveys; − uneven at review |\n"
          "| mid | 9 | + executor only |\n\n## Next\n")


def main():
    cases = []

    def case(name, ok, detail=""):
        cases.append((name, bool(ok), detail))

    try:
        spec = importlib.util.spec_from_file_location("_dispatch_checks", os.path.join(HOOKS, "_dispatch_checks.py"))
        dc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dc)
    except Exception as exc:
        case("hooks/_dispatch_checks.py imports", False, "%s: %s" % (type(exc).__name__, exc))
        return report(cases)
    case("hooks/_dispatch_checks.py exposes tier_fit", hasattr(dc, "tier_fit"))
    if not hasattr(dc, "tier_fit"):
        return report(cases)
    # The advisories that arrived after the decision are gone: their facts reach the session earlier
    # (`[budget-posture]` from the first prompt, `for-role` probation marks, the ladder before the gate admits).
    case("the late currency and probation builders are gone",
         not any(hasattr(dc, name) for name in ("currency", "currency_facts", "currency_note", "probation",
                                                "cheaper_rows")))

    # tier_fit: the job's own transport decides which rows fit.
    case("a Go alias that serves the tier fits on go", dc.tier_fit("g-exec", "executor", "go", DATA) is None)
    why = dc.tier_fit("g-fan", "executor", "go", DATA)
    case("a Go alias that does not serve the tier is refused, naming the tier's rows on go",
         isinstance(why, str) and "g-fan" in why and "`executor`" in why and "g-exec" in why, repr(why))
    case("a missing tier never refuses", dc.tier_fit("g-fan", None, "go", DATA) is None)
    case("fit is judged on the named transport, not another's rows",
         dc.tier_fit("a-exec", "executor", "go", DATA) is not None)

    # seat_fit: a row holding the tier in `supplementary` fills a review seat only beside a qualified seat.
    sdata = {"transports": DATA["transports"], "models": DATA["models"] + [
        {"transport": "anthropic", "id": "host-sup-1", "alias": "a-sup", "roles": ["fanout"],
         "supplementary": ["architect"]},
        {"transport": "anthropic", "id": "host-both-1", "alias": "a-both", "roles": ["architect", "executor"],
         "supplementary": ["architect"]}]}
    seat_fit = getattr(dc, "seat_fit", None)
    case("hooks/_dispatch_checks.py exposes seat_fit", seat_fit is not None)

    def seat(label, model, mandates, tier="architect", transport="anthropic"):
        return {"label": label, "model": model, "tier": tier, "mandates": list(mandates), "transport": transport}

    def fit(*seats, data=sdata):
        return seat_fit(list(seats), data) if seat_fit else ["seat_fit missing"]

    lone = fit(seat("s1", "a-sup", ["m1"]))
    case("a lone supplementary-only seat is denied, naming the rule and for-role",
         len(lone) == 1 and "supplementary review seat" in lone[0] and "m1" in lone[0]
         and "for-role architect" in lone[0] and "a-exec" in lone[0] and "a-sup" in lone[0], repr(lone))
    case("a supplementary seat beside a qualified merged seat carrying its mandates is allowed",
         fit(seat("s1", "a-sup", ["m1"]), seat("s2", "a-exec", ["m1", "m2"])) == [])
    other = fit(seat("s1", "a-sup", ["m1"]), seat("s2", "a-exec", ["m2"]))
    case("a qualified seat on other mandates only does not back it, and the line names the uncovered mandate",
         len(other) == 1 and "carries m1." in other[0], repr(other))
    merged = fit(seat("s1", "a-sup", ["m1", "m3"]), seat("s2", "a-exec", ["m1", "m2"]))
    case("a supplementary merged seat with one mandate uncovered is denied naming that mandate",
         len(merged) == 1 and "carries m3." in merged[0], repr(merged))
    case("several qualified seats together cover a merged supplementary seat",
         fit(seat("s1", "a-sup", ["m1", "m3"]), seat("s2", "a-exec", ["m1"]), seat("s3", "a-exec", ["m3"])) == [])
    case("a supplementary seat never backs another: both are denied",
         len(fit(seat("s1", "a-sup", ["m1"]), seat("s2", "a-sup", ["m1"]))) == 2)
    case("a lone seat holding the tier plainly and as supplementary is admitted with no refusal",
         fit(seat("s1", "a-both", ["m1"])) == [])
    case("a plain+supplementary seat does not back a supplementary-only seat",
         len(fit(seat("s1", "a-sup", ["m1"]), seat("s2", "a-both", ["m1"]))) == 1)
    xdata = {"transports": sdata["transports"], "models": sdata["models"] + [
        {"transport": "go", "id": "go-arch-1", "alias": "g-arch", "roles": ["architect"]}]}
    case("a qualified seat on another transport backs a supplementary seat: a panel spans transports",
         fit(seat("s1", "a-sup", ["m1"]), seat("s2", "g-arch", ["m1"], transport="go"), data=xdata) == [])
    xother = fit(seat("s1", "a-sup", ["m1"]), seat("s2", "g-arch", ["m2"], transport="go"), data=xdata)
    case("a qualified seat on another transport carrying other mandates does not back it",
         len(xother) == 1 and "carries m1." in xother[0], repr(xother))
    nowhere = fit(seat("s1", "a-sup", ["m1"]), data={"transports": sdata["transports"], "models": [
        {"transport": "anthropic", "id": "host-sup-1", "alias": "a-sup", "roles": ["fanout"],
         "supplementary": ["architect"]}]})
    case("with no qualified row anywhere, the line does not claim 'one of none'",
         len(nowhere) == 1 and "one of none" not in nowhere[0], repr(nowhere))
    case("a lone qualified seat is allowed", fit(seat("s1", "a-exec", ["m1"])) == [])
    case("a seat holding neither gets today's tier_fit line, byte-identical",
         fit(seat("s1", "a-fan", ["m1"])) == [dc.tier_fit("a-fan", "architect", "anthropic", sdata)])
    case("a seat with no mandates cannot be covered: a supplementary seat carrying none is denied",
         len(fit(seat("s1", "a-sup", []), seat("s2", "a-exec", ["m1"]))) == 1)
    case("a seat with no tier is never refused", fit(seat("s1", "a-sup", ["m1"], tier=None)) == [])
    case("without any supplementary field, a row outside the tier keeps today's tier_fit refusal",
         fit(seat("s1", "a-fan", ["m1"]), data=DATA) == [dc.tier_fit("a-fan", "architect", "anthropic", DATA)])

    # cheaper_claimants: the facts the preflight line and the brief hook print for a pin above a cheaper claimant.
    case("hooks/_dispatch_checks.py exposes cheaper_claimants", callable(getattr(dc, "cheaper_claimants", None)))
    if not callable(getattr(dc, "cheaper_claimants", None)):
        return report(cases)
    with tempfile.TemporaryDirectory() as td:
        ladder = os.path.join(td, "ladder.md")
        with open(ladder, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(LADDER)
        data = pricier_data()

        facts = dc.cheaper_claimants("dear-1", "fanout", "anthropic", 20000, data, ladder)
        case("cheaper_claimants names the cheaper row with cost, ± cell, window fit and long-context point, no pick",
             len(facts) == 1 and facts[0]["alias"] == "haiku" and facts[0]["cost"] == 10 and facts[0]["pinCost"] == 7
             and "uneven at review" in facts[0]["sign"] and facts[0]["fits"] is True
             and facts[0]["thresholdAt"] == 100000 and facts[0]["pastLongContext"] is False, repr(facts))
        case("a row not claiming the tier is not a claimant (mid claims only executor)",
             all(f["alias"] != "mid" for f in facts) and
             [f["alias"] for f in dc.cheaper_claimants("dear-1", "executor", "anthropic", 20000, data, ladder)]
             == ["haiku", "mid"])
        past = dc.cheaper_claimants("dear-1", "fanout", "anthropic", 120000, data, ladder)
        case("a reading past the price point is reported past it while still fitting the window",
             past[0]["pastLongContext"] is True and past[0]["fits"] is True, repr(past))
        over = dc.cheaper_claimants("dear-1", "fanout", "anthropic", 900000, data, ladder)
        case("a reading over the usable window does not fit", over[0]["fits"] is False, repr(over))
        claude = pricier_data(id="claude-cheap-1")
        try:
            held = dc.cheaper_claimants("dear-1", "fanout", "anthropic", 300000, claude, ladder,
                                        job={"resident": True, "shape": "author"})
        except TypeError as err:
            held = repr(err)
        case("a resident job's claimant is measured against the point it compacts at, 467,000 tokens",
             isinstance(held, list) and held and held[0]["usable"] == 467000 and held[0]["fits"] is True
             and "467,000-token compaction point" in dc.claimant_text(held[0], 300000), repr(held))
        case("an unmeasured reading leaves fit unjudged",
             dc.cheaper_claimants("dear-1", "fanout", "anthropic", None, data, ladder)[0]["fits"] is None)
        case("the cheapest claimant has no cheaper claimant",
             dc.cheaper_claimants("cheap-1", "fanout", "anthropic", 20000, data, ladder) == [])
        def raised(exc_type, fn):
            try:
                fn()
            except exc_type:
                return True
            return False
        case("an unknown model, an unreadable ladder and no tier raise, so [] always means nothing is cheaper",
             raised(dc.model_registry.UnknownModel,
                    lambda: dc.cheaper_claimants("nope", "fanout", "anthropic", 1, data, ladder))
             and raised((OSError, dc.model_registry.RegistryError),
                        lambda: dc.cheaper_claimants("dear-1", "fanout", "anthropic", 1, data, os.path.join(td, "gone.md")))
             and raised(ValueError, lambda: dc.cheaper_claimants("dear-1", None, "anthropic", 1, data, ladder)))
        claimant = dc.claimant_text(facts[0], 20000)
        case("the claimant line names haiku, its cost, its ± cell and its window fit",
             "haiku (cost 10" in claimant and "uneven at review" in claimant and "fits" in claimant, repr(claimant))
        past_claimant = dc.claimant_text(past[0], 120000)
        case("a plan claimant past its long-context point states its draw against the pin and that point, no USD",
             "draw ≈0.05x the pin" in past_claimant and "$" not in past_claimant
             and "past its 100,000-token long-context point" in past_claimant, repr(past_claimant))
        pay_ladder = os.path.join(td, "pay_ladder.md")
        with open(pay_ladder, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# Ladder\n\n## Role guidance\n\n| model | cost | ± |\n|---|---|---|\n"
                     "| ds-dear | 7 | + the strong default |\n| ds-cheap | 10 | + the cheap pick |\n")
        pay = dc.cheaper_claimants("ds-dear", "fanout", "deepseek", 20000, pay_data(), pay_ladder)
        pay_text = dc.claimant_text(pay[0], 20000) if pay else ""
        case("a pay-per-token claimant keeps its USD prices for the reading and names no draw",
             len(pay) == 1 and "≈$" in pay_text and "vs the pin's ≈$" in pay_text and "draw" not in pay_text,
             repr(pay_text))
        case("the long-context point is a fact, not a bar: the cheaper row past it still fits and is cheaper",
             past[0]["pastLongContext"] is True and past[0]["fits"] is True and past[0].get("price", 1e9) < past[0].get("pinPrice", 0)
             and past[0].get("priced") is True, repr(past))
        unpriced = pricier_data(price=None)
        ladder_only = dc.cheaper_claimants("dear-1", "fanout", "anthropic", 120000, unpriced, ladder)
        case("an unpriceable claimant falls back to ladder cost and is marked priced False",
             len(ladder_only) == 1 and ladder_only[0]["alias"] == "haiku" and ladder_only[0].get("priced") is False
             and ladder_only[0].get("price") is None, repr(ladder_only))
        down = pricier_data(status={"state": "unavailable", "reason": "planted"})
        case("an unavailable cheaper row is not a claimant",
             dc.cheaper_claimants("dear-1", "fanout", "anthropic", 20000, down, ladder) == [])
        marked = pricier_data(probation=[{"tier": "fanout", "text": "planted mark", "evidenceRef": "fixture"}])
        case("a cheaper row on an owner probation mark for the tier is not a claimant",
             dc.cheaper_claimants("dear-1", "fanout", "anthropic", 20000, marked, ladder) == [])

    # spare_plan_fact: one fact naming a claimant of the tier on another plan whose quota expires unused.
    spare_fact = getattr(dc, "spare_plan_fact", None)
    case("hooks/_dispatch_checks.py exposes spare_plan_fact", callable(spare_fact))
    if callable(spare_fact):
        now = 1_800_000_000.0

        def plan_reading(transport, used, left_days, days=7):
            return {"schemaVersion": 1, "transport": transport, "costModel": "plan-quota",
                    "sourceKind": "live", "observedAt": "2027-01-15T08:00:00Z", "status": "available",
                    "quota": {"windows": [{"name": "seven_day", "usedPercent": used,
                                           "resetsAt": now + left_days * 86400,
                                           "windowDurationMins": days * 1440}],
                              "routingWindow": "seven_day", "bindingWindow": "seven_day"}}

        rows = {"anthropic": None, "go": plan_reading("go", 20, 4)}
        fact = spare_fact("executor", "anthropic", data=DATA, now=now, read=lambda name, _data=None: rows.get(name))
        case("a claimant of the tier on another plan with expiring quota is named with its plan and share",
             fact == "g-exec on go: ~53% unused at reset in 4d", repr(fact))
        rows = {"anthropic": None, "go": plan_reading("go", 99, 1)}
        case("a plan whose quota will not expire unused names nothing",
             spare_fact("executor", "anthropic", data=DATA, now=now,
                        read=lambda name, _data=None: rows.get(name)) is None,
             repr(spare_fact("executor", "anthropic", data=DATA, now=now,
                             read=lambda name, _data=None: rows.get(name))))
        rows = {"anthropic": plan_reading("anthropic", 20, 4), "go": plan_reading("go", 20, 4)}
        case("the job's own plan is never the fact",
             spare_fact("executor", "go", data=DATA, now=now,
                        read=lambda name, _data=None: rows.get(name)) == "a-exec on anthropic: ~53% unused at reset in 4d",
             repr(spare_fact("executor", "go", data=DATA, now=now, read=lambda name, _data=None: rows.get(name))))
    else:
        case("spare_plan_fact cases ran", False, "no spare_plan_fact")

    return report(cases)


def report(cases):
    failed = [n for n, ok, _d in cases if not ok]
    for name, ok, detail in cases:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok else "   [%s]" % str(detail)[:500]))
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
