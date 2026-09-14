#!/usr/bin/env python3
"""Re-runnable proof for tools/aggregate_routing_audit.py bucketing.

The load-bearing case: a `census` row (a vault doc write, direct or via write_doc) is COUNTED
under `vault_writes` by route and is NEVER a silent miss — the census measures the
direct/worker split the Documentation Delegation Rule is judged against; it does not judge.

    python3 .claude/tests/test_aggregate_routing_audit.py
"""
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "aggregate_routing_audit.py")
spec = importlib.util.spec_from_file_location("ara", TOOL)
ara = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ara)


def row(cls, rule, days_ago=1, nudge=False, agent=""):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    return {"ts": ts, "session_id": "s", "agent_id": agent, "tool": "Write",
            "classification": cls, "rule": rule, "nudge_fired": nudge}


def main():
    cases = []
    b = ara._classification_bucket
    cases.append(("census -> its own bucket", b(row("census", "vault-write-direct")) == "census"))
    cases.append(("nudge-warranted + no nudge -> silent_miss", b(row("nudge-warranted", "x")) == "silent_miss"))
    cases.append(("nudge-warranted + nudge -> nudged", b(row("nudge-warranted", "x", nudge=True)) == "nudged"))
    cases.append(("cue-exempt -> cue_exempt", b(row("cue-exempt", "x")) == "cue_exempt"))
    cases.append(("anything else -> other", b(row("compliant", None)) == "other"))

    entries = [
        row("census", "vault-write-direct"),
        row("census", "vault-write-direct", days_ago=2),
        row("census", "vault-write-worker"),
        row("nudge-warranted", "native-read-synthesis-doc"),
        row("cue-exempt", "native-read-synthesis-doc"),
        row("census", "vault-write-direct", days_ago=90),   # outside the 30-day window
    ]
    stats = ara.aggregate(entries, active_window_days=30)
    cases.append(("vault_writes counts census rows by route inside the window",
                  stats.get("vault_writes") == {"vault-write-direct": 2, "vault-write-worker": 1},
                  stats.get("vault_writes")))
    cases.append(("a census row is never a silent miss", stats["silent_misses"] == 1))
    cases.append(("cue-exempt still counts as an override", stats["cue_exempt_overrides"] == 1))
    cases.append(("the window excludes the 90-day-old row", stats["total_entries"] == 5))
    cases.append(("by_rule carries the census column", stats["by_rule"]["vault-write-direct"].get("census") == 2))
    cases.append(("a window with no census rows emits an empty map, not a missing key",
                  ara.aggregate([row("cue-exempt", "x")], 30).get("vault_writes") == {}))

    failures = [c for c in cases if not c[1]]
    for c in cases:
        print("%-4s %s%s" % ("ok" if c[1] else "FAIL", c[0], "" if c[1] or len(c) < 3 else "  got=%r" % (c[2],)))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
