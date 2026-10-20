"""Re-runnable proof for tools/eval_history.py: publish, close, backfill, compare, and the month notes.

Unit cases call the library against a planted root; the CLI cases run tools/eval_dashboard.py against a
planted --root and --vault. Each numbered case reports its own failure so one broken seam does not hide the rest.

    python3 .claude/tests/test_eval_history.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), "tools")
sys.path.insert(0, TOOLS)
sys.path.insert(0, HERE)
import eval_history  # noqa: E402
import eval_render  # noqa: E402
import test_eval_dashboard as td  # noqa: E402

VERDICT = {"month": "2026-09", "state": "September held.", "moves": [], "watch": []}


def agent_cell(model, effort, *, wf=0, sc=0, unrated=0, other=0, qcost=0.0, qcost_n=0, usd=0.0, usd_n=0, **outcomes):
    cell = {"model": model, "effort": effort, "wf": wf, "sc": sc, "n": wf + sc, "unrated": unrated, "other": other,
            "qcost": qcost, "qcost_n": qcost_n, "usd": usd, "usd_n": usd_n}
    cell.update({name: outcomes.get(name, 0) for name in eval_history.OUTCOMES})
    return cell


def snap(month, n=10, clean=None, failure=0, unknown=0, a=0, unrated=0, sampled=0, eff=None, side=None, ladder=(),
         causes=None, paid=(0.0, 0)):
    clean = n - failure - unknown if clean is None else clean
    correction = n - unknown - clean - failure
    eff = eff or {}
    outcomes = {name: eff.get(name, 0) for name in eval_history.OUTCOMES}
    rated = sum(outcomes.values())
    usd, usd_n, side_n = side or (0.0, 0, 0)
    cells = []
    if side:
        cells.append(agent_cell("m-one", "low", sc=side_n, usd=usd, usd_n=usd_n))
    if eff:
        cells.append(agent_cell("m-one", "medium", wf=rated, qcost=float(eff.get("qcost", 0)), qcost_n=eff.get("qcost_n", 0),
                                **outcomes))
    rows = rated + unrated + sampled
    return {"month": month,
            "sources": {"self_eval": {"rows": n, "first": month + "-01", "last": month + "-20"},
                        "orchestration": {"rows": rows, "undated": 0, "first": month + "-01", "last": month + "-20"}},
            "sessions": {"n": n, "clean": clean, "correction": correction, "failure": failure, "unknown": unknown,
                         "patterns": {"A": a, "B": 0, "C": 0, "D": 0, "E": 0, "none": 0},
                         "causes": causes if causes is not None else {"A": a, "B": 0, "C": 0, "D": 0, "E": 0, "none": 0},
                         "domains": {}, "skills": {},
                         "duration": {"lt300": {"n": 0, "clean": 0}, "300_700": {"n": 0, "clean": 0},
                                      "gt700": {"n": 0, "clean": 0}, "unknown": n}},
            "agents": {"rows": rows, "rated": rated, "unrated": unrated, "sampled_out": sampled, "absent": 0,
                       "unparseable": 0, "cells": cells, "paid": {"usd": paid[0], "n": paid[1]}},
            "ladder": list(ladder), "attention": []}


def legacy_snap(month, **kw):
    """A month as schema 1 stored it: agents keyed by effort, sidecar rows keyed by served model."""
    shot = snap(month, **kw)
    shot["sessions"] = {key: value for key, value in shot["sessions"].items() if key != "causes"}
    shot["agents"] = {key: value for key, value in shot["agents"].items() if key not in ("cells", "paid")}
    shot["agents"]["by_effort"] = {"medium": dict(dict.fromkeys(eval_history.OUTCOMES, 0), clean=shot["agents"]["rated"],
                                                  n=shot["agents"]["rated"], qcost=5.0, qcost_n=shot["agents"]["rated"])}
    shot["agents"]["sidecar"] = {"m-one": dict(dict.fromkeys(eval_history.OUTCOMES, 0), n=2, usd=1.0, usd_n=2)}
    return shot


def schema2_snap(month, **kw):
    """A month as schema 2 stored it: cells without `other`, sessions without `causes`, agents without `paid`."""
    shot = snap(month, **kw)
    shot["sessions"] = {key: value for key, value in shot["sessions"].items() if key != "causes"}
    shot["agents"] = {key: value for key, value in shot["agents"].items() if key != "paid"}
    shot["agents"]["cells"] = [{key: value for key, value in c.items() if key != "other"} for c in shot["agents"]["cells"]]
    return shot


def stored(snapshot, closed=True, backfilled=False, verdict=None):
    return dict(snapshot, closed=closed, backfilled=backfilled, verdict=verdict)


def cell(model, n=9):
    return {"model_id": model, "tier": "executor", "n": n, "sessions": 3, "clean": n, "defects": 0, "rework": 0,
            "discarded": 0, "verdict": "holds"}


def plant(months=None, schema=None):
    root = tempfile.mkdtemp(prefix="evalhist_root_")
    os.makedirs(os.path.join(root, ".claude"))
    if months is not None:
        with open(eval_history.history_path(root), "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"schema": eval_history.SCHEMA if schema is None else schema, "months": months}, handle)
    return root


def load(root):
    with open(eval_history.history_path(root), encoding="utf-8") as handle:
        return json.load(handle)["months"]


def raw(root):
    with open(eval_history.history_path(root), "rb") as handle:
        return handle.read()


def facts(*candidates, rules=None):
    return {"months": {c["month"]: c for c in candidates}, "rules": rules}


def row_of(view, key):
    return next(r for r in view["rows"] if r["key"] == key)


def child(argv):
    root, month, gate = argv
    while not os.path.exists(gate):
        time.sleep(0.005)
    eval_history.publish(root, facts(snap(month)), None, month + "-15")
    return 0


class Suite:
    def __init__(self):
        self.failures, self.count = [], 0

    def check(self, ok, label, detail=""):
        self.count += 1
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            self.failures.append(label + (": " + detail if detail else ""))

    def guard(self, name, fn):
        try:
            fn()
        except Exception:
            self.count += 1
            print("FAIL %s raised" % name)
            self.failures.append("%s raised: %s" % (name, traceback.format_exc(limit=2).strip().splitlines()[-1]))


S = Suite()


def t1_idempotence():
    root = plant()
    first = facts(snap("2026-09", n=10, failure=2))
    eval_history.publish(root, first, VERDICT, "2026-09-10")
    a = raw(root)
    eval_history.publish(root, dict(first, rules={"fired": 4, "malformed": 0, "undecidable": 1}), None, "2026-09-20")
    S.check(raw(root) == a, "1: two runs on different days, the second without a verdict and with a rules feed that "
            "moved, write identical bytes")
    block = load(root)["2026-09"]
    S.check(block["verdict"] == VERDICT and "rules" not in block and block["closed"] is False,
            "1: the stored verdict stays, and the open month stores no rules block", repr(sorted(block)))
    other = stored(snap("2026-08", n=6))
    root2 = plant({"2026-08": other, "2026-09": stored(snap("2026-09"), closed=False)})
    before = load(root2)
    eval_history.publish(root2, facts(snap("2026-09", n=11)), None, "2026-09-11")
    after = load(root2)
    S.check(after["2026-08"] == before["2026-08"] and after["2026-09"]["sessions"]["n"] == 11
            and before["2026-09"]["sessions"]["n"] == 10,
            "1: one new planted entry changes only that month's block")


def t2_closing():
    root = plant()
    eval_history.publish(root, facts(snap("2026-08", n=10)), None, "2026-08-20")
    rules = {"fired": 2, "malformed": 0, "undecidable": 0}
    result = eval_history.publish(root, facts(snap("2026-08", n=12), snap("2026-09", n=3), rules=rules), None,
                                  "2026-09-02")
    aug = load(root)["2026-08"]
    S.check(aug["closed"] is True and aug["backfilled"] is False and aug["sessions"]["n"] == 12
            and aug["rules"] == rules and result["actions"].get("2026-08") == "closed",
            "2: a stored open month run in the next month is recomputed once and set closed, with its rules",
            repr(result["actions"]))
    closed_bytes = raw(root)
    eval_history.publish(root, facts(snap("2026-08", n=99), snap("2026-09", n=3)), None, "2026-09-03")
    S.check(load(root)["2026-08"] == json.loads(closed_bytes)["months"]["2026-08"],
            "2: a closed month is never rewritten by a later run, even after its source changed")
    eval_history.publish(root, facts(snap("2026-08", n=99), snap("2026-09", n=3)), None, "2026-09-04",
                         refreeze=("2026-08",))
    S.check(load(root)["2026-08"]["sessions"]["n"] == 99, "2: --refreeze rewrites a closed month")
    root2 = plant()
    eval_history.publish(root2, facts(snap("2026-09", n=10, eff={"clean": 6, "defects": 1, "qcost": 700.0,
                                                                  "qcost_n": 7}, ladder=[cell("m", 7)])),
                         None, "2026-09-10")
    eval_history.publish(root2, facts(snap("2026-09", n=6, eff={"clean": 3, "qcost": 300.0, "qcost_n": 3},
                                           ladder=[cell("m", 3)])), None, "2026-09-11")
    kept = load(root2)["2026-09"]
    S.check(kept["sessions"]["n"] == 10 and kept["agents"]["rows"] == 7 and kept["ladder"][0]["n"] == 7,
            "2: a recomputed block with a smaller population keeps the stored block, in the open month",
            repr(kept["agents"]["rows"]))
    root3 = plant()
    eval_history.publish(root3, facts(snap("2026-08", n=10)), None, "2026-08-20")
    eval_history.publish(root3, facts(snap("2026-08", n=4), snap("2026-09")), None, "2026-09-02")
    S.check(load(root3)["2026-08"]["sessions"]["n"] == 10 and load(root3)["2026-08"]["closed"] is True,
            "2: closing keeps a stored block that a rotation would have lowered")
    monotonic_mixed = plant()
    eval_history.publish(monotonic_mixed, facts(snap("2026-09", n=10)), None, "2026-09-10")
    eval_history.publish(monotonic_mixed, facts(snap("2026-09", n=6, eff={"clean": 5, "qcost": 5.0, "qcost_n": 5})),
                         None, "2026-09-11")
    mixed = load(monotonic_mixed)["2026-09"]
    S.check(mixed["sessions"]["n"] == 10 and mixed["agents"]["rows"] == 5,
            "2: each block is judged on its own population: sessions kept, agents replaced by the larger block")


def t3_backfill():
    root = plant()
    empty = snap("2026-05", n=0)
    empty["sources"]["orchestration"]["rows"] = 0
    result = eval_history.publish(root, facts(snap("2026-06", n=5, ladder=[cell("m")]), empty, snap("2026-09")), None,
                                  "2026-09-05")
    months = load(root)
    june = months["2026-06"]
    S.check(june["closed"] is True and june["backfilled"] is True and june["ladder"] == [] and june["rules"] is None
            and result["actions"].get("2026-06") == "backfilled",
            "3: a past month present only in the ledgers is stored backfilled with an empty ladder and null rules",
            repr(june.get("ladder")))
    S.check("2026-05" not in months and sorted(months) == ["2026-06", "2026-09"],
            "3: an empty month writes no entry", repr(sorted(months)))
    proposal = {"signal": "ladder proposal", "evidence": "m at executor: candidate (n 9, 3 sessions)", "tab": "ladder"}
    floor = {"signal": "effort floor moved", "evidence": "medium: 3 defects or rework in 10 rated agents", "tab": "agents"}
    with_rows = dict(snap("2026-04", n=5, ladder=[cell("m")]), attention=[proposal, floor])
    eval_history.publish(root, facts(with_rows, snap("2026-09")), None, "2026-09-05")
    S.check(load(root)["2026-04"]["attention"] == [floor],
            "3: a backfilled month drops its ladder-tab attention rows with its ladder and keeps the rest",
            repr(load(root)["2026-04"]["attention"]))


def t4_compare():
    def view(prior, cur, prior_backfilled=False):
        months = {}
        if prior is not None:
            months[prior["month"]] = stored(prior, backfilled=prior_backfilled)
        months[cur["month"]] = stored(cur, closed=False)
        return eval_history.compare(months, cur["month"] + "-28")

    v = view(snap("2026-08", n=20, clean=10), snap("2026-09", n=10, clean=8))
    row = row_of(v, "clean")
    S.check(row["marker"] == "better" and eval_render.change_text(row) == "+30pp better"
            and v["current"] == "2026-09" and v["prior"] == "2026-08",
            "4: clean 50% (n 20) then 80% (n 10) reads +30pp better", eval_render.change_text(row))
    exact = row_of(view(snap("2026-08", n=20, clean=10), snap("2026-09", n=20, clean=12)), "clean")
    S.check(exact["marker"] == "flat", "4: exactly 10 points is flat", repr(exact["marker"]))
    over = row_of(view(snap("2026-08", n=1000, clean=500), snap("2026-09", n=1000, clean=601)), "clean")
    S.check(over["marker"] == "better", "4: 10.1 points with the unfavorable count moved by 2 or more is a marker",
            repr(over["marker"]))
    one = row_of(view(snap("2026-08", n=8, clean=7, failure=1), snap("2026-09", n=8, clean=8, failure=0)), "failure")
    S.check(one["marker"] == "flat", "4: 1 of 8 against 0 of 8 is 12.5 points and still flat", repr(one["marker"]))
    worse = row_of(view(snap("2026-08", n=20, failure=1), snap("2026-09", n=20, failure=6)), "failure")
    S.check(worse["marker"] == "worse", "4: a rising failure rate is worse (lower is better)", repr(worse["marker"]))
    shift = row_of(view(snap("2026-08", n=20, clean=4, failure=1), snap("2026-09", n=20, clean=4, failure=8)), "correction")
    S.check(shift["marker"] is None,
            "4: a correction share that falls because failures rose is neither better nor worse", repr(shift["marker"]))
    low = row_of(view(snap("2026-08", n=20, clean=10), snap("2026-09", n=5, clean=5)), "clean")
    S.check(low["marker"] == "low N" and eval_render.change_text(low) == "low N",
            "4: below min N prints low N and no marker", repr(low["marker"]))
    none = view(None, snap("2026-09", n=20, clean=10))
    S.check(none["prior"] is None and row_of(none, "clean")["marker"] == "no prior month",
            "4: no prior month prints no prior month", repr(none["prior"]))
    keys = [r["key"] for r in v["rows"]]
    S.check(keys == ["clean", "correction", "failure", "cause_A", "cause_B", "cause_D", "cause_E", "cause_other", "defect_rework", "unrated"]
            and not {"qcost", "usd", "pattern_a"} & set(keys),
            "4: the metrics are the three session outcomes, the causes and two agent shares; no pooled cost row",
            repr(keys))
    S.check(all(r["label"] and r["definition"] and not any(word in (r["label"] + r["definition"]).lower()
                                                            for word in ("backfilled", "pattern a"))
                for r in v["rows"]),
            "4: every metric carries a plain label and a definition, and neither uses the retired words")
    back = view(snap("2026-08", n=20, clean=10, unrated=10, eff={"clean": 10}),
                snap("2026-09", n=20, clean=18, unrated=2, eff={"clean": 10}), prior_backfilled=True)
    S.check(row_of(back, "unrated")["marker"] == "rebuilt later" and row_of(back, "clean")["marker"] == "better"
            and eval_render.change_text(row_of(back, "unrated")).endswith(" rebuilt later"),
            "4: a prior month rebuilt later prints rebuilt later on agent metrics and a marker on session metrics",
            repr((row_of(back, "unrated")["marker"], row_of(back, "clean")["marker"])))
    mid = view(snap("2026-08", n=20, clean=10, failure=2, causes={"A": 6, "B": 4, "C": 0, "D": 0, "E": 0, "none": 0}),
               snap("2026-09", n=20, clean=10, failure=2, causes={"A": 2, "B": 8, "C": 0, "D": 0, "E": 0, "none": 0}))
    cause = row_of(mid, "cause_A")
    S.check(cause["current"]["value"] == 20.0 and cause["current"]["n"] == 10 and cause["current"]["count"] == 2
            and cause["prior"]["value"] == 60.0 and cause["marker"] is None and eval_render.change_text(cause) == "-40pp",
            "4: a cause is a share of the non-clean sessions with its count; the change is points with no better or worse word",
            repr(cause))
    few = row_of(view(snap("2026-08", n=20, clean=10, failure=2, causes={"A": 6, "B": 4, "C": 0, "D": 0, "E": 0, "none": 0}),
                      snap("2026-09", n=8, clean=5, causes={"A": 1, "B": 2, "C": 0, "D": 0, "E": 0, "none": 0})), "cause_A")
    S.check(few["marker"] == "low N", "4: a cause share below the minimum N of non-clean sessions prints low N", repr(few["marker"]))
    unknown_cause = eval_history.point(dict(snap("2026-09", n=10, clean=4), sessions=dict(snap("2026-09")["sessions"], causes=None)),
                                       "cause_A")
    S.check(unknown_cause["value"] is None, "4: a month with no recorded causes has no cause value", repr(unknown_cause))
    series = v["series"]["clean"]
    S.check(len(series) == 24 and series[-1]["month"] == "2026-09" and series[-2]["month"] == "2026-08"
            and series[0]["value"] is None and series[-1]["value"] == 80.0 and series[-1]["low"] is False,
            "4: the series is 24 calendar months ending at the current month, with gaps as None")
    skipped = eval_history.compare({"2026-06": stored(snap("2026-06")), "2026-08": stored(snap("2026-08")),
                                    "2026-09": stored(snap("2026-09"), closed=False)}, "2026-09-28")
    S.check(skipped["prior"] == "2026-08" and skipped["closed"] == ["2026-08", "2026-06"],
            "4: the prior month is the latest closed month, and closed lists newest first", repr(skipped["closed"]))


def t5_sparkline():
    points = [{"value": v, "low": low} for v, low in ((10, False), (20, False), (25, True), (40, False), (50, False),
                                                       (60, False))]
    S.check(eval_render.sparkline(points) == "▁▂·▅▇█",
            "5: a planted six-month series draws the exact string with a dot for the below-min-N month",
            eval_render.sparkline(points))
    S.check(eval_render.sparkline([{"value": None, "low": True}] * 3) == "···",
            "5: a series with no usable month is all dots")


def cli_root(today_runs):
    root, vault = td.plant_root(), tempfile.mkdtemp(prefix="evalhist_vault_")
    results = [td.run(root, vault, today=today) for today in today_runs]
    return root, vault, results


def t6_month_notes():
    root, vault, results = cli_root(["2026-09-28"])
    S.check(results[0].returncode == 0, "6: the CLI exits 0", (results[0].stdout + results[0].stderr)[-300:])
    archive = os.path.join(vault, "Eval Dashboard Archive")
    written = sorted(os.listdir(archive)) if os.path.isdir(archive) else []
    S.check(written == ["Eval Dashboard 2026-07.md", "Eval Dashboard 2026-08.md"],
            "6: one month note per closed month, under Eval Dashboard Archive", repr(written))
    with open(eval_history.history_path(root), encoding="utf-8") as handle:
        stored_months = json.load(handle)["months"]
    shutil.rmtree(root)
    pairs = list(eval_history.closed_pairs(stored_months))
    notes = {month: eval_render.month_note(snapshot, prev) for month, snapshot, prev in pairs}
    on_disk = {}
    for name in written:
        with open(os.path.join(archive, name), encoding="utf-8") as handle:
            on_disk[name[len("Eval Dashboard "):-3]] = handle.read()
    S.check([p[0] for p in pairs] == ["2026-07", "2026-08"] and pairs[0][2] is None
            and pairs[1][2]["month"] == "2026-07" and notes == on_disk and all(notes.values()),
            "6: with the sources deleted, the month notes still render, byte-equal to the ones written",
            repr([p[0] for p in pairs]))
    text = notes.get("2026-08", "")
    S.check("closed: true" in text and "backfilled: true" in text and "[[Eval Dashboard 2026-07]]" in text
            and "Clean sessions (the owner made no correction)" in text and "2026-08-03" in text,
            "6: a month note carries closed, backfilled, the previous month link, the metric table and the source span")
    S.check(list(eval_history.closed_pairs({"2026-09": stored(snap("2026-09"), closed=False)})) == [],
            "6: an open month has no month note")


def t7_refused():
    variants = {"unparseable": b"{not json", "not a dict": b"[1, 2]", "unknown schema": b'{"schema": 9, "months": {}}'}
    for name, payload in variants.items():
        root, vault = td.plant_root(), tempfile.mkdtemp(prefix="evalhist_vault_")
        path = eval_history.history_path(root)
        with open(path, "wb") as handle:
            handle.write(payload)
        result = td.run(root, vault)
        note = os.path.join(vault, "Self Evaluation Dashboard.md")
        text = open(note, encoding="utf-8").read() if os.path.isfile(note) else ""
        S.check(result.returncode == 4 and raw(root) == payload and os.path.isfile(note)
                and os.path.isfile(os.path.join(vault, "EVAL-DASHBOARD.html")) and "unparseable" in text
                and not os.path.isdir(os.path.join(vault, "Eval Dashboard Archive"))
                and not os.path.exists(path + ".tmp"),
                "7: a %s history exits 4, leaves its bytes, still writes the note and report, writes no month notes"
                % name, "rc=%s %s" % (result.returncode, (result.stdout + result.stderr)[-200:]))
    root = plant({"2026-09": stored(snap("2026-09"), closed=False)})
    before = raw(root)
    real = eval_history.update_json_locked
    eval_history.update_json_locked = lambda path, updater: (False, None)
    try:
        try:
            eval_history.publish(root, facts(snap("2026-09", n=11)), None, "2026-09-11")
            failed = False
        except eval_history.HistoryWriteError:
            failed = True
    finally:
        eval_history.update_json_locked = real
    S.check(failed and raw(root) == before, "7: a failed lock or write raises HistoryWriteError and leaves the file")
    import eval_dashboard
    real = eval_history.update_json_locked
    eval_history.update_json_locked = lambda path, updater: (False, None)
    root_b, vault_b = td.plant_root(), tempfile.mkdtemp(prefix="evalhist_vault_")
    try:
        code = eval_dashboard.main(["--root", root_b, "--vault", vault_b, "--today", "2026-09-28"])
    finally:
        eval_history.update_json_locked = real
    S.check(code == 1 and not os.path.exists(eval_history.history_path(root_b)),
            "7: a write failure exits 1 and creates no history file", repr(code))
    root_p = plant()
    gate = os.path.join(root_p, "go")
    months = ["2026-0%d" % i for i in range(1, 9)]
    procs = [subprocess.Popen([sys.executable, os.path.abspath(__file__), "--publish", root_p, m, gate])
             for m in months]
    time.sleep(1.0)
    open(gate, "w").close()
    codes = [p.wait(timeout=120) for p in procs]
    S.check(codes == [0] * 8 and sorted(load(root_p)) == months,
            "7: eight parallel publishers for different months all survive", repr((codes, sorted(load(root_p)))))


def t8_size():
    domains = {"domain-%02d" % i: {"n": 99, "clean": 60} for i in range(70)}
    skills = {"skill-%02d" % i: {"n": 99, "clean": 60} for i in range(60)}
    month = snap("2026-09", n=500, ladder=[cell("model-%02d" % i, 40) for i in range(12)])
    month["sessions"]["domains"], month["sessions"]["skills"] = domains, skills
    month["agents"]["cells"] = [
        agent_cell("model-%02d" % m, effort, wf=90, sc=10, unrated=3, qcost=123456789.5, qcost_n=88, usd=123.456789,
                   usd_n=9, clean=70, defects=9, rework=4, discarded=3)
        for m in range(10) for effort in ("low", "medium", "high", "xhigh", "max", "?")] + [
        agent_cell("?", effort, wf=90, sc=10, unrated=3, qcost=123456789.5, qcost_n=88, usd=123.456789, usd_n=9,
                   clean=70, defects=9, rework=4, discarded=3) for effort in ("low", "medium", "high", "xhigh", "max", "?")]
    root = plant()
    eval_history.publish(root, facts(month), None, "2026-09-10")
    size = len(json.dumps(load(root)["2026-09"], sort_keys=True, ensure_ascii=True))
    print("     planted maximal month: %d bytes; MONTH_MAX_BYTES %s" % (size, getattr(eval_history, "MONTH_MAX_BYTES", None)))
    S.check(size <= eval_history.MONTH_MAX_BYTES and eval_history.MONTH_MAX_BYTES <= size * 2,
            "8: a planted month with 70 domains, 60 skills, 12 ladder cells and 66 (model, effort) cells (10 served models "
            "and an unrecorded one, every rung and `?`) stays within MONTH_MAX_BYTES (a growth tripwire at the measured "
            "size times 1.5)", repr(size))


def t9_rules():
    root = plant()
    rules = {"fired": 1, "malformed": 0, "undecidable": 2}
    eval_history.publish(root, facts(snap("2026-09"), rules=rules), None, "2026-09-10")
    S.check("rules" not in load(root)["2026-09"], "9: rules is absent from the open month's block")
    eval_history.publish(root, facts(snap("2026-09"), rules=rules), None, "2026-10-01")
    S.check(load(root)["2026-09"].get("rules") == rules, "9: rules is present once the month closes")


def t10_cli_refreeze():
    root, vault = td.plant_root(), tempfile.mkdtemp(prefix="evalhist_vault_")
    td.run(root, vault)
    path = os.path.join(root, ".claude", "self_evaluate_archive.json")
    with open(path, encoding="utf-8") as handle:
        archive = json.load(handle)
    archive["structured_entries"].append(td.entry(11, "2026-08-25", "clean"))
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(archive, handle)
    again = td.run(root, vault)
    S.check(again.returncode == 0 and load(root)["2026-08"]["sessions"]["n"] == 3,
            "10: a plain run leaves a closed month alone after its source gained an entry")
    frozen = td.run(root, vault, "--refreeze", "2026-08")
    S.check(frozen.returncode == 0 and load(root)["2026-08"]["sessions"]["n"] == 4 and "2026-08 refrozen" in frozen.stdout
            and load(root)["2026-08"]["closed"] is True,
            "10: --refreeze 2026-08 rewrites the closed month and says so", frozen.stdout[-200:])
    bad = td.run(root, vault, "--refreeze", "August")
    S.check(bad.returncode == 2 and "--refreeze" in bad.stdout, "10: a malformed --refreeze month exits 2",
            "rc=%s %s" % (bad.returncode, bad.stdout[-120:]))


def t11_attention_rows():
    ladder = [dict(cell("m-hold"), verdict="holds"), dict(cell("m-cand"), verdict="candidate"),
              dict(cell("m-bad"), verdict="contradicts"), dict(cell("m-few", 3), verdict="insufficient")]
    families = {"review": {"m-a": {"high": {"rated": 8, "falsified": 2}, "low": {"rated": 7, "falsified": 1},
                                   "medium": {"rated": 5, "falsified": 2}},
                           "m-b": {"high": {"rated": 4, "falsified": 2}}},
                "scan": {"m-a": {"high": {"rated": 8, "falsified": 1}}}}
    candidates = [{"family": "fam", "model": "m-x", "effort": "high", "suggested": "medium", "cost_ratio": 2.5,
                   "n_hi": 2, "n_lo": 2}]
    trends = [{"skill": "sk-down", "direction": "DOWN", "low_n": False, "recent_pct": 20.0, "prior_pct": 50.0},
              {"skill": "sk-thin", "direction": "DOWN", "low_n": True, "recent_pct": 0.0, "prior_pct": 50.0},
              {"skill": "sk-flat", "direction": "FLAT", "low_n": False, "recent_pct": 50.0, "prior_pct": 50.0}]
    rows = eval_history.attention_rows(ladder=ladder, floor_cells=families, candidates=candidates, skill_trends=trends)
    signals = [row["signal"] for row in rows]
    S.check(signals == ["ladder proposal", "ladder proposal", "effort floor moved", "over-pin candidate", "skill drift"],
            "11: attention rows come as ladder proposals, effort floor, over-pin, skill drift, in that order", repr(signals))
    S.check("m-bad" in rows[0]["evidence"] and "m-cand" in rows[1]["evidence"] and rows[0]["tab"] == "ladder",
            "11: a contradicting cell precedes a candidate cell; holds and insufficient make no row", repr(rows[:2]))
    S.check(rows[2]["evidence"].startswith("review on m-a at high: 2 defects or rework in 8 rated agents")
            and rows[2]["tab"] == "agents",
            "11: the effort floor needs 2 defects or rework AND 8 rated rows in one family on one model at one effort, "
            "and its evidence names all three", repr(rows[2]))
    S.check("sk-down" in rows[4]["evidence"] and "50" in rows[4]["evidence"] and "20" in rows[4]["evidence"]
            and rows[4]["tab"] == "sessions",
            "11: skill drift lists only a DOWN trend that is not low N, with both rates", repr(rows[4]))
    S.check(all(set(row) == {"signal", "evidence", "tab"} for row in rows),
            "11: every row is exactly {signal, evidence, tab}")
    S.check(eval_history.attention_rows(ladder=[], floor_cells={}, candidates=[]) == [],
            "11: no evidence gives no rows, and skill_trends defaults to none")


def t12_verdicts_and_closing_rows():
    verdict = {"month": "2026-08", "state": "August held.", "moves": [], "watch": []}
    root = plant()
    eval_history.publish(root, facts(snap("2026-08", n=10)), verdict, "2026-08-20")
    eval_history.publish(root, facts(snap("2026-08", n=12), snap("2026-09")), None, "2026-09-02")
    S.check(load(root)["2026-08"]["verdict"] == verdict and load(root)["2026-08"]["closed"] is True,
            "12: closing a month keeps the verdict stored while it was open")
    eval_history.publish(root, facts(snap("2026-08", n=13), snap("2026-09")), None, "2026-09-03", refreeze=("2026-08",))
    S.check(load(root)["2026-08"]["verdict"] == verdict and load(root)["2026-08"]["sessions"]["n"] == 13,
            "12: --refreeze rewrites the counts and keeps the closed month's verdict")
    drift = {"signal": "skill drift", "evidence": "testing: clean 50% to 17%", "tab": "sessions"}
    floor = {"signal": "effort floor moved", "evidence": "medium: 3 defects or rework in 10 rated agents", "tab": "agents"}
    open_month = dict(snap("2026-08", n=10), attention=[floor, drift])
    root2 = plant()
    eval_history.publish(root2, facts(open_month), None, "2026-08-20")
    eval_history.publish(root2, facts(dict(snap("2026-08", n=12), attention=[floor]), snap("2026-09")), None, "2026-09-02")
    S.check(load(root2)["2026-08"]["attention"] == [floor, drift],
            "12: closing keeps the skill-drift rows recorded while the month was open, which a closing recompute cannot make",
            repr(load(root2)["2026-08"]["attention"]))
    eval_history.publish(root2, facts(dict(snap("2026-08", n=14), attention=[floor]), snap("2026-09")), None,
                         "2026-09-03", refreeze=("2026-08",))
    S.check(load(root2)["2026-08"]["attention"] == [floor, drift] and load(root2)["2026-08"]["sessions"]["n"] == 14,
            "12: --refreeze keeps the stored skill-drift rows too")
    open_now = dict(snap("2026-09", n=10), attention=[floor, drift])
    root3 = plant()
    eval_history.publish(root3, facts(open_now), None, "2026-09-10")
    eval_history.publish(root3, facts(dict(snap("2026-09", n=11), attention=[floor])), None, "2026-09-11")
    S.check(load(root3)["2026-09"]["attention"] == [floor],
            "12: an open month follows its recompute, so a skill that recovered leaves the list")


def t13_rule_edges():
    def rows(floor_cells):
        return eval_history.attention_rows(ladder=[], floor_cells=floor_cells, candidates=[])

    def eff(clean, defects):
        return {"rated": clean + defects, "falsified": defects}

    S.check(rows({"f": {"m": {"e": eff(5, 2)}}}) == [], "13: 7 rated agents with 2 falsified do not move the floor")
    S.check(rows({"f": {"m": {"e": eff(7, 1)}}}) == [], "13: 8 rated agents with 1 falsified do not move the floor")
    S.check(len(rows({"f": {"m": {"e": eff(6, 2)}}})) == 1, "13: 8 rated agents with 2 falsified move the floor")
    S.check(rows({"f": {"m": {"e": eff(6, 1)}}, "g": {"m": {"e": eff(6, 1)}}}) == [],
            "13: one falsification in each of two families at one effort does not move either floor")
    S.check(rows({"f": {"m": {"e": eff(3, 1)}, "n": {"e": eff(3, 1)}}}) == [],
            "13: one falsification on each of two models in one family and effort does not move the floor: the cell is "
            "family, model and effort together")
    S.check(len(rows({"f": {"m": {"e": eff(6, 2)}, "n": {"e": eff(6, 2)}}})) == 2,
            "13: two models that each meet the rule make one attention row each")
    unknown = stored(snap("2026-09", n=10, clean=4, unknown=2, failure=1), closed=False)
    clean = eval_history.point(unknown, "clean")
    fail = eval_history.point(unknown, "failure")
    S.check(clean["n"] == 8 and clean["value"] == 50.0 and fail["n"] == 8 and fail["value"] == 12.5,
            "13: session rates divide by the entries with a known outcome, not every entry",
            repr((clean, fail)))
    rated_open = stored(snap("2026-09", unrated=2, sampled=2, eff={"clean": 4}), closed=False)
    unrated = eval_history.point(rated_open, "unrated")
    S.check(unrated["n"] == 6 and round(unrated["value"], 2) == 33.33,
            "13: the unrated rate divides by the rows that were open to rating, sampled-out rows excluded", repr(unrated))
    months = {"2026-08": stored(snap("2026-08", n=5, clean=2)),
              "2026-09": stored(snap("2026-09", n=20, clean=10), closed=False)}
    low = row_of(eval_history.compare(months, "2026-09-28"), "clean")
    S.check(low["marker"] == "low N", "13: a prior month below the minimum N prints low N whatever the current n",
            repr(low["marker"]))


def t14_refused_shapes():
    good = stored(snap("2026-08"))
    bad_months = {"a month that is not an object": {"2026-08": 5},
                  "a month without its counts": {"2026-08": {"month": "2026-08"}},
                  "a month keyed under another name": {"2026-07": good},
                  "a sessions block of the wrong type": {"2026-08": dict(good, sessions=[1])},
                  "an attention list of the wrong type": {"2026-08": dict(good, attention="x")}}
    for name, months in bad_months.items():
        root = plant(months)
        before = raw(root)
        try:
            eval_history.publish(root, facts(snap("2026-09")), None, "2026-09-10")
            refused = False
        except eval_history.HistoryRefusedError:
            refused = True
        S.check(refused and raw(root) == before, "14: %s is refused and left untouched" % name)
    root, vault = td.plant_root(), tempfile.mkdtemp(prefix="evalhist_vault_")
    with open(eval_history.history_path(root), "w", encoding="utf-8") as handle:
        json.dump({"schema": eval_history.SCHEMA, "months": {"2026-08": 5}}, handle)
    result = td.run(root, vault)
    S.check(result.returncode == 4 and os.path.isfile(os.path.join(vault, "Self Evaluation Dashboard.md")),
            "14: a stored month of the wrong shape exits 4 with the note written, not 1 'could not write'",
            "rc=%s %s" % (result.returncode, result.stdout[-200:]))
    root_b = plant({"2026-09": stored(snap("2026-09"), closed=False)})
    real = eval_history._merged

    def boom(old, new):
        raise ValueError("planted updater bug")

    eval_history._merged = boom
    try:
        try:
            eval_history.publish(root_b, facts(snap("2026-09", n=11)), None, "2026-09-11")
            surfaced = None
        except eval_history.HistoryWriteError:
            surfaced = "write"
        except ValueError as exc:
            surfaced = str(exc)
    finally:
        eval_history._merged = real
    S.check(surfaced == "planted updater bug", "14: a bug inside the updater surfaces as itself, not as a write failure",
            repr(surfaced))


def t15_undated_counts_only_the_open_month():
    agents = {"rows": 9, "undated": 4, "first": "2026-09-01", "last": "2026-09-20"}
    S.check(eval_history.orchestration_source(agents, open_month=True)["undated"] == 4
            and eval_history.orchestration_source(agents, open_month=False)["undated"] is None,
            "15: the archive-wide undated count is stored on the open month and null on any other")
    open_snap = snap("2026-08", n=10)
    open_snap["sources"]["orchestration"]["undated"] = 7
    root = plant()
    eval_history.publish(root, facts(open_snap), None, "2026-08-20")
    S.check(load(root)["2026-08"]["sources"]["orchestration"]["undated"] == 7, "15: an open month keeps the count")
    eval_history.publish(root, facts(snap("2026-08", n=10), snap("2026-09")), None, "2026-09-02")
    sealed = load(root)["2026-08"]
    S.check(sealed["sources"]["orchestration"]["undated"] is None,
            "15: closing a month nulls the count, which no month can own", repr(sealed["sources"]["orchestration"]))
    text = eval_render.month_note(dict(sealed, month="2026-08"), None)
    S.check("undated" not in text, "15: a month note with no count prints no undated clause")
    back = plant()
    eval_history.publish(back, facts(open_snap, snap("2026-09")), None, "2026-09-02")
    S.check(load(back)["2026-08"]["sources"]["orchestration"]["undated"] is None and load(back)["2026-08"]["backfilled"],
            "15: a backfilled month carries no count")


MIGRATED = "migrated"
FLOOR_NEW = {"signal": "effort floor moved", "evidence": "review on m-one at medium: 2 defects or rework in 9 rated agents",
             "tab": "agents"}
FLOOR_OLD = {"signal": "effort floor moved", "evidence": "review at medium: 2 defects or rework in 9 rated agents",
             "tab": "agents"}
DRIFT = {"signal": "skill drift", "evidence": "testing: clean 50% to 17%", "tab": "sessions"}
DRIFT_NEW = {"signal": "skill drift", "evidence": "testing: clean 50% to 10%", "tab": "sessions"}
VERDICT_AUG = {"month": "2026-08", "state": "August held.", "moves": [], "watch": []}
RULES_AUG = {"fired": 3, "malformed": 0, "undecidable": 1}


def t16_migration():
    old_aug = dict(legacy_snap("2026-08", n=10), closed=True, backfilled=False, verdict=VERDICT_AUG, rules=RULES_AUG,
                   attention=[FLOOR_OLD, DRIFT], ladder=[cell("m", 7)])
    old_jun = dict(legacy_snap("2026-06", n=5), closed=True, backfilled=True, verdict=None, rules=None, attention=[])
    old_mar = dict(legacy_snap("2026-03", n=7, unrated=2), closed=True, backfilled=True, verdict=None, rules=None,
                   attention=[])
    old_sep = dict(legacy_snap("2026-09", n=10), closed=False, backfilled=False, verdict=VERDICT, attention=[FLOOR_OLD, DRIFT])
    root = plant({"2026-03": old_mar, "2026-06": old_jun, "2026-08": old_aug, "2026-09": old_sep}, schema=1)
    new_aug = dict(snap("2026-08", n=12, eff={"clean": 7, "defects": 2, "qcost": 900.0, "qcost_n": 9}, side=(2.0, 2, 2),
                        ladder=[cell("m", 9)]), attention=[FLOOR_NEW])
    new_jun = snap("2026-06", n=5, eff={"clean": 4, "qcost": 40.0, "qcost_n": 4})
    new_sep = dict(snap("2026-09", n=11, eff={"clean": 5, "qcost": 50.0, "qcost_n": 5}), attention=[FLOOR_NEW, DRIFT_NEW])
    built = facts(new_jun, new_aug, new_sep, rules={"fired": 9, "malformed": 9, "undecidable": 9})
    result = eval_history.publish(root, built, None, "2026-09-28")
    months = load(root)
    with open(eval_history.history_path(root), encoding="utf-8") as handle:
        schema = json.load(handle)["schema"]
    S.check(schema == eval_history.SCHEMA and eval_history.SCHEMA == 3
            and all(set(m["agents"]) - {"paid"} == {"rows", "rated", "unrated", "sampled_out", "absent", "unparseable", "cells"}
                    for m in months.values()),
            "16: a schema 1 history is rewritten as the current schema and no month keeps by_effort or sidecar",
            repr((schema, [sorted(m["agents"]) for m in months.values()][:1])))
    S.check(result["actions"] == {m: MIGRATED for m in ("2026-03", "2026-06", "2026-08", "2026-09")},
            "16: every stored month is reported migrated", repr(result["actions"]))
    aug, jun, sep = months["2026-08"], months["2026-06"], months["2026-09"]
    S.check(aug["sessions"]["n"] == 12 and aug["agents"]["cells"] == new_aug["agents"]["cells"]
            and jun["agents"]["cells"] == new_jun["agents"]["cells"] and sep["sessions"]["n"] == 11
            and aug["sources"]["orchestration"]["rows"] == new_aug["agents"]["rows"],
            "16: each month with a live source is rebuilt from it, sessions and agents alike")
    S.check((aug["closed"], aug["backfilled"], aug["verdict"]) == (True, False, VERDICT_AUG)
            and (jun["closed"], jun["backfilled"], jun["verdict"]) == (True, True, None)
            and (sep["closed"], sep["backfilled"], sep["verdict"]) == (False, False, VERDICT),
            "16: each month keeps its stored closed and backfilled flags and its stored verdict",
            repr([(m["closed"], m["backfilled"]) for m in (aug, jun, sep)]))
    S.check(aug["attention"] == [FLOOR_NEW, DRIFT] and aug["rules"] == RULES_AUG and jun["ladder"] == [] and jun["rules"] is None,
            "16: a closed month keeps its stored skill-drift rows and its rules block; its effort-floor rows are the rebuilt ones",
            repr(aug["attention"]))
    S.check(sep["attention"] == [FLOOR_NEW, DRIFT_NEW] and "rules" not in sep,
            "16: the open month follows its recompute, as on any run", repr(sep["attention"]))
    mar = months["2026-03"]
    S.check(mar["agents"] == {"rows": 2, "rated": 0, "unrated": 2, "sampled_out": 0, "absent": 0, "unparseable": 0, "cells": []}
            and mar["closed"] is True and mar["backfilled"] is True and mar["sessions"]["causes"] is None,
            "16: a stored month with no live source keeps its counts and stores no cells or causes: nothing can rebuild them",
            repr(mar["agents"]))
    before = raw(root)
    again = eval_history.publish(root, built, None, "2026-09-28")
    S.check(raw(root) == before and MIGRATED not in again["actions"].values(),
            "16: a second run over the same sources writes the same bytes and migrates nothing", repr(again["actions"]))
    refused = {}
    for name, payload in {"an unknown schema": {"schema": 9, "months": {"2026-08": old_aug}},
                          "a schema 1 month that is not an object": {"schema": 1, "months": {"2026-08": 5}},
                          "a schema 1 month with neither agents shape": {"schema": 1, "months": {
                              "2026-08": dict(old_aug, agents={"rows": 1, "rated": 1, "unrated": 0, "sampled_out": 0,
                                                               "absent": 0, "unparseable": 0})}},
                          "a schema 2 month still in the schema 1 agents shape": {"schema": 2, "months": {"2026-08": old_aug}}
                          }.items():
        bad = plant()
        with open(eval_history.history_path(bad), "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle)
        frozen = raw(bad)
        try:
            eval_history.publish(bad, built, None, "2026-09-28")
            refused[name] = False
        except eval_history.HistoryRefusedError:
            refused[name] = raw(bad) == frozen
    S.check(all(refused.values()) and len(refused) == 4,
            "16: an unknown schema, or a stored month that fits neither agents shape, is refused with the file untouched",
            repr(refused))


def t17_cli_migration():
    root, vault = td.plant_root(), tempfile.mkdtemp(prefix="evalhist_vault_")
    aug = dict(legacy_snap("2026-08", n=3), closed=True, backfilled=False, verdict=VERDICT_AUG, rules=RULES_AUG,
               attention=[DRIFT], ladder=[])
    sep = dict(legacy_snap("2026-09", n=5), closed=False, backfilled=False, verdict=VERDICT, attention=[])
    with open(eval_history.history_path(root), "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"schema": 1, "months": {"2026-08": aug, "2026-09": sep}}, handle)
    first = td.run(root, vault)
    months = load(root)
    S.check(first.returncode == 0 and "2026-08 migrated" in first.stdout and "2026-09 migrated" in first.stdout
            and "2026-07 backfilled" in first.stdout,
            "17: the CLI migrates a schema 1 history in place, exits 0 and says which months it rebuilt",
            "rc=%s %s" % (first.returncode, (first.stdout + first.stderr)[-300:]))
    S.check(months["2026-09"]["verdict"] == VERDICT and months["2026-08"]["verdict"] == VERDICT_AUG
            and months["2026-08"]["attention"][-1] == DRIFT
            and all("cells" in m["agents"] and "by_effort" not in m["agents"] for m in months.values()),
            "17: the migrated file keeps the stored verdicts and skill-drift rows and holds the new agents shape")
    with open(eval_history.history_path(root), encoding="utf-8") as handle:
        schema = json.load(handle)["schema"]
    bytes_after = raw(root)
    second = td.run(root, vault)
    S.check(schema == eval_history.SCHEMA and second.returncode == 0 and raw(root) == bytes_after,
            "17: a second CLI run over the migrated file changes nothing", "rc=%s" % second.returncode)


def t18_schema2_migration():
    old_aug = dict(schema2_snap("2026-08", n=10, eff={"clean": 5, "defects": 1, "qcost": 90.0, "qcost_n": 6}), closed=True,
                   backfilled=False, verdict=VERDICT_AUG, rules=RULES_AUG, attention=[FLOOR_OLD, DRIFT], ladder=[cell("m", 7)])
    old_mar = dict(schema2_snap("2026-03", n=7, unrated=2), closed=True, backfilled=True, verdict=None, rules=None,
                   attention=[])
    old_sep = dict(schema2_snap("2026-09", n=10, eff={"clean": 4, "qcost": 40.0, "qcost_n": 4}), closed=False,
                   backfilled=False, verdict=VERDICT, attention=[FLOOR_OLD])
    root = plant({"2026-03": old_mar, "2026-08": old_aug, "2026-09": old_sep}, schema=2)
    new_aug = dict(snap("2026-08", n=12, eff={"clean": 7, "defects": 2, "qcost": 900.0, "qcost_n": 9},
                        causes={"A": 3, "B": 1, "C": 0, "D": 0, "E": 0, "none": 0}, paid=(4.5, 3)), attention=[FLOOR_NEW])
    new_sep = snap("2026-09", n=6, eff={"clean": 3, "qcost": 30.0, "qcost_n": 3})
    built = facts(new_aug, new_sep)
    result = eval_history.publish(root, built, None, "2026-09-28")
    months = load(root)
    with open(eval_history.history_path(root), encoding="utf-8") as handle:
        schema = json.load(handle)["schema"]
    aug, mar, sep = months["2026-08"], months["2026-03"], months["2026-09"]
    S.check(schema == 3 and result["actions"] == {m: MIGRATED for m in ("2026-03", "2026-08", "2026-09")},
            "18: a schema 2 history is rewritten as schema 3 and every stored month is reported migrated",
            repr((schema, result["actions"])))
    S.check(aug["agents"]["cells"] == new_aug["agents"]["cells"] and aug["agents"]["paid"] == {"usd": 4.5, "n": 3}
            and aug["sessions"]["causes"] == {"A": 3, "B": 1, "C": 0, "D": 0, "E": 0, "none": 0}
            and all("other" in c for c in aug["agents"]["cells"])
            and (aug["closed"], aug["verdict"], aug["rules"]) == (True, VERDICT_AUG, RULES_AUG),
            "18: a month with a live source is rebuilt with `other` in every cell, its causes and its paid spend, and keeps "
            "its verdict and rules", repr(aug["agents"]))
    S.check(mar["agents"]["cells"] == [] and mar["sessions"]["causes"] is None and mar["sessions"]["n"] == 7,
            "18: a month with no live source keeps its counts and has no cells and no causes")
    S.check(sep["sessions"]["n"] == 10 and sep["sessions"]["causes"] is None,
            "18: an open month whose live source rotated below the stored count keeps the stored sessions, marked with no causes",
            repr(sep["sessions"].get("causes", "absent")))
    before = raw(root)
    again = eval_history.publish(root, built, None, "2026-09-28")
    S.check(raw(root) == before and MIGRATED not in again["actions"].values(),
            "18: a second run over the same sources writes the same bytes and migrates nothing", repr(again["actions"]))
    no_causes = dict(snap("2026-08"), sessions={k: v for k, v in snap("2026-08")["sessions"].items() if k != "causes"})
    bad_shapes = {"a schema 3 cell with no `other`": {"schema": 3, "months": {"2026-08": stored(schema2_snap("2026-08", eff={"clean": 2}))}},
                  "a schema 3 month with no causes key": {"schema": 3, "months": {"2026-08": stored(no_causes)}}}
    refused = {}
    for name, payload in bad_shapes.items():
        bad = plant()
        with open(eval_history.history_path(bad), "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle)
        frozen = raw(bad)
        try:
            eval_history.publish(bad, built, None, "2026-09-28")
            refused[name] = False
        except eval_history.HistoryRefusedError:
            refused[name] = raw(bad) == frozen
    S.check(all(refused.values()) and len(refused) == 2,
            "18: a current-schema month missing `other` or `causes` is refused with the file untouched", repr(refused))


def t19_dispatch_facts():
    def cells_of(*cells):
        return dict(snap("2026-09"), agents=dict(snap("2026-09")["agents"], cells=list(cells)))

    hit = agent_cell("m-hit", "high", wf=8, clean=6, defects=1, rework=1)
    sparse = agent_cell("m-thin", "high", wf=7, clean=5, defects=2)
    single = agent_cell("m-one", "high", wf=8, clean=7, defects=1)
    spec = agent_cell("m-spec", "low", wf=8, clean=5, discarded=3)
    worse = agent_cell("m-worse", "medium", wf=10, clean=4, defects=4, rework=1, discarded=1)
    got = eval_history.problem_cells(cells_of(single, hit, sparse, spec, worse))
    S.check([(c["model"], c["effort"], c["falsified"], c["rated"]) for c in got]
            == [("m-worse", "medium", 5, 10), ("m-hit", "high", 2, 8)],
            "19: a problem cell has 2 or more defects or rework among 8 or more rated agents: 7 rated, 1 falsified and "
            "discarded-only cells are not; the worst share comes first", repr(got))
    S.check(eval_history.FLOOR_MIN_ROWS == 8 and eval_history.FLOOR_MIN_FALSIFIED == 2
            and eval_history.problem_cells(cells_of(agent_cell("m", "low", wf=20, unrated=12, clean=6, defects=2))) != []
            and eval_history.problem_cells(cells_of(agent_cell("m", "low", wf=20, unrated=13, clean=5, defects=2))) == [],
            "19: the thresholds are the floor rule's constants, and unrated agents are not rated")
    S.check(eval_history.problem_cells(cells_of()) == [], "19: a month with no cell has no problem cell")
    facts_in = {"rows": 12, "coverage": {"total": dict.fromkeys(eval_history.COVERAGE_BUCKETS, 3)},
                "cells": [dict(agent_cell("m", "low", wf=12, clean=12), qcost=1.23456789, usd=0.5)],
                "paid": {"usd": 2.123456789, "n": 4}}
    block = eval_history.agents_block(facts_in)
    S.check(block["paid"] == {"usd": 2.123457, "n": 4} and "other" in block["cells"][0] and "other" in eval_history.CELL_KEYS,
            "19: the agents block stores the pay-per-token spend and every cell's `other` count", repr(block["paid"]))
    with_paid = dict(snap("2026-09"), agents=dict(snap("2026-09")["agents"], paid={"usd": 6.5, "n": 134}))
    S.check(eval_history.paid_spend(with_paid) == (6.5, 134) and eval_history.paid_spend(snap("2026-09")) is None
            and eval_history.paid_spend({"agents": {"cells": []}}) is None,
            "19: paid_spend is the stored spend and run count, or None when the month has no priced pay-per-token run")


def t20_corrections_per_prompt():
    """S3 (H1/D2): a month's corrections per 100 owner prompts read from its stored sums, beside the clean
    share. The sums are optional keys (schema stays 3): a month stored before they existed, or one whose
    rows carried no `facts`, reads absent, never a zero rate."""
    shot = snap("2026-09", n=10)
    shot["sessions"].update({"corrections": 12, "prompts": 150, "facts_rows": 6})
    S.check(eval_history.corrections_per_prompt(shot) == {"value": 8.0, "count": 12, "prompts": 150, "sessions": 6},
            "20: the rate divides the stored corrections by the stored owner prompts", repr(eval_history.corrections_per_prompt(shot)))
    S.check(eval_history.corrections_per_prompt(snap("2026-08")) is None
            and eval_history.corrections_per_prompt(stored(snap("2026-08"))) is None
            and eval_history.corrections_per_prompt(None) is None and eval_history.corrections_per_prompt({}) is None,
            "20: a month with no stored sums reads absent, never a zero rate")
    empty = snap("2026-09")
    empty["sessions"].update({"corrections": 0, "prompts": 0, "facts_rows": 0})
    S.check(eval_history.corrections_per_prompt(empty) is None,
            "20: a month whose rows recorded no owner prompts has no rate")
    root = plant({"2026-08": stored(snap("2026-08")), "2026-09": stored(snap("2026-09"), closed=False)})
    view = eval_history.compare(load(root), "2026-09-15")
    S.check(len(view["rows"]) == len(eval_history.METRICS)
            and eval_history.corrections_per_prompt(load(root)["2026-08"]) is None,
            "20: a stored month written before the keys existed still loads, compares and reads absent")
    fresh = plant()
    month = snap("2026-09", n=10)
    month["sessions"].update({"corrections": 5, "prompts": 100, "facts_rows": 3})
    eval_history.publish(fresh, facts(month), None, "2026-09-10")
    stored_snapshot = load(fresh)["2026-09"]
    S.check(stored_snapshot["sessions"]["corrections"] == 5 and stored_snapshot["sessions"]["prompts"] == 100
            and stored_snapshot["sessions"]["facts_rows"] == 3
            and eval_history.corrections_per_prompt(stored_snapshot)["value"] == 5.0,
            "20: the optional sums survive a publish, and the rate reads them back", repr(stored_snapshot["sessions"]))


def main():
    for name, fn in (("t1", t1_idempotence), ("t2", t2_closing), ("t3", t3_backfill), ("t4", t4_compare),
                     ("t5", t5_sparkline), ("t6", t6_month_notes), ("t7", t7_refused), ("t8", t8_size),
                     ("t9", t9_rules), ("t10", t10_cli_refreeze), ("t11", t11_attention_rows),
                     ("t12", t12_verdicts_and_closing_rows), ("t13", t13_rule_edges), ("t14", t14_refused_shapes),
                     ("t15", t15_undated_counts_only_the_open_month), ("t16", t16_migration),
                     ("t17", t17_cli_migration), ("t18", t18_schema2_migration), ("t19", t19_dispatch_facts),
                     ("t20", t20_corrections_per_prompt)):
        S.guard(name, fn)
    print("\n%d/%d cases pass" % (S.count - len(S.failures), S.count))
    for line in S.failures:
        print("  FAIL " + line)
    return 1 if S.failures else 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--publish":
        sys.exit(child(sys.argv[2:]))
    sys.exit(main())
