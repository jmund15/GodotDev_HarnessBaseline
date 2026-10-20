"""Proof: review-panel width reaches the metrics loop (orchestration §2 *Sizing the width*).

A merged seat records one verdict row with a per-mandate block. The yield line names a mandate
with zero unique findings across its last 5 reached merged-seat rows, a later unique finding
clears it, and a `not-reached` mandate is reported UNCOVERED instead of counting as zero.
`--run <runId>` prints per-seat usage with the merge agent on its own row. A review seat row joins
the review ledger: joined rows carry the five yield fields, and an absent or unreadable join says so.

    python3 .claude/tests/test_orchestration_metrics_panel_yield.py
"""
import contextlib
import io
import json
import atexit
import os
import shutil
import sys
import tempfile

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)
import orchestration_metrics as om  # noqa: E402

SCRATCH = tempfile.mkdtemp(prefix="om_scratch_")
atexit.register(shutil.rmtree, SCRATCH, True)

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


def test_single_mandate_seats_count():
    # S42: "Single-mandate rows, uncovered entries beyond ten, and several report modes omit the promised signals."
    rows = [{"run": "r%d" % i, "label": "review:x", "mandates": {KEY: {"outcome": "clean", "unique": 0}}}
            for i in range(6)]
    assert om.panel_yield(rows) == ([KEY], []), om.panel_yield(rows)


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
    rejected = om.apply_verdict(row, {"outcome": "clean", "effort": "high", "tier": "validation", "tokens": 1,
                                      "mandates": {KEY: {"outcome": "clean", "unique": 0, "duplicate": 0}}})
    assert row["outcome"] == "clean" and row["effort"] == "high", row
    assert row["tier"] == "fanout" and KEY in row["mandates"] and rejected is None, (row, rejected)


def test_dict_verdict_refuses_a_tier_that_is_not_a_role_tier():
    for legacy in ("Standard", "Wide", "gpt-5", 3):
        row = {"label": "review:grounding", "effort": "?"}
        rejected = om.apply_verdict(row, {"outcome": "clean", "tier": legacy})
        assert "tier" not in row and rejected == legacy and row["outcome"] == "clean", (legacy, row, rejected)


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


