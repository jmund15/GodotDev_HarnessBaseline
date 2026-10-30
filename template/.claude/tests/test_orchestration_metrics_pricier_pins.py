#!/usr/bin/env python3
"""Proof for orchestration_metrics.pricier_pins: per archived dispatch, the rows claiming its tier at a lower
ladder cost. A fact for review, computed from the record and the registry, never the orchestrator's own rating.

Run: python3 .claude/tests/test_orchestration_metrics_pricier_pins.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import orchestration_metrics as om  # noqa: E402

DATA = {"transports": {"anthropic": {"costModel": "plan-quota"}, "codex": {"costModel": "plan-quota"}},
        "models": [
            {"id": "big", "alias": "big", "transport": "anthropic", "family": "big", "roles": ["architect", "fanout"]},
            {"id": "small", "alias": "small", "transport": "anthropic", "family": "small", "roles": ["fanout"]},
            {"id": "far", "alias": "far", "transport": "codex", "family": "far", "roles": ["fanout"]},
        ]}
COSTS = {"big": 5, "small": 10, "far": 7}


def report_reads_rotated_shards():
    """report_pricier_pins reads through archive_rows (so `.jsonl.1` counts) and dates rows by row_date."""
    root = tempfile.mkdtemp(prefix="pricier_rot_")
    ledger = os.path.join(root, "ledger.jsonl")
    with open(ledger, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"run": "2026-10-09T00:00:00Z-new", "label": "n", "tier": "fanout",
                                 "model": "big"}) + "\n")
    with open(ledger + ".1", "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"run": "sidecar-2026-10-09T00:00:00Z-old", "label": "old", "tier": "fanout",
                                 "model": "big"}) + "\n")
        handle.write(json.dumps({"run": "wf_ignored", "label": "noise", "ignored": True, "reason": "synthetic"}) + "\n")
        handle.write(json.dumps({"run": "wf_undated", "label": "archive-dated", "tier": "fanout", "model": "big",
                                 "date": "2026-10-09"}) + "\n")
    seen = {}
    real = om.pricier_pins

    def capture(rows, data=None, costs=None):
        seen["rows"] = list(rows)
        return real(rows, data, costs)

    om.pricier_pins = capture
    saved = om.ARCHIVE
    om.ARCHIVE = ledger
    try:
        om.report_pricier_pins(30)
    finally:
        om.ARCHIVE = saved
        om.pricier_pins = real
    labels = {r.get("label") for r in seen.get("rows", [])}
    return [("report_pricier_pins reads the rotated shard: a row in .jsonl.1 counts", "old" in labels, repr(labels)),
            ("report_pricier_pins drops an ignored row the archive reader excludes", "noise" not in labels, repr(labels)),
            ("report_pricier_pins dates rows by row_date, so one dated only by its archive date is out",
             "archive-dated" not in labels, repr(labels))]


def main():
    rows = [
        {"label": "a", "model": "big", "tier": "fanout", "date": "2026-10-09"},
        {"label": "b", "model": "small", "tier": "fanout", "date": "2026-10-09"},
        {"label": "c", "model": "big", "tier": "architect", "date": "2026-10-09"},
        {"label": "d", "model": "big", "date": "2026-10-09"},
        {"label": "e", "model": "unknown-model", "tier": "fanout", "date": "2026-10-09"},
    ]
    got = {r["label"]: r for r in om.pricier_pins(rows, DATA, costs=COSTS)}
    cases = [
        ("a fanout dispatch pinned above two cheaper fanout claimants names both, cheapest first, any transport",
         got.get("a", {}).get("cheaper") == ["small", "far"]),
        ("the cheapest claimant itself is not listed", "b" not in got),
        ("a tier with no cheaper claimant is not listed", "c" not in got),
        ("a row with no tier is not listed (no claim to compare)", "d" not in got),
        ("an unresolvable model is not listed", "e" not in got),
        ("each listed row carries its pinned model, tier and cost",
         got.get("a", {}).get("model") == "big" and got["a"].get("tier") == "fanout" and got["a"].get("cost") == 5),
    ]
    if hasattr(om, "report_pricier_pins"):
        cases += [(label, ok, detail) for label, ok, detail in report_reads_rotated_shards()]
    failed = [n for n, ok, *_ in cases if not ok]
    for case in cases:
        print(("ok   " if case[1] else "FAIL ") + case[0])
        if not case[1] and len(case) > 2:
            print("     " + case[2])
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
