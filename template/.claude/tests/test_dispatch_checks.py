#!/usr/bin/env python3
"""Proof for hooks/_dispatch_checks.py: the pre-launch check both dispatch routes share.

`tier_fit` is RULE 3's membership check on the job's own transport. `cheaper_claimants` lists the cheaper claimants of
a tier with their facts, and `claimant_text` renders one. Every case plants the registry rows (a dict) and a ladder
file. The channel cases live beside each caller: tests/test_workflow_provider_guard*.py (native) and
tests/test_sidecar_dispatch_context.py (sidecar).

    python3 .claude/tests/test_dispatch_checks.py
"""
import datetime
import importlib.util
import json
import os
import sys
import tempfile
import time

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

    # kind_tiers: the tier a job implies comes from its kind row; judge is an executor-role job.
    kind_tiers = getattr(dc, "kind_tiers", None)
    case("hooks/_dispatch_checks.py exposes kind_tiers", callable(kind_tiers))
    ktiers = kind_tiers() if callable(kind_tiers) else {}
    case("kind_tiers reads each kind row's tier",
         (ktiers.get("scout"), ktiers.get("lens"), ktiers.get("reviewer"), ktiers.get("author"),
          ktiers.get("judge")) == ("fanout", "fanout", "executor", "executor", "executor"), repr(ktiers))
    why = dc.tier_fit("g-fan", ktiers.get("judge"), "go", DATA)
    case("a kind judge job pinned to a fanout-only row is denied", isinstance(why, str) and "g-fan" in why, repr(why))
    case("a kind judge job pinned to an executor row passes", dc.tier_fit("g-exec", ktiers.get("judge"), "go", DATA) is None)

    # job_tier: the written `tier` wins over the kind row's; `validation` folds onto `fanout`.
    case("job_tier takes the kind row's tier when none is written",
         dc.job_tier("reviewer") == ("executor", "kind `reviewer`"), repr(dc.job_tier("reviewer")))
    case("a written tier outranks the kind row's",
         dc.job_tier("reviewer", "architect") == ("architect", "the job's `tier`"), repr(dc.job_tier("reviewer", "architect")))
    case("a written validation tier folds onto fanout", dc.job_tier("author", "validation")[0] == "fanout")
    try:
        dc.job_tier("author", "bogus")
        refused = False
    except ValueError:
        refused = True
    case("an unknown written tier raises ValueError", refused)
    case("a kind with no row and no written tier has no tier", dc.job_tier("nokind") == (None, None))

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

    # cheaper_claimants: the facts the preflight line prints for a pin above a cheaper claimant.
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
        edge = facts[0]["usable"] - 1000
        bare = dc.cheaper_claimants("dear-1", "fanout", "anthropic", edge, data, ladder)
        loaded = dc.cheaper_claimants("dear-1", "fanout", "anthropic", edge, data, ladder, load=5000)
        case("a reading that fits alone is over once the harness load is added",
             bare[0]["fits"] is True and loaded[0]["fits"] is False, repr((bare, loaded)))
        claude = pricier_data(id="claude-cheap-1")
        login = os.path.join(td, "login")
        os.mkdir(login)
        with open(os.path.join(login, "settings.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"autoCompactWindow": 500000}, fh)
        saved_login = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = login
        try:
            held = dc.cheaper_claimants("dear-1", "fanout", "anthropic", 200000, claude, ladder,
                                        job={"kind": "reviewer"})
            wide = dc.cheaper_claimants("dear-1", "fanout", "anthropic", 300000,
                                        pricier_data(id="claude-cheap-1", longContextTier=None,
                                                     limits={"contextTokens": 1000000, "maxOutputTokens": 128000}),
                                        ladder, job={"kind": "author"})
        except TypeError as err:
            held = wide = repr(err)
        finally:
            if saved_login is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = saved_login
        case("a Haiku-like claimant keeps its own 217,000-token point whatever the job's kind",
             isinstance(held, list) and held and held[0]["usable"] == 217000 and held[0]["fits"] is True
             and "217,000-token compaction point" in dc.claimant_text(held[0], 200000), repr(held))
        case("a claimant with no operating point is measured against the login's 500,000 window less the reserve, 467,000 tokens",
             isinstance(wide, list) and wide and wide[0]["usable"] == 467000 and wide[0]["fits"] is True
             and "467,000-token compaction point" in dc.claimant_text(wide[0], 300000), repr(wide))
        ceiling = dc.claimant_text(wide[0], 300000, 500000)
        case("a claimant is judged on the lower number: an upper number past its point says ceiling over and still fits",
             "reading 300,000 tokens fits its 467,000-token compaction point, ceiling over (500,000 if every named "
             "file is read whole)" in ceiling and "is over" not in ceiling, ceiling)
        case("an upper number inside the point, or none, adds no ceiling clause",
             "ceiling over" not in dc.claimant_text(wide[0], 300000, 400000)
             and "ceiling over" not in dc.claimant_text(wide[0], 300000))
        case("a lower number past the point is over, never ceiling over",
             "is over" in dc.claimant_text(over[0], 900000, 950000)
             and "ceiling over" not in dc.claimant_text(over[0], 900000, 950000))
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

        rows = {"anthropic": None, "go": plan_reading("go", 20, 3)}
        fact = spare_fact("executor", "anthropic", data=DATA, now=now, read=lambda name, _data=None: rows.get(name))
        case("a claimant of the tier on another plan with expiring quota is named with its plan and share",
             fact == "g-exec on go: 7d window, ~65% on course to go unused at its reset in 3.0d", repr(fact))
        rows = {"anthropic": None, "go": plan_reading("go", 99, 1)}
        case("a plan whose quota will not expire unused names nothing",
             spare_fact("executor", "anthropic", data=DATA, now=now,
                        read=lambda name, _data=None: rows.get(name)) is None,
             repr(spare_fact("executor", "anthropic", data=DATA, now=now,
                             read=lambda name, _data=None: rows.get(name))))
        rows = {"anthropic": plan_reading("anthropic", 20, 3), "go": plan_reading("go", 20, 3)}
        case("the job's own plan is never the fact",
             spare_fact("executor", "go", data=DATA, now=now,
                        read=lambda name, _data=None: rows.get(name)) == "a-exec on anthropic: 7d window, ~65% on course to go unused at its reset in 3.0d",
             repr(spare_fact("executor", "go", data=DATA, now=now, read=lambda name, _data=None: rows.get(name))))
        rows = {"anthropic": None, "go": plan_reading("go", 20, 3), "codex": plan_reading("codex", 40, 2)}
        two = spare_fact("executor", "anthropic", data=DATA, now=now, read=lambda name, _data=None: rows.get(name))
        case("every plan with expiring quota is named, not only the first in registry order",
             set((two or "").split("; ")) == {"g-exec on go: 7d window, ~65% on course to go unused at its reset in 3.0d",
                                              "c-exec on codex: 7d window, ~44% on course to go unused at its reset in 2.0d"},
             repr(two))
    else:
        case("spare_plan_fact cases ran", False, "no spare_plan_fact")

    # ---- the five-hour width predicate RULE 5 and the fan-out's split share ----
    if hasattr(dc, "native_room"):
        case("a cool or unreadable window leaves the native count unbounded",
             dc.native_room(None, 0) is None and dc.native_room(0.5, 3) is None
             and dc.native_room(dc.quota_bands.FIVE_HOUR_NARROW_PRESSURE, 3) is None)
        hot = dc.quota_bands.FIVE_HOUR_NARROW_PRESSURE + 0.01
        case("a hot window leaves room for one native job less the live ones, never below zero",
             (dc.native_room(hot, 0), dc.native_room(hot, 1), dc.native_room(hot, 4)) == (1, 0, 0),
             repr((dc.native_room(hot, 0), dc.native_room(hot, 1), dc.native_room(hot, 4))))
    else:
        case("native_room cases ran", False, "no native_room")
    if hasattr(dc, "live_jobs") and hasattr(dc, "native_width"):
        root = tempfile.mkdtemp(prefix="dc_live_")
        run_dir = os.path.join(root, "subagents", "workflows", "wf_1")
        os.makedirs(run_dir)
        with open(os.path.join(run_dir, "journal.jsonl"), "w", encoding="utf-8", newline=chr(10)) as fh:
            fh.write(json.dumps({"type": "started", "agentId": "a1", "label": "x"}) + chr(10))
        live, note = dc.live_jobs(root)
        case("live_jobs reads a started agent with no terminal line from a session dir",
             [j["agentId"] for j in live] == ["a1"] and note is None, repr((live, note)))
        dc.five_hour_read = lambda session_id=None, budget=10.0: ({"pressure": 1.5, "resets_at": 9e9, "used": 80.0}, None)
        got = dc.native_width("sid", root)
        case("native_width joins the window read and the live count into the room",
             got["room"] == 0 and len(got["live"]) == 1 and got["pressure"] == 1.5, repr(got))
        dc.five_hour_read = lambda session_id=None, budget=10.0: ({}, "planted: no read")
        got = dc.native_width("sid", root)
        case("an unreadable window is no room limit and says why", got["room"] is None and got["note"], repr(got))
        # A busy session can write more than 8 MiB inside one live window (25.7 MB in 15 minutes measured): an
        # agent's completion notification that far back but inside the window still marks it finished.
        busy = tempfile.mkdtemp(prefix="dc_notify_")
        os.makedirs(os.path.join(busy, "subagents"))
        now = time.time()

        def stamp(t):
            return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat().replace("+00:00", "Z")

        agent = os.path.join(busy, "subagents", "agent-a2.jsonl")
        with open(agent, "w", encoding="utf-8", newline=chr(10)) as fh:
            fh.write(json.dumps({"type": "user", "timestamp": stamp(now - 120),
                                 "message": {"role": "user", "content": [{"type": "tool_result"}]}}) + chr(10))
        os.utime(agent, (now - 120, now - 120))
        with open(busy + ".jsonl", "w", encoding="utf-8", newline=chr(10)) as fh:
            fh.write(json.dumps({"type": "user", "timestamp": stamp(now - 110), "message": {"role": "user",
                     "content": "<task-notification><task-id>a2</task-id></task-notification>"}}) + chr(10))
            pad = "x" * 1000
            for i in range(9 * 1024):
                fh.write(json.dumps({"type": "assistant", "timestamp": stamp(now - 100 + i / 1000), "pad": pad}) + chr(10))
        live, note = dc.live_jobs(busy, now=now)
        case("a completion notification more than 8 MiB back but inside the live window still ends its agent",
             [j["agentId"] for j in live] == [] and note is None, repr((live, note)))
    else:
        case("live_jobs and native_width cases ran", False, "missing live_jobs or native_width")

    # ---- S3: a brief that orders a wide test run is refused at launch ----
    if hasattr(dc, "wide_run_refusal"):
        wide_run_cases(case, dc)
    else:
        case("wide_run_refusal cases ran", False, "no wide_run_refusal")
    if hasattr(dc, "wide_run_refusals"):
        joined_refusal_cases(case, dc)
    else:
        case("wide_run_refusals cases ran", False, "no wide_run_refusals")

    return report(cases)


WIDE_ROW = {"runner": r"npm\s+run\s+verify\b", "exempt": [], "narrow": None}
CLOSE_ROW = {"runner": r"(?:(?:python3?|py)\s+)?(?:\S*/)?harness_tests\.py\b",
             "exempt": ["^--help$"], "narrow": None}
STAGING_ROW = {"runner": r"npm\s+run\s+staging:e2e\b", "exempt": [],
               "narrow": {"requires": [r"\.spec\.ts:\d+", "--project"]}}

# Trimmed from `.claude/scratch/mentions/m15-brief.md:12`.
M15_TEMPLATE = ("An e2e row runs only if you change a screen's behavior beyond swapping its reader; then run "
                "that row alone on staging (`npm run staging:deploy` once, then `npm run staging:e2e -- "
                "<spec>:<line> --project=<desktop|phone>`).\n")
# Trimmed from `.claude/scratch/mentions/m2plain-brief.md:14` (one of five briefs carrying it).
MENTIONS_TEMPLATE = ("- E2 and E3 pass alone on staging on each project: `npm run staging:deploy`, then "
                     "`npm run staging:e2e -- e2e/mentions.spec.ts:<line> --project=<desktop|phone>`.\n")
# Trimmed from `.claude/scratch/ui-lock/r22/gatefix2-common.md:3`.
GATEFIX_REPORT = ("`npm run verify:merge` ran in `D:/proj/.claude/worktrees/"
                  "ui-r22-int` at `72818ca4`: 833 passed, 19 failed, 1 flaky.\n")
# Trimmed from the same file's line 11, beside its line 3.
GATEFIX_COMMON = (GATEFIX_REPORT + "1. Reproduce it alone on its project: `npm run staging:deploy` once in "
                  "your worktree, then `npm run staging:e2e -- <spec>:<line> --project=<desktop|phone>`.\n")
# Trimmed from `.claude/scratch/ui-lock/r22/gatefix-brief.md:5,24`.
GATEFIX_ORDER = ("Make `npm run verify:merge` green in the worktree "
                 "`D:/proj/.claude/worktrees/ui-r22-int`.\n"
                 "and `cd .claude/worktrees/ui-r22-int && npm run verify:merge` (it starts from the last "
                 "green head) end with 0 failed and 0 flaky.\n")
# Trimmed from `.claude/scratch/ui-lock/r22/gate-brief.md:21`.
PROOFS_ORDER = ("the harness proof for the doc: `python3 .claude/scripts/harness_tests.py --proofs-for "
                ".claude/skills/ui_lock/reference/review_page.md`\n")


def _cadence_root(rows):
    """A project root whose `gate_cadence.wide` holds `rows` and nothing else."""
    root = tempfile.mkdtemp(prefix="dc_wide_")
    folder = os.path.join(root, ".claude", "skills", "project_subsystems")
    os.makedirs(folder)
    with open(os.path.join(folder, "adaptation.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"gate_cadence": {"slice": [], "merge": [], "close": [], "wide": rows}}, fh)
    return root


def wide_run_cases(case, dc):
    root = _cadence_root([WIDE_ROW, CLOSE_ROW])
    prose = "Read the staging spec files and report what the last run said.\n"
    case("a brief that names no runner passes", dc.wide_run_refusal("author", prose, root) is None)
    ordered = "1. Run `npm run verify` before you finish.\n"
    why = dc.wide_run_refusal("author", ordered, root) or ""
    case("an author brief ordering a wide run is refused",
         "npm run verify" in why and "wide test run" in why, why)
    case("the refusal names the gate kind as the next step",
         "gate" in why and "orchestration" in why, why)
    case("a gate job may order the same run", dc.wide_run_refusal("gate", ordered, root) is None)
    case("a scout's ordered wide run is refused: every kind holds Bash, so every brief's commands are read",
         bool(dc.wide_run_refusal("scout", ordered, root)))
    case("a negated clause is a prohibition, not an instruction",
         dc.wide_run_refusal("author", "Never run `npm run verify` in this job.\n", root) is None)
    case("a checklist item quoted into the brief is data",
         dc.wide_run_refusal("author", "- [ ] npm run verify\n", root) is None)
    case("a json fence is data",
         dc.wide_run_refusal("author", '```json\n{"check": "npm run verify"}\n```\n', root) is None)
    case("a bare fence is an instruction",
         bool(dc.wide_run_refusal("author", "```\nnpm run verify\n```\n", root)))
    case("a --help run of the close runner is exempt",
         dc.wide_run_refusal("author", "Run `python3 .claude/scripts/harness_tests.py --help`.\n", root) is None)
    case("the close runner is wide in an author brief",
         bool(dc.wide_run_refusal(
             "author", "Run `python3 .claude/scripts/harness_tests.py --close` at the end.\n", root)))
    task = '```task\n{"check": ["npm run verify"], "writeSet": ["x.md"]}\n```\n'
    case("a task block check that is a wide run is refused for an author",
         bool(dc.wide_run_refusal("author", task, root)))
    case("the same task block check is refused for a scout (a task check runs in the parent)",
         bool(dc.wide_run_refusal("scout", task, root)))
    case("a gate job's task block check passes", dc.wide_run_refusal("gate", task, root) is None)
    bad = _cadence_root([{"runner": "(", "exempt": [], "narrow": None}])
    why_bad = dc.wide_run_refusal("author", prose, bad) or ""
    case("a malformed gate_cadence row fails closed and names the validation error",
         "wide[0].runner" in why_bad, why_bad)
    # The real briefs' deciding passages, trimmed from the files named in the vc-s3fix brief.
    project = _cadence_root([WIDE_ROW, CLOSE_ROW, STAGING_ROW])
    case("a narrow row's template, printed with a placeholder for the spec line, is not a wide order",
         dc.wide_run_refusal("author", M15_TEMPLATE, project) is None,
         dc.wide_run_refusal("author", M15_TEMPLATE, project))
    case("the same template with a real spec path and a placeholder line passes",
         dc.wide_run_refusal("author", MENTIONS_TEMPLATE, project) is None,
         dc.wide_run_refusal("author", MENTIONS_TEMPLATE, project))
    case("the template and a past-tense gate report in one brief pass together",
         dc.wide_run_refusal("author", GATEFIX_COMMON, project) is None,
         dc.wide_run_refusal("author", GATEFIX_COMMON, project))
    case("a sentence reporting that the merge gate ran is a status fact, not an order",
         dc.wide_run_refusal("author", GATEFIX_REPORT, root) is None,
         dc.wide_run_refusal("author", GATEFIX_REPORT, root))
    redirect = "Run `npm run staging:e2e < /dev/null > e2e.log` now.\n"
    case("a real shell redirect is not a placeholder: the wide staging run still denies",
         bool(dc.wide_run_refusal("author", redirect, project)),
         dc.wide_run_refusal("author", redirect, project))
    case("a placeholder does not narrow a row that declares no narrow form",
         bool(dc.wide_run_refusal("author", "Run `npm run verify -- <only>` once.\n", root)))
    case("an order making the merge gate green still denies",
         bool(dc.wide_run_refusal("author", GATEFIX_ORDER, root)),
         dc.wide_run_refusal("author", GATEFIX_ORDER, root))
    case("an order to run a doc's bound proofs still denies",
         bool(dc.wide_run_refusal("author", PROOFS_ORDER, root)))


def joined_refusal_cases(case, dc):
    """The one joined launch denial both dispatch hooks print, from `(label, kind, brief_text)` items."""
    root = _cadence_root([WIDE_ROW, CLOSE_ROW])
    items = [("alpha", "author", "1. Run `npm run verify` before you finish.\n"),
             ("beta", "author", "Read the staging spec files and report what the last run said.\n"),
             ("gamma", "author", "Run `python3 .claude/scripts/harness_tests.py --close` at the end.\n")]
    why = dc.wide_run_refusals(items, root) or ""
    case("the joined denial opens with the launch sentence",
         why.startswith("A brief orders a wide run, refused at launch — "), why)
    case("the joined denial names each wide job's label", "`alpha`:" in why and "`gamma`:" in why, why)
    case("the joined denial leaves a clean job out", "`beta`:" not in why, why)
    case("no wide item means no denial",
         dc.wide_run_refusals([("beta", "author", "Read the spec files.\n")], root) is None)
    many = [("job%d" % index, "author", "Run `npm run verify`.\n") for index in range(10)]
    why_many = dc.wide_run_refusals(many, root) or ""
    case("the joined denial names the first eight and counts the rest",
         "`job7`:" in why_many and "`job8`:" not in why_many and "(2 more)" in why_many, why_many)


def report(cases):
    failed = [n for n, ok, _d in cases if not ok]
    for name, ok, detail in cases:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok else "   [%s]" % str(detail)[:500]))
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
