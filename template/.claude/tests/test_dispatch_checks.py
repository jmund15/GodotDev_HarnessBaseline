#!/usr/bin/env python3
"""Proof for hooks/_dispatch_checks.py: the pre-launch check both dispatch routes share.

`tier_fit` is RULE 3's membership check on the job's own transport. Every case plants the registry rows (a dict).
The channel cases live beside each caller: tests/test_workflow_provider_guard.py (native) and
tests/test_sidecar_dispatch_context.py (sidecar).

    python3 .claude/tests/test_dispatch_checks.py
"""
import importlib.util
import os
import sys

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
    case("the late currency, probation and cheaper-claimant builders are gone",
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

    return report(cases)
    case("hooks/_dispatch_checks.py exposes tier_fit", hasattr(dc, "tier_fit"))
    if not hasattr(dc, "tier_fit"):
        return report(cases)
    # The advisories that arrived after the decision are gone: their facts reach the session earlier
    # (`[budget-posture]` from the first prompt, `for-role` probation marks, the ladder before the gate admits).
    case("the late currency, probation and cheaper-claimant builders are gone",
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

    read = read_from(TODAY)
    no_pick = lambda text: isinstance(text, str) and "pick" not in text.lower()
    line = dc.currency("g-exec", "executor", "anthropic", DATA, read)
    case("a go job spending faster than its window names go and the plan with more room, never a pick",
         no_pick(line) and "go Hot: 7d 70% left, 90% to go" in line and "spending faster than its window" in line
         and "plans paying for a row claiming executor with more quota left for their window: anthropic Hot: 7d 78% left, 87% to go" in line
         and "codex" not in line and "orchestration §5b" in line, repr(line))
    case("a job whose plan has the most room gets no advisory",
         dc.currency("a-exec", "executor", "anthropic", DATA, read) is None)
    roomy_own = dict(TODAY, anthropic=reading("anthropic", 10, 0.3))
    case("a job whose plan spends slower than its window gets no advisory, whatever the others read",
         dc.currency("a-exec", "executor", "anthropic", DATA, read_from(dict(roomy_own, go=reading("go", 0, 0.1))))
         is None)
    line = dc.currency("d-exec", "executor", "anthropic", DATA, read)
    case("a pay-per-token job while every fit plan spends faster than its window still names the plans with quota (§5b: dollars are the last resort)",
         no_pick(line) and "deepseek pays per token" in line and "anthropic Hot: 7d 78% left" in line
         and "go Hot: 7d 70% left" in line and "codex" in line and "with quota left" in line, repr(line))
    line = dc.currency("d-exec", "executor", "anthropic", DATA, read_from(roomy_own))
    case("a pay-per-token job while a plan spends under its window's pace names every plan with quota",
         no_pick(line) and "deepseek pays per token" in line and "anthropic Surplus: 7d 90% left" in line
         and "go Hot" in line, repr(line))
    mixed = dict(TODAY, go=reading("go", 100, 0.5), codex=None)
    line = dc.currency("d-exec", "executor", "anthropic", DATA, read_from(mixed))
    case("a spent plan and an unreadable plan are not named as plans with quota",
         no_pick(line) and "anthropic" in line and "go " not in line.split("with quota left:")[-1]
         and "codex" not in line.split("with quota left:")[-1], repr(line))
    spent = {k: reading(k, 100, 0.5) for k in TODAY}
    case("a pay-per-token job with no plan left gets no advisory",
         dc.currency("d-exec", "executor", "anthropic", DATA, read_from(spent)) is None)
    line = dc.currency("g-exec", None, "anthropic", DATA, read)
    case("no tier: the row's claimed tiers stand in",
         no_pick(line) and "spending faster than its window" in line and "a row claiming executor/fanout" in line, repr(line))
    dropped = dict(TODAY, go=None)
    line = dc.currency("g-exec", "executor", "anthropic", DATA, read_from(dropped))
    case("a job on an unreadable plan names why, and the plans with room",
         no_pick(line) and "go unreadable" in line and "anthropic Hot" in line, repr(line))

    def boom(*_a, **_k):
        raise RuntimeError("planted reader failure")
    line = dc.currency("g-exec", "executor", "anthropic", DATA, boom)
    case("a reader that raises never raises out of currency", line is None or isinstance(line, str), repr(line))
    case("an unknown model gets no advisory and no exception",
         dc.currency("no-such-model", "executor", "anthropic", DATA, read) is None)

    facts = dc.currency_facts("g-exec", "executor", "anthropic", DATA, read)
    case("currency_facts returns the line and the tiers, nothing else",
         isinstance(facts, dict) and set(facts) == {"line", "tiers"}, repr(facts))
    if facts:
        plain = dc.currency_note(facts, [("job-a", "executor"), ("job-b", None)], "anthropic", DATA)
        case("currency_note names the advisory's jobs and adds no launcher line",
             plain == "%s (jobs: job-a, job-b)" % facts["line"], repr(plain))
        real_cap = dc.CURRENCY_JOB_LIST_CAP
        dc.CURRENCY_JOB_LIST_CAP = 1
        try:
            capped = dc.currency_note(facts, [("job-a", "executor"), ("job-b", "executor")], "anthropic", DATA)
        finally:
            dc.CURRENCY_JOB_LIST_CAP = real_cap
        case("the job list is capped and counts the rest", capped.endswith("(jobs: job-a, +1 more)"), repr(capped))

    # A second plan paying for anthropic's rows: the job's PLAN, not its row's transport, is compared.
    plans = {"transports": dict(DATA["transports"], **{
        "anthropic-secondary": {"planOf": "anthropic", "costModel": "plan-quota"}}),
        "models": DATA["models"]}
    roomy = dict(TODAY, **{"anthropic-secondary": reading("anthropic-secondary", 5, 6.1 / 7)})
    line = dc.currency("a-exec", "executor", "anthropic", plans, read_from(roomy))
    case("a native job from a primary session with a roomier secondary names anthropic-secondary",
         no_pick(line) and "anthropic-secondary" in line.split("with more quota left for their window", 1)[1]
         and "anthropic Hot: 7d 78% left" in line, repr(line))
    line = dc.currency("a-exec", "executor", "anthropic", plans, read_from(roomy), job_plan="anthropic-secondary")
    case("a job launched on a plan spending slower than its window gets no advisory", line is None, repr(line))
    line = dc.currency("a-exec", "executor", "anthropic-secondary", plans, read_from(roomy))
    case("a native job from a secondary session defaults to the session plan: no advisory",
         line is None, repr(line))
    short = dict(TODAY)
    short["anthropic"] = reading("anthropic", 22, 6.1 / 7)
    short["anthropic"]["quota"]["windows"].append(
        {"name": "five_hour", "usedPercent": 83, "resetsAt": NOW + 0.46 * 300 * 60, "windowDurationMins": 300})
    line = dc.currency("a-exec", "executor", "anthropic", DATA, read_from(short))
    case("a job on a plan whose 5-hour window binds gets the advisory, naming its binding window",
         no_pick(line) and "5h 17% left, 46% to go" in line and "go Hot: 7d 70% left" in line, repr(line))
    line = dc.currency("g-exec", "executor", "anthropic-secondary", plans, read_from(roomy))
    case("a cross-transport job's plan defaults to its row's transport",
         no_pick(line) and line.startswith("currency (orchestration §5b): go ") and "anthropic-secondary" in line,
         repr(line))

    # probation: an advisory line for a pin on probation for the declared tier, else None.
    probation = getattr(dc, "probation", None)
    case("hooks/_dispatch_checks.py exposes probation", callable(probation))
    if callable(probation):
        empty = os.path.join(HERE, "__no_such_archive__.jsonl")
        real = dc.model_registry.metrics_archive if hasattr(dc.model_registry, "metrics_archive") else None
        dc.model_registry.metrics_archive = lambda: empty
        try:
            line = probation("a-fan", "executor", DATA)
            case("an unclaimed tier prints the probation line with its basis and the verifier pointer",
                 isinstance(line, str) and line.startswith("probation (orchestration §5): a-fan for executor — ")
                 and "no executor claim in registry roles" in line
                 and "drive_spine §Slices runs the spec verifier" in line, repr(line))
            case("a claimed tier prints nothing", probation("a-exec", "executor", DATA) is None)
            case("no declared tier prints nothing", probation("a-fan", None, DATA) is None)
            case("an unknown model prints nothing and never raises",
                 probation("no-such-model", "executor", DATA) is None)
            case("an unknown tier prints nothing and never raises", probation("a-fan", "Wide", DATA) is None)
        finally:
            if real is not None:
                dc.model_registry.metrics_archive = real

    # cheaper_rows: the rows on the job's transport claiming its tier at a lower ladder cost, each with its cost
    # and `±` cell, so the pin is weighed over every claimant (a fact line, never a pick or a deny).
    cheaper_rows = getattr(dc, "cheaper_rows", None)
    case("hooks/_dispatch_checks.py exposes cheaper_rows", callable(cheaper_rows))
    if callable(cheaper_rows):
        import tempfile
        data = {"transports": DATA["transports"], "models": DATA["models"] + [
            {"transport": "anthropic", "id": "host-cheap-1", "alias": "a-cheap", "roles": ["executor", "fanout"]},
            {"transport": "anthropic", "id": "host-dear-1", "alias": "a-dear", "roles": ["executor"]},
            {"transport": "go", "id": "go-cheap-1", "alias": "g-cheap", "roles": ["executor"]}]}
        ladder = ("# Ladder\n\n## Role guidance\n\n| model | cost | ± |\n|---|---|---|\n"
                  "| a-dear | 3 | + deep design; − the most expensive row |\n"
                  "| a-exec | 7 | + tight exec |\n"
                  "| a-cheap | 10 | + the cheapest reliable pick for converged execution; − never a lone review seat |\n"
                  "| g-cheap | 9 | + go executor |\n"
                  "| a-fan | ? | + extraction |\n\n## Next\n")
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "ladder.md")
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(ladder)
            line = cheaper_rows("a-exec", "executor", "anthropic", data, ladder=path)
            case("a pricier pin names each cheaper claimant on its transport with cost and ± cell",
                 isinstance(line, str) and "a-cheap (cost 10)" in line and "never a lone review seat" in line
                 and "a-exec (cost 7)" in line and "`executor`" in line, repr(line))
            case("a claimant on another transport is not named", isinstance(line, str) and "g-cheap" not in line,
                 repr(line))
            case("a pricier claimant is not named", isinstance(line, str) and "a-dear" not in line, repr(line))
            case("the line cites the selection line and never says which row to pin",
                 isinstance(line, str) and "selection line" in line and "pin a-cheap" not in line.lower(), repr(line))
            case("the cheapest claimant gets no line", cheaper_rows("a-cheap", "executor", "anthropic", data,
                                                                    ladder=path) is None)
            case("no tier gets no line", cheaper_rows("a-exec", None, "anthropic", data, ladder=path) is None)
            case("a pin with no ladder cost gets no line",
                 cheaper_rows("a-fan", "fanout", "anthropic", data, ladder=path) is None)
            case("an unknown model never raises", cheaper_rows("nope", "executor", "anthropic", data,
                                                               ladder=path) is None)
            case("an unreadable ladder never raises",
                 cheaper_rows("a-exec", "executor", "anthropic", data, ladder=os.path.join(td, "gone.md")) is None)
            # A `?` ladder cost is no cost: a claimant carrying one is never named cheaper, while a costed one still is.
            unk_path = os.path.join(td, "ladder_unk.md")
            with open(unk_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(ladder.replace("| a-fan | ? |", "| a-unk | ? | + executor with no measured cost |\n| a-fan | ? |"))
            unk_data = {"transports": data["transports"], "models": data["models"] + [
                {"transport": "anthropic", "id": "host-unk-1", "alias": "a-unk", "roles": ["executor"]}]}
            line = cheaper_rows("a-exec", "executor", "anthropic", unk_data, ladder=unk_path)
            case("a `?`-cost claimant is not named as cheaper; a costed one still is",
                 isinstance(line, str) and "a-cheap (cost 10)" in line and "a-unk" not in line, repr(line))
    return report(cases)


def report(cases):
    failed = [n for n, ok, _d in cases if not ok]
    for name, ok, detail in cases:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok else "   [%s]" % str(detail)[:500]))
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
