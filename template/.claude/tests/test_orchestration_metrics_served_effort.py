"""Proof: orchestration_metrics reads the effort each agent transcript RECORDS (top-level `effort` on
assistant records) instead of printing observed_effort None, and reports drift from the requested pin."""
import json
import os
import sys
import tempfile

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)
import orchestration_metrics as om


def plant(efforts, pins_effort="high", model="opus", served="claude-opus-5-5"):
    session = tempfile.mkdtemp(prefix="om_effort_")
    rid = "wf_eff-1"
    run_dir = os.path.join(session, "subagents", "workflows", rid)
    os.makedirs(run_dir)
    os.makedirs(os.path.join(session, "workflows"))
    with open(os.path.join(run_dir, "agent-x1.jsonl"), "w", encoding="utf-8") as fh:
        for i, eff in enumerate(efforts):
            rec = {"type": "assistant", "uuid": "u%d" % i, "timestamp": "2026-09-22T10:00:0%dZ" % i,
                   "message": {"id": "m%d" % i, "model": served, "usage": {}, "content": []}}
            if eff is not None:
                rec["effort"] = eff
            fh.write(json.dumps(rec) + "\n")
    journal = {"runId": rid, "status": "completed", "workflowName": "p", "script": "",
               "logs": ['PINS {"lens": "%s/%s"}' % (model, pins_effort)],
               "workflowProgress": [{"type": "workflow_agent", "label": "lens", "agentId": "x1", "model": model,
                                     "state": "done", "startedAt": 1790000000000}]}
    json.dump(journal, open(os.path.join(session, "workflows", rid + ".json"), "w"))
    return session, rid


def test_served_effort_single_value():
    session, _ = plant(["xhigh", "xhigh"])
    usage = om.agent_usage(os.path.join(session, "subagents", "workflows", "wf_eff-1"))
    assert usage["x1"]["served_effort"] == "xhigh", usage


def test_served_effort_mixed_and_absent():
    session, _ = plant(["low", "high"])
    assert om.agent_usage(os.path.join(session, "subagents", "workflows", "wf_eff-1"))["x1"]["served_effort"] == "mixed:high,low"
    session, _ = plant([None])
    assert om.agent_usage(os.path.join(session, "subagents", "workflows", "wf_eff-1"))["x1"]["served_effort"] is None


def test_collect_fills_observed_effort_and_flags_drift():
    session, rid = plant(["xhigh"])
    rows = om.collect(session)
    assert rows and rows[0]["observed_effort"] == "xhigh" and rows[0]["requested_effort"] == "high", rows
    lines = om.model_mismatches(rows)
    assert any(l.startswith("EFFORT MISMATCH") and "requested high" in l and "xhigh" in l for l in lines), lines


def test_run_rows_matches_collect():
    session, rid = plant(["high"])
    assert om.run_rows(session, rid) == om.collect(session)


def test_matching_effort_is_not_flagged():
    session, _ = plant(["high"])
    assert om.model_mismatches(om.collect(session)) == []


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
