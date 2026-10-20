"""Re-runnable proof for tools/review_calibration.py: planted escape, yield and registry data fire each proposal
kind once, and one row short of each firing condition fires none.

    python3 .claude/tests/test_review_calibration.py
"""
import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), "tools")
sys.path.insert(0, TOOLS)

try:
    import review_calibration
except ImportError:
    review_calibration = None

MODEL = "model-a"
REGISTRY = {"models": [{"id": MODEL, "alias": "a", "family": "fam-a"}, {"id": "model-b", "alias": "b", "family": "fam-b"},
                       {"id": "model-c", "alias": "c", "family": "fam-c"}]}
MARKED = {"models": [
    dict(REGISTRY["models"][0], probation=[{"tier": "executor", "text": "owner mark"}]),
    dict(REGISTRY["models"][1], driverNotes=[{"text": "t", "evidenceRef": "e", "weakness": "w",
                                              "removeWhen": "two clean weeks"}]),
    REGISTRY["models"][2]]}


def seat(day, run, unique, mandate="lens-a", model=MODEL, domain=("sync",), stage="pre-merge", ledger_new=0,
         seat_key=None, outcome="rated", **extra):
    row = {"run": run, "label": "review:%s" % (seat_key or "s1"), "workflow": "review-fanout", "model": "model-b",
           "run_date": day, "author_model": model, "domain": list(domain), "review_stage": stage,
           "ledger_join": "joined", "mandates": {mandate: {"outcome": outcome, "unique": unique, "duplicate": 0}}}
    if ledger_new is not None:
        row["ledger_new"] = ledger_new
    if seat_key:
        row["seat_overlap"] = {"key": seat_key, "found": 1, "unique": unique, "duplicate": 0, "unique_critical": 0}
    row.update(extra)
    return row


def escape(**fields):
    base = {"id": "e1", "ts": "2026-09-20T00:00:00Z", "sha": "abc", "paths": ["x/y.py"], "shouldHaveCaught": "none",
            "writer": "self_evaluate", "authorModel": MODEL, "tier": "executor", "triage": "unrecorded",
            "domain": ["sync"], "evidence": "e"}
    base.update(fields)
    return base


def kinds(result):
    return sorted(p["kind"] for p in result["proposals"])


