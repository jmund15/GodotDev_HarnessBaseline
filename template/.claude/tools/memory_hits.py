#!/usr/bin/env python3
"""
On-demand reader for logs/memory_hits.jsonl — lists auto-memory paths a
session actually loaded, grouped by path, for `/self_evaluate`'s
`memory_hits` field to be seeded from fact.

Usage: python3 .claude/tools/memory_hits.py --session <prefix> [--json]

Groups by path; each group carries the strongest provenance seen ("read"
outranks "search") and the count. Rows are ordered strong-first, then by
first-seen order within a tier — the main-loop `Read` signal is stronger
evidence of an informed decision than a `search` hit that may never have
been opened.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
from _hook_state import log_path  # noqa: E402


def _log_path() -> str:
    return log_path("memory_hits.jsonl", "HARNESS_MEMORY_HITS_LOG")


def load_records(log_file: str):
    if not os.path.exists(log_file):
        return []
    records = []
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    return records


def group_by_session(records, session_prefix: str):
    groups = {}
    order = []
    for rec in records:
        if session_prefix and not str(rec.get("session_id", "")).startswith(session_prefix):
            continue
        path = rec.get("path", "")
        via = rec.get("via", "search")
        if path not in groups:
            groups[path] = {"path": path, "read": 0, "search": 0}
            order.append(path)
        if via == "read":
            groups[path]["read"] += 1
        else:
            groups[path]["search"] += 1

    def strength(p):
        g = groups[p]
        return (0 if g["read"] > 0 else 1, order.index(p))

    return [groups[p] for p in sorted(order, key=strength)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True,
                        help="session id prefix; no default — an unscoped run would report "
                             "every session's hits as this one's")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    records = load_records(_log_path())
    rows = group_by_session(records, args.session)

    if args.json:
        print(json.dumps(rows))
        return 0

    if not rows:
        print("no memory hits logged for this session")
        return 0

    for row in rows:
        provenance = "read" if row["read"] > 0 else "search"
        print("%-6s %2dx  %s" % (provenance, row["read"] + row["search"], row["path"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
