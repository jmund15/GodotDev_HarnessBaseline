#!/usr/bin/env python3
"""Spend by session and model over recent Claude Code transcripts, priced from seats.json.

    python3 .claude/tools/usage_report.py [--days N] [--top N] [--root DIR ...]

Reads every login's transcripts (transcript_usage.projects_dirs; --root replaces them) in files modified
in the last N days (default 7), and prints the calls and token parts per session and model with their USD
cost, then totals by model and by transport.

Prices are seats.json `models.<id>.price`, USD per 1M tokens: fresh input at cacheMissPer1M, cache reads at
cacheHitPer1M, output at outputPer1M. seats.json has no cache-write rate, so cache-write tokens are counted
and left out of the cost. A model with no seats.json row is unpriced: its tokens count, its cost does not.
"""
import argparse
import json
import os
import sys
import time

import transcript_usage

CLAUDE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PER_TOKEN_UNIT = 1000000
PARTS = ("inp", "cr", "cw", "out")


def load_prices(claude_dir=CLAUDE_DIR):
    with open(os.path.join(claude_dir, "seats.json"), encoding="utf-8") as fh:
        return {model: row["price"] for model, row in json.load(fh)["models"].items()}


def cost_of(call, price):
    """USD of one call, or None when its model has no price row."""
    if price is None:
        return None
    return ((call["inp"] or 0) * price["cacheMissPer1M"]
            + (call["cr"] or 0) * price["cacheHitPer1M"]
            + (call["out"] or 0) * price["outputPer1M"]) / PER_TOKEN_UNIT


def _new_row():
    row = {"calls": 0, "usd": 0.0, "unpriced_calls": 0}
    row.update({part: 0 for part in PARTS})
    return row


def _add(row, call, usd):
    row["calls"] += 1
    for part in PARTS:
        row[part] += call[part] or 0
    if usd is None:
        row["unpriced_calls"] += 1
    else:
        row["usd"] += usd


def report(calls, prices):
    """{sessions: {(session, model): row}, models: {model: row}, transports: {transport: row}}."""
    tables = {"sessions": {}, "models": {}, "transports": {}}
    for call in calls:
        model = call["model"] or "?"
        usd = cost_of(call, prices.get(model))
        keys = {"sessions": (call["session"] or "-", model), "models": model, "transports": call["transport"]}
        for name, key in keys.items():
            _add(tables[name].setdefault(key, _new_row()), call, usd)
    return tables


def _line(label, model, row):
    cost = "%.4f" % row["usd"] + (" +%d unpriced" % row["unpriced_calls"] if row["unpriced_calls"] else "")
    return "%-36s %-28s %6d %12d %12d %12d %10d  %s" % (
        label, model, row["calls"], row["inp"], row["cr"], row["cw"], row["out"], cost)


HEADER = "%-36s %-28s %6s %12s %12s %12s %10s  %s" % (
    "session", "model", "calls", "fresh", "cache_read", "cache_write", "output", "USD")


def render(tables, found, days, top):
    lines = ["Spend, transcripts modified in the last %g days: %d files, %d calls; "
             "%d unparseable lines in %d files." % (days, found["files"], len(found["calls"]),
                                                  found["unparseable_lines"], len(found["unparseable_files"]))]
    lines += ["missing transcript root: %s" % gap for gap in found["gaps"]]
    lines += ["", HEADER]
    ranked = sorted(tables["sessions"].items(), key=lambda kv: (-kv[1]["usd"], -kv[1]["out"]))
    lines += [_line(session, model, row) for (session, model), row in ranked[:top]]
    if len(ranked) > top:
        lines.append("... %d more session-model rows (--top)" % (len(ranked) - top))
    lines += ["", "By model", HEADER]
    lines += [_line("", model, row) for model, row in sorted(tables["models"].items())]
    lines += ["", "By transport", HEADER]
    lines += [_line("", transport, row) for transport, row in sorted(tables["transports"].items())]
    unpriced = sorted(m for m, row in tables["models"].items() if row["unpriced_calls"])
    if unpriced:
        lines.append("Unpriced (no seats.json row; tokens counted, cost not): " + ", ".join(unpriced))
    cache_writes = sum(row["cw"] for row in tables["models"].values())
    lines.append("Cost excludes %d cache-write tokens (seats.json has no cache-write rate)." % cache_writes)
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=float, default=7)
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--root", action="append", default=None, help="a projects dir to read; repeatable")
    args = parser.parse_args(argv)
    roots = args.root or transcript_usage.projects_dirs()
    found = transcript_usage.scan(roots, since=time.time() - args.days * 86400)
    print(render(report(found["calls"], load_prices()), found, args.days, args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