def main():
    failures, ran = [], []

    def check(ok, label, detail=""):
        ran.append(label)
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label + (": " + detail if detail else ""))

    def build(**kw):
        if review_calibration is None:
            return None
        args = dict(escapes=[], escape_bytes=None, yield_rows=[], registry=REGISTRY, today="2026-09-30")
        args.update(kw)
        return review_calibration.build(**args)

    def guarded(label, fn):
        try:
            check(bool(fn()), label)
        except Exception as exc:
            check(False, label, "%s: %s" % (type(exc).__name__, exc))

    absent = build()
    guarded("absent escape log: rows 0, bytes null, no proposals",
            lambda: absent["escapes"]["rows"] == 0 and absent["escapes"]["bytes"] is None
            and absent["proposals"] == [] and set(absent) == {"escapes", "mandates", "premerge", "proposals", "join"})

    zero_run = [seat("2026-09-%02d" % d, "r%d" % d, 0, ledger_new=1) for d in (1, 2, 3)]
    pre = [seat("2026-09-%02d" % d, "p%d" % d, 1, seat_key="cross", ledger_new=0) for d in (1, 2, 3, 4)]
    pre_result = build(yield_rows=pre)
    guarded("premerge-seat fires once for a ledger_new zero run with L printed",
            lambda: kinds(pre_result) == ["premerge-seat"] and "L=4" in pre_result["proposals"][0]["evidence"]
            and pre_result["premerge"][0]["ledger_new"] == 0 and pre_result["premerge"][0]["reviews"] == 4)
    nonzero = pre[:-1] + [seat("2026-09-05", "p5", 1, seat_key="cross", ledger_new=2)]
    guarded("premerge-seat: a non-zero newest row fires none", lambda: build(yield_rows=nonzero)["proposals"] == [])
    nulled = pre[:-1] + [seat("2026-09-05", "p5", 1, seat_key="cross", ledger_new=None)]
    nulled_result = build(yield_rows=nulled)
    guarded("premerge-seat: a null ledger_new never counts as zero",
            lambda: nulled_result["proposals"] == [] and nulled_result["premerge"][0]["null"] == 1)
    guarded("premerge-seat: other stages are not pre-merge data",
            lambda: build(yield_rows=[dict(r, review_stage="candidate") for r in pre])["premerge"] == [])

    merged = [seat("2026-09-%02d" % d, "m%d" % d, 0, seat_key="dup", ledger_new=1, stage="candidate") for d in range(1, 6)]
    merge_result = build(yield_rows=merged)
    merge_kinds = [p for p in merge_result["proposals"] if p["kind"] == "merge-seat"] if merge_result else []
    guarded("merge-seat fires once when seat_yield returns the key, with the marker per mandate",
            lambda: len(merge_kinds) == 1 and merge_kinds[0]["subject"] == "dup"
            and "review-when: escape mandate:lens-a since:2026-09-30 >= 1" in merge_kinds[0]["action"])
    guarded("merge-seat: four runs fire none",
            lambda: [p for p in build(yield_rows=merged[:4])["proposals"] if p["kind"] == "merge-seat"] == [])

    untagged = build(escapes=[escape(domain=["untagged"])])
    guarded("sensitivity-tag fires once for an untagged escape row",
            lambda: [p["kind"] for p in untagged["proposals"] if p["kind"] == "sensitivity-tag"] == ["sensitivity-tag"])
    guarded("sensitivity-tag: a tagged row fires none",
            lambda: not [p for p in build(escapes=[escape()])["proposals"] if p["kind"] == "sensitivity-tag"])

    unrecorded = build(escapes=[escape(triage="unrecorded")])
    guarded("triage-rule fires once for an unrecorded triage",
            lambda: [p["kind"] for p in unrecorded["proposals"] if p["kind"] == "triage-rule"] == ["triage-rule"])
    recorded = escape(triage="reach=external reversibility=none design=settled grounding=verified oracle=gate size=one-context")
    guarded("triage-rule: a trailer at the top of the reach ladder fires none",
            lambda: not [p for p in build(escapes=[recorded])["proposals"] if p["kind"] == "triage-rule"])

    comp = build(escapes=[escape(id="e1"), escape(id="e2", ts="2026-09-25T00:00:00Z")], registry=MARKED)
    comps = [p for p in comp["proposals"] if p["kind"] == "compensation"] if comp else []
    guarded("compensation fires for the probation mark and the removeWhen note, with escape count and newest",
            lambda: sorted(p["subject"] for p in comps) == ["model-a", "model-b"]
            and "owner mark" in [p for p in comps if p["subject"] == "model-a"][0]["evidence"]
            and "2 escape" in [p for p in comps if p["subject"] == "model-a"][0]["evidence"]
            and "two clean weeks" in [p for p in comps if p["subject"] == "model-b"][0]["action"])
    guarded("compensation: a model with no mark or note fires none",
            lambda: not [p for p in build(registry=MARKED)["proposals"] if p["subject"] == "model-c"])

    counted = build(escapes=[escape(shouldHaveCaught="lens-a"), escape(id="e2", domain=["untagged"])], escape_bytes=321,
                    yield_rows=[seat("2026-09-01", "r1", 3, ledger_new=2), seat("2026-09-02", "r2", 2, ledger_new=None),
                                {"run": "x", "label": "review:x", "ledger_join": "absent"},
                                {"run": "y", "label": "review:y", "ledger_join": "unreadable"}])
    guarded("escapes and mandates facts count rows, bytes, unique and ledger_new (null skipped)",
            lambda: counted["escapes"]["rows"] == 2 and counted["escapes"]["bytes"] == 321
            and counted["escapes"]["by_mandate"]["lens-a"] == 1 and counted["escapes"]["by_model"][MODEL] == 2
            and counted["mandates"][0]["key"] == "lens-a" and counted["mandates"][0]["reviews"] == 2
            and counted["mandates"][0]["unique"] == 5 and counted["mandates"][0]["ledger_new"] == 2
            and counted["mandates"][0]["escapes"] == 1)
    two = seat("2026-09-03", "r3", 1, ledger_new=5)
    two["mandates"]["lens-b"] = {"outcome": "rated", "unique": 0, "duplicate": 0}
    split = {m["key"]: m for m in build(yield_rows=[seat("2026-09-01", "r1", 3, ledger_new=2), two])["mandates"]}
    guarded("seat-level ledger_new counts only single-mandate seats; a mandate only multi-mandate seats reach is null",
            lambda: split["lens-a"]["ledger_new"] == 2 and split["lens-b"]["ledger_new"] is None
            and split["lens-b"]["reviews"] == 1)
    guarded("join counts keep absent and unreadable apart from joined",
            lambda: counted["join"] == {"joined": 2, "absent": 1, "unreadable": 1})

    before = copy.deepcopy(zero_run)
    build(yield_rows=zero_run)
    guarded("build does not mutate its inputs", lambda: before == zero_run)

    longer = [seat("2026-09-%02d" % d, "w%d" % d, 1 if d < 3 else 0, seat_key="long", ledger_new=1, stage="candidate")
              for d in range(1, 8)]
    long_merge = [p for p in build(yield_rows=longer)["proposals"] if p["kind"] == "merge-seat"]
    guarded("merge-seat evidence names the flagged window and the reviews joined, not every row as zero",
            lambda: len(long_merge) == 1 and long_merge[0]["evidence"].startswith("the last 5 of 7 reviews carry unique 0"))
    guarded("a null escape sha still yields a triage-rule proposal",
            lambda: [p["kind"] for p in build(escapes=[escape(sha=None)])["proposals"] if p["kind"] == "triage-rule"]
            == ["triage-rule"])
    same_day = [seat("2026-09-10", "late", 1, timestamp="2026-09-10T12:00:00Z"),
                seat("2026-09-10", "early", 1, timestamp="2026-09-10T08:00:00Z")]
    guarded("same-day rows that carry a timestamp order by it",
            lambda: [r["run"] for r in review_calibration._ordered(same_day)] == ["early", "late"])
    tied = ([seat("2026-09-01", "t1", 1, seat_key="tie", ledger_new=1, stage="candidate"),
             seat("2026-09-01", "t2", 0, seat_key="tie", ledger_new=1, stage="candidate")]
            + [seat("2026-09-%02d" % d, "t%d" % (d + 1), 0, seat_key="tie", ledger_new=1, stage="candidate")
               for d in range(2, 6)])
    guarded("merge-seat: a streak whose same-day order is undecidable raises no proposal, in either row order",
            lambda: all([p for p in build(yield_rows=rows)["proposals"] if p["kind"] == "merge-seat"] == []
                        for rows in (tied, tied[1::-1] + tied[2:])))
    stamped = [dict(tied[0], timestamp="2026-09-01T09:00:00Z"), dict(tied[1], timestamp="2026-09-01T10:00:00Z")] + tied[2:]
    guarded("merge-seat: the same streak with timestamps that order the day raises the proposal",
            lambda: len([p for p in build(yield_rows=stamped)["proposals"] if p["kind"] == "merge-seat"]) == 1)

    print("\n%d cases, %d failed" % (len(ran), len(failures)))
    for line in failures:
        print("  FAIL " + line)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