def plant_run(merge_label="review:merge", merge_phase="Merge"):
    session = tempfile.mkdtemp(prefix="om_panel_", dir=SCRATCH)
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
        {"type": "workflow_agent", "label": merge_label, "agentId": "c1", "model": "opus",
         "phase": merge_phase, "phaseTitle": merge_phase, "state": "done"},
    ]
    journal = {"runId": rid, "workflowName": "review-fanout", "script": "",
               "logs": ['PINS {"review:plc-memory-alignment+plc-evidence-grounding": "opus/high", '
                        '"review:plc-instruction-quality": "opus/high", "%s": "opus/low"}' % merge_label],
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


def test_run_prints_per_seat_usage_and_merge_row():
    session, rid = plant_run()
    code, text = run_main(["--session", session, "--run", rid])
    assert code == 0, text
    seat_line = next(l for l in text.splitlines() if "plc-memory-alignment+plc-evidence-grounding" in l)
    # input-equivalent = 10 + 100*5 + 1000*1.25 + 20000*0.1 = 3760
    assert "1.0k" in seat_line and "20.0k" in seat_line and "3.8k" in seat_line, seat_line
    lines = text.splitlines()
    merge = [i for i, l in enumerate(lines) if "review:merge" in l]
    seats_total = [i for i, l in enumerate(lines) if l.startswith("seats:")]
    assert merge and seats_total and merge[0] > seats_total[0], text
    assert " merge " in lines[merge[0]], lines[merge[0]]


def test_run_finds_the_merge_row_by_label_when_phase_is_missing():
    for label in ("review:merge", "review:consolidate"):  # archived runs carry the old label
        session, rid = plant_run(merge_label=label, merge_phase=None)
        code, text = run_main(["--session", session, "--run", rid])
        assert code == 0, text
        seats_total = text.index("seats:")
        assert text.index(label) > seats_total and "seats: 2 " in text, (label, text)


def test_unknown_run_is_refused():
    session, _ = plant_run()
    code, text = run_main(["--session", session, "--run", "wf_missing"])
    assert code == 1 and "wf_missing" in text, text



def _finding(desc, critical=None, file=None):
    f = {"agent": "a", "action": "FIX", "category": "bug", "description": desc, "rationale": "r"}
    if file:
        f["file"] = file
    if critical is not None:
        f["critical"] = critical
    return f


def plant_review_run(session=None, rid="wf_seat-1", consolidate=True, disjoint=False, single_seat=False,
                     fail_merge=False):
    """Seat A finds 3 (F1 unique critical), seat B finds 2 (both merged with A), seat C finds 1 unique.
    `consolidate=False` plants a run whose engine skipped the consolidate stage."""
    session = session or tempfile.mkdtemp(prefix="om_seat_", dir=SCRATCH)
    run_dir = os.path.join(session, "subagents", "workflows", rid)
    os.makedirs(run_dir)
    os.makedirs(os.path.join(session, "workflows"), exist_ok=True)
    seats = [("review:A", "aA", [_finding("a1", True), _finding("a2", file="src/shared-a.py:1"),
                                  _finding("a3", file="src/shared-b.py:1")]),
             ("review:B", "aB", [_finding("b1", file="src/shared-a.py:2"),
                                  _finding("b2", False, file="src/shared-b.py:2")]),
             ("review:C", "aC", [_finding("c1")])]
    if single_seat:
        seats = seats[:1]
    elif disjoint:
        seats[1][2][0]["file"] = "src/only-b1.py:1"
        seats[1][2][1]["file"] = "src/only-b2.py:1"
    rows = []
    for label, aid, findings in seats:
        rows.append({"type": "started", "agentId": aid, "key": "k" + aid, "label": label, "phase": "Review"})
    for label, aid, findings in seats:
        rows.append({"type": "result", "agentId": aid, "key": "k" + aid, "result": {"findings": findings}})
    groups = [["F2", "F4"], ["F3", "F5"]]
    if consolidate and not (disjoint or single_seat):
        rows.append({"type": "started", "agentId": "aM", "key": "kaM", "label": "review:consolidate",
                     "phase": "Merge"})
        rows.append({"type": "failed", "agentId": "aM", "key": "kaM"} if fail_merge else
                    {"type": "result", "agentId": "aM", "key": "kaM",
                     "result": {"findings": [{"merged_from": g} for g in groups]}})
    with open(os.path.join(run_dir, "journal.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
    with open(os.path.join(session, "workflows", rid + ".json"), "w", encoding="utf-8") as fh:
        json.dump({"runId": rid, "workflowName": "review-fanout", "workflowProgress": [],
                   "args": {"agents": [{"key": "k" + aid} for _, aid, _ in seats],
                            "consolidate": consolidate}}, fh)
    return session, rid


def test_seat_overlap_counts_each_seat_against_the_merge_groups():
    session, rid = plant_review_run()
    got = om.seat_overlap(os.path.join(session, "subagents", "workflows", rid))
    assert "unreadable" not in got, got
    seats = {k: {f: v[f] for f in ("found", "unique", "duplicate", "unique_critical")}
             for k, v in got["seats"].items()}
    assert seats == {"A": {"found": 3, "unique": 1, "duplicate": 2, "unique_critical": 1},
                     "B": {"found": 2, "unique": 0, "duplicate": 2, "unique_critical": 0},
                     "C": {"found": 1, "unique": 1, "duplicate": 0, "unique_critical": 0}}, seats
    assert (got["distinct"], got["shared"]) == (4, 2), got


def test_session_report_prints_one_seat_overlap_line_per_review_run():
    session, rid = plant_review_run()
    overlaps = om.session_seat_overlaps(session)
    assert list(overlaps) == [rid], overlaps
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        om.report_seat_overlap(overlaps)
    assert out.getvalue().strip() == ("Seat overlap %s: 3 seats, 4 distinct findings, 2 (50%%) found by "
                                      "2+ seats; zero-unique seats: B" % rid), out.getvalue()


def test_unreadable_workflow_dir_is_reported_unreadable_not_zero():
    missing = os.path.join(tempfile.mkdtemp(prefix="om_seat_", dir=SCRATCH), "wf_gone")
    got = om.seat_overlap(missing)
    assert "unreadable" in got and "seats" not in got, got
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        om.report_seat_overlap({"wf_gone": got})
    assert "Seat overlap wf_gone: unreadable" in out.getvalue(), out.getvalue()


def test_run_without_a_consolidator_is_unmeasured_not_zero_overlap():
    session, rid = plant_review_run(consolidate=False)
    got = om.seat_overlap(os.path.join(session, "subagents", "workflows", rid))
    assert "unmeasured" in got and "seats" not in got, got
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        om.report_seat_overlap({rid: got})
    assert out.getvalue().startswith("Seat overlap %s: unmeasured -- " % rid), out.getvalue()
    rows = [{"run": rid, "label": "review:A"}]
    om.attach_seat_overlap(rows, {rid: got})
    assert "seat_overlap" not in rows[0], rows


def test_failed_merge_with_candidates_remains_unmeasured():
    session, rid = plant_review_run(fail_merge=True)
    got = om.seat_overlap(os.path.join(session, "subagents", "workflows", rid))
    assert "unmeasured" in got and "seats" not in got, got


def test_skipped_merge_with_disjoint_seats_reports_zero_overlap():
    session, rid = plant_review_run(disjoint=True)
    got = om.seat_overlap(os.path.join(session, "subagents", "workflows", rid))
    assert (got.get("distinct"), got.get("shared")) == (6, 0), got
    assert all(seat["unique"] == seat["found"] for seat in got["seats"].values()), got
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        om.report_seat_overlap({rid: got})
    assert "0 (0%) found by 2+ seats" in out.getvalue(), out.getvalue()
    rows = [{"run": rid, "label": "review:A"}]
    om.attach_seat_overlap(rows, {rid: got})
    assert rows[0]["seat_overlap"]["unique"] == 3, rows


def test_skipped_merge_with_one_seat_reports_zero_overlap():
    session, rid = plant_review_run(single_seat=True)
    got = om.seat_overlap(os.path.join(session, "subagents", "workflows", rid))
    assert (got.get("distinct"), got.get("shared")) == (3, 0), got
    assert got["seats"]["A"]["unique"] == 3, got


def test_seat_blocks_attach_to_the_rows_that_archive():
    session, rid = plant_review_run()
    overlaps = om.session_seat_overlaps(session)
    rows = [{"run": rid, "label": "review:B"}, {"run": rid, "label": "review:consolidate"},
            {"run": "wf_other", "label": "review:B"}]
    om.attach_seat_overlap(rows, overlaps)
    assert rows[0]["seat_overlap"]["key"] == "B" and rows[0]["seat_overlap"]["unique"] == 0, rows[0]
    assert "seat_overlap" not in rows[1] and "seat_overlap" not in rows[2], rows


def seat_row(n, key="B", unique=0):
    return {"run": "wf_s%d" % n, "label": "review:" + key,
            "seat_overlap": {"key": key, "found": 2, "unique": unique, "duplicate": 2 - unique,
                             "unique_critical": 0}}


def _yield_text(rows):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        om.report_panel_yield(rows)
    return out.getvalue()


def test_five_zero_unique_seat_runs_flag_the_key():
    text = _yield_text([seat_row(i) for i in range(5)])
    assert ("Panel yield: B added no unique finding in its last 5 runs -- merge it into another seat "
            "next time.") in text, text


def test_four_zero_unique_seat_runs_do_not_flag():
    assert "Panel yield" not in _yield_text([seat_row(i) for i in range(4)])


def test_one_unique_seat_run_in_the_window_clears_the_flag():
    rows = [seat_row(i) for i in range(4)] + [seat_row(4, unique=1)]
    assert "Panel yield" not in _yield_text(rows)


def test_history_rows_extend_the_seat_window_across_sessions():
    history = [seat_row(i) for i in range(3)]
    current = [seat_row(i + 3) for i in range(2)] + [seat_row(4)]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        om.report_panel_yield(current, history=history)
    assert out.getvalue().count("Panel yield: B added no unique finding") == 1, out.getvalue()


REVIEWED = ".claude/tools/orchestration_metrics.py"


def ledger_row(rid, seat, mandate, **over):
    row = {"id": "%s:%s:%s" % (rid, seat, mandate), "ts": "2026-09-30T00:00:00Z", "stage": "pre-merge",
           "subject": "pr:1", "authorModel": "author-x", "reviewer": "panel", "seat": seat, "session": "S1",
           "files": {REVIEWED: "sha"}, "seatLedgerNew": 2}
    row.update(over)
    return row


def workflow_seat(rid="wf_r1", label="review:grounding", phase="Review"):
    return {"source": "workflow", "run": rid, "label": label, "workflow": "review-fanout", "phase": phase}


def sidecar_seat(label="s7-plc", shape="review", session="S1", launch="L1"):
    path = os.path.join(tempfile.mkdtemp(prefix="om_join_", dir=SCRATCH), label + ".record.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"label": label, "toolProfile": {"shape": shape}}, fh)
    return {"source": "sidecar", "workflow": "sidecar", "run": "sidecar-" + launch, "label": label,
            "session_id": session, "record_path": path}


def test_a_joined_review_seat_row_carries_the_five_yield_fields():
    project = tempfile.mkdtemp(prefix="om_join_tags_", dir=SCRATCH)
    catalog = os.path.join(project, ".claude", "skills", "project_subsystems", "adaptation.json")
    os.makedirs(os.path.dirname(catalog))
    with open(catalog, "w", encoding="utf-8") as fh:
        json.dump({"sensitivity_tags": [{"id": "fixture-tool", "paths": [".claude/tools/*.py"],
                                         "sets": {"reach": "shared"}, "source": "fixture"}]}, fh)
    rows = [workflow_seat()]
    saved = os.environ.get("CLAUDE_PROJECT_DIR")
    os.environ["CLAUDE_PROJECT_DIR"] = project
    try:
        om.attach_review_ledger(rows, [ledger_row("wf_r1", "grounding", "cq.C"),
                                       ledger_row("wf_r1", "grounding", "cq.D"),
                                       ledger_row("wf_r10", "grounding", "cq.C", authorModel="other")])
    finally:
        if saved is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = saved
    row = rows[0]
    assert row["ledger_join"] == "joined", row
    assert (row["author_model"], row["review_stage"], row["ledger_new"]) == ("author-x", "pre-merge", 2), row
    assert row["domain"] == ["fixture-tool"], row


def test_ledger_new_is_left_off_when_no_joined_row_carries_it():
    rows = [workflow_seat()]
    ledger = [ledger_row("wf_r1", "grounding", "cq.C", stage="candidate")]
    del ledger[0]["seatLedgerNew"]
    om.attach_review_ledger(rows, ledger)
    assert rows[0]["ledger_join"] == "joined" and "ledger_new" not in rows[0], rows[0]


def test_a_review_seat_with_no_ledger_rows_is_absent_and_other_rows_get_no_join():
    rows = [workflow_seat(), workflow_seat(label="review:consolidate", phase="Merge"),
            {"source": "workflow", "run": "wf_x", "label": "impl:a", "workflow": "impl"}]
    om.attach_review_ledger(rows, [ledger_row("wf_other", "grounding", "cq.C")])
    assert rows[0]["ledger_join"] == "absent" and "author_model" not in rows[0], rows[0]
    assert "ledger_join" not in rows[1] and "ledger_join" not in rows[2], rows


def test_an_unreadable_ledger_marks_every_review_seat_unreadable():
    rows = [workflow_seat(), sidecar_seat()]
    om.attach_review_ledger(rows, None)
    assert [r["ledger_join"] for r in rows] == ["unreadable", "unreadable"], rows


def test_a_sidecar_review_seat_joins_on_its_session_and_label():
    rows = [sidecar_seat(), sidecar_seat(label="impl-a", shape="author")]
    om.attach_review_ledger(rows, [ledger_row("prepared", "s7-plc", "cq.C")])
    assert rows[0]["ledger_join"] == "joined" and rows[0]["ledger_new"] == 2, rows[0]
    assert "ledger_join" not in rows[1], rows[1]


def test_a_sidecar_label_another_session_or_launch_shares_is_absent():
    other_session = [sidecar_seat(session="S2")]
    om.attach_review_ledger(other_session, [ledger_row("prepared", "s7-plc", "cq.C")])
    assert other_session[0]["ledger_join"] == "absent", other_session
    twice = [sidecar_seat(launch="L1"), sidecar_seat(launch="L2")]
    om.attach_review_ledger(twice, [ledger_row("prepared", "s7-plc", "cq.C")])
    assert [r["ledger_join"] for r in twice] == ["absent", "absent"], twice
    two_records = [sidecar_seat()]
    om.attach_review_ledger(two_records, [ledger_row("prep1", "s7-plc", "cq.C"), ledger_row("prep2", "s7-plc", "cq.C")])
    assert two_records[0]["ledger_join"] == "absent", two_records


def test_the_report_prints_one_join_count_line():
    rows = [dict(workflow_seat(), ledger_join="joined"), dict(workflow_seat(), ledger_join="absent"),
            dict(sidecar_seat(), ledger_join="unreadable"), {"label": "impl:a"}]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        om.report_review_join(rows)
    assert out.getvalue().strip() == "Review ledger join: 1 joined, 1 absent, 1 unreadable", out.getvalue()


def test_the_ledger_loader_tells_an_absent_ledger_from_an_unreadable_one():
    tmp = tempfile.mkdtemp(prefix="om_ledger_", dir=SCRATCH)
    saved = os.environ.get("HARNESS_REVIEW_LEDGER")
    try:
        os.environ["HARNESS_REVIEW_LEDGER"] = os.path.join(tmp, "missing.jsonl")
        assert om.load_review_ledger() == [], "absent ledger"
        os.environ["HARNESS_REVIEW_LEDGER"] = tmp
        assert om.load_review_ledger() is None, "a directory cannot be read"
        path = os.path.join(tmp, "ledger.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(ledger_row("wf_r1", "grounding", "cq.C")) + "\nnot json\n")
        os.environ["HARNESS_REVIEW_LEDGER"] = path
        assert [r["id"] for r in om.load_review_ledger()] == ["wf_r1:grounding:cq.C"]
    finally:
        if saved is None:
            os.environ.pop("HARNESS_REVIEW_LEDGER", None)
        else:
            os.environ["HARNESS_REVIEW_LEDGER"] = saved


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
