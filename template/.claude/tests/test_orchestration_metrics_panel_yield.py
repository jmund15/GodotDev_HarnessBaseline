"""Proof: review-panel width reaches the metrics loop (orchestration §2 *Sizing the width*).

A merged seat records one verdict row with a per-mandate block. The yield line names a mandate
with zero unique findings across its last 5 reached merged-seat rows, a later unique finding
clears it, and a `not-reached` mandate is reported UNCOVERED instead of counting as zero.
`--run <runId>` prints per-seat usage with the consolidator on its own row.

    python3 .claude/tests/test_orchestration_metrics_panel_yield.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)
import orchestration_metrics as om  # noqa: E402

KEY = "plc-memory-alignment"
PEER = "plc-evidence-grounding"


def seat(n, key_outcome="clean", unique=0, run="wf_r"):
    return {"run": "%s%d" % (run, n), "label": "review:grounding", "outcome": "clean",
            "mandates": {KEY: {"outcome": key_outcome, "unique": unique, "duplicate": 0},
                         PEER: {"outcome": "findings", "unique": 2, "duplicate": 0}}}


def test_five_zero_unique_rows_name_the_mandate():
    zero, _ = om.panel_yield([seat(i) for i in range(5)])
    assert zero == [KEY], zero


def test_a_later_unique_finding_clears_it():
    zero, _ = om.panel_yield([seat(i) for i in range(5)] + [seat(5, "findings", 1)])
    assert zero == [], zero


def test_not_reached_is_uncovered_and_not_a_zero():
    rows = [seat(i) for i in range(4)] + [seat(4, "not-reached")]
    zero, uncovered = om.panel_yield(rows)
    assert zero == [], zero
    assert uncovered == [(KEY, "wf_r4:review:grounding")], uncovered


def test_single_mandate_seats_do_not_count():
    rows = [{"run": "r%d" % i, "label": "review:x", "mandates": {KEY: {"outcome": "clean", "unique": 0}}}
            for i in range(6)]
    assert om.panel_yield(rows) == ([], []), om.panel_yield(rows)


def test_report_prints_the_yield_line():
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        om.report_panel_yield([seat(i) for i in range(4)] + [seat(4, "not-reached")] + [seat(i + 5) for i in range(1)])
    text = out.getvalue()
    assert "UNCOVERED %s" % KEY in text, text
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        om.report_panel_yield([seat(i) for i in range(5)])
    assert "zero unique findings" in out.getvalue() and KEY in out.getvalue(), out.getvalue()


def test_dict_verdict_keeps_tier_and_mandates_on_the_row():
    row = {"label": "review:grounding", "effort": "?"}
    om.apply_verdict(row, {"outcome": "clean", "effort": "high", "tier": "Standard", "tokens": 1,
                           "mandates": {KEY: {"outcome": "clean", "unique": 0, "duplicate": 0}}})
    assert row["outcome"] == "clean" and row["effort"] == "high", row
    assert row["tier"] == "Standard" and KEY in row["mandates"], row


def test_list_and_string_verdicts_still_apply():
    row = {"label": "x", "effort": "?"}
    om.apply_verdict(row, ["clean", "low", "probe"])
    assert (row["outcome"], row["effort"], row.get("probe")) == ("clean", "low", True), row
    row = {"label": "x", "effort": "low"}
    om.apply_verdict(row, "clean")
    assert row["outcome"] == "clean" and "mandates" not in row, row
    row = {"label": "x", "effort": "low"}
    om.apply_verdict(row, None)
    assert row["outcome"] == "unrated", row


def plant_run():
    session = tempfile.mkdtemp(prefix="om_panel_")
    rid = "wf_panel-1"
    run_dir = os.path.join(session, "subagents", "workflows", rid)
    os.makedirs(run_dir)
    os.makedirs(os.path.join(session, "workflows"))
    usage = {"a1": (10, 100, 1000, 20000), "a2": (5, 50, 800, 10000), "c1": (1, 20, 300, 4000)}
    for aid, (inp, out, cw, cr) in usage.items():
        rec = {"type": "assistant", "uuid": aid, "timestamp": "2026-09-22T10:00:00Z",
               "message": {"id": "m-" + aid, "model": "claude-opus-5-5", "content": [],
                           "usage": {"input_tokens": inp, "output_tokens": out,
                                     "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr}}}
        with open(os.path.join(run_dir, "agent-%s.jsonl" % aid), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    progress = [
        {"type": "workflow_agent", "label": "review:plc-memory-alignment+plc-evidence-grounding",
         "agentId": "a1", "model": "opus", "phase": "Review", "phaseTitle": "Review", "state": "done"},
        {"type": "workflow_agent", "label": "review:plc-instruction-quality", "agentId": "a2",
         "model": "opus", "phase": "Review", "phaseTitle": "Review", "state": "done"},
        {"type": "workflow_agent", "label": "review:consolidate", "agentId": "c1", "model": "opus",
         "phase": "Merge", "phaseTitle": "Merge", "state": "done"},
    ]
    journal = {"runId": rid, "workflowName": "review-fanout", "script": "",
               "logs": ['PINS {"review:plc-memory-alignment+plc-evidence-grounding": "opus/high", '
                        '"review:plc-instruction-quality": "opus/high", "review:consolidate": "opus/low"}'],
               "workflowProgress": progress}
    with open(os.path.join(session, "workflows", rid + ".json"), "w", encoding="utf-8") as fh:
        json.dump(journal, fh)
    return session, rid


def run_main(argv):
    out = io.StringIO()
    old = sys.argv
    sys.argv = ["orchestration_metrics.py"] + argv
    try:
        with contextlib.redirect_stdout(out):
            code = om.main()
    finally:
        sys.argv = old
    return code, out.getvalue()


def test_run_prints_per_seat_usage_and_consolidator_row():
    session, rid = plant_run()
    code, text = run_main(["--session", session, "--run", rid])
    assert code == 0, text
    seat_line = next(l for l in text.splitlines() if "plc-memory-alignment+plc-evidence-grounding" in l)
    # input-equivalent = 10 + 100*5 + 1000*1.25 + 20000*0.1 = 3760
    assert "1.0k" in seat_line and "20.0k" in seat_line and "3.8k" in seat_line, seat_line
    lines = text.splitlines()
    cons = [i for i, l in enumerate(lines) if "review:consolidate" in l]
    seats_total = [i for i, l in enumerate(lines) if l.startswith("seats:")]
    assert cons and seats_total and cons[0] > seats_total[0], text
    assert "consolidator" in lines[cons[0]], lines[cons[0]]


def test_unknown_run_is_refused():
    session, _ = plant_run()
    code, text = run_main(["--session", session, "--run", "wf_missing"])
    assert code == 1 and "wf_missing" in text, text


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:  # noqa: BLE001
                fails += 1
                print("FAIL", name, "-", repr(exc)[:300])
    sys.exit(1 if fails else 0)
