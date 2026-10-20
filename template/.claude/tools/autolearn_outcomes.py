#!/usr/bin/env python3
"""The lesson-outcome ledger: one row per /autolearn packet row the owner applied or declined, and one
per /codify pass.

Usage:
    autolearn_outcomes.py record --date YYYY-MM-DD --session S --target T --signal G --recommended R
                                 --outcome applied|declined|recorded --reason TEXT [--fix KIND]
                                 [--row ID] [--metric KEY] [--cap N] [--file PATH]
    autolearn_outcomes.py lookup --target T [--file PATH]
    autolearn_outcomes.py history --target T [--target T ...] [--file PATH]

`record` validates the row shape, appends it and keeps only the newest `--cap` rows (default 500).
`recorded` counts a slip that landed nothing. `--fix` names what a landing changed: text, reach,
tool, hook, remove (a deletion or narrowing), or none. `metric` is the dashboard metric key the fix is meant to move.
`lookup` prints, as JSON lines, the declined rows whose target equals T, so the re-proposal check
is a deterministic read: a decline whose reason still holds is not proposed again.
`history` prints every row, any outcome, whose target names one of the given paths (alone, among
several joined by `;`, with a `#anchor`, or followed by a section name), so /codify Step 0 reads which fixes a rule's homes already had.
"""
import argparse
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
from _file_lock import locked  # noqa: E402

LOCK_TIMEOUT = 5.0

DEFAULT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "autolearn_outcomes.jsonl")
DEFAULT_CAP = 500
OUTCOMES = ("applied", "declined", "recorded")
FIXES = ("text", "reach", "tool", "hook", "remove", "none")
REQUIRED = ("date", "session", "target", "signal", "recommended", "outcome", "reason")


def read_rows(path):
    rows, bad = [], 0
    try:
        fh = open(path, encoding="utf-8")
    except OSError:
        return rows, bad
    with fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if isinstance(row, dict):
                rows.append(row)
            else:
                bad += 1
    return rows, bad


def problems(row):
    out = ["missing or blank field: %s" % k for k in REQUIRED if not str(row.get(k) or "").strip()]
    if row.get("outcome") and row["outcome"] not in OUTCOMES:
        out.append("outcome must be one of %s, got %r" % ("|".join(OUTCOMES), row["outcome"]))
    if row.get("fix") and row["fix"] not in FIXES:
        out.append("fix must be one of %s, got %r" % ("|".join(FIXES), row["fix"]))
    if row.get("metric") and not re.fullmatch(r"[a-z0-9.-]+", row["metric"]):
        out.append("metric must match [a-z0-9.-]+, got %r" % row["metric"])
    if row.get("date") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["date"]):
        out.append("date must be YYYY-MM-DD, got %r" % row["date"])
    return out


def cmd_record(args):
    row = {k: getattr(args, k) for k in REQUIRED}
    if args.row:
        row["row"] = args.row
    if args.fix:
        row["fix"] = args.fix
    if args.metric:
        row["metric"] = args.metric
    bad = problems(row)
    if bad:
        print("autolearn_outcomes: row rejected, file unchanged: " + "; ".join(bad), file=sys.stderr)
        return 1
    temp = None
    try:
        directory = os.path.dirname(os.path.abspath(args.file))
        os.makedirs(directory, exist_ok=True)
        with locked(args.file + ".lock", LOCK_TIMEOUT):
            rows, _ = read_rows(args.file)
            rows.append(row)
            rows = rows[-args.cap:]
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                             dir=directory, delete=False) as fh:
                temp = fh.name
                for r in rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            os.replace(temp, args.file)
            temp = None
    except (TimeoutError, OSError) as exc:
        print("autolearn_outcomes: could not record in %s: %s" % (args.file, exc), file=sys.stderr)
        return 1
    finally:
        if temp is not None:
            os.unlink(temp)
    print("autolearn_outcomes: recorded %s for %s (%d rows kept)" % (row["outcome"], row["target"], len(rows)))
    return 0


def cmd_lookup(args):
    rows, bad = read_rows(args.file)
    if bad:
        print("autolearn_outcomes: %d unreadable row(s) in %s" % (bad, args.file), file=sys.stderr)
    for r in rows:
        if r.get("outcome") == "declined" and r.get("target") == args.target:
            print(json.dumps(r, ensure_ascii=False))
    return 0


def cmd_history(args):
    rows, bad = read_rows(args.file)
    if bad:
        print("autolearn_outcomes: %d unreadable row(s) in %s" % (bad, args.file), file=sys.stderr)
    paths = set(args.target)
    for r in rows:
        tokens = re.split(r"[;\s]+", str(r.get("target") or ""))
        if paths & {t.split("#", 1)[0] for t in tokens if t}:
            print(json.dumps(r, ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="autolearn_outcomes.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record")
    for k in REQUIRED:
        rec.add_argument("--" + k, default="")
    rec.add_argument("--row", default="")
    rec.add_argument("--fix", default="")
    rec.add_argument("--metric", default="")
    rec.add_argument("--cap", type=int, default=DEFAULT_CAP)
    rec.add_argument("--file", default=DEFAULT_FILE)
    rec.set_defaults(fn=cmd_record)
    look = sub.add_parser("lookup")
    look.add_argument("--target", required=True)
    look.add_argument("--file", default=DEFAULT_FILE)
    look.set_defaults(fn=cmd_lookup)
    hist = sub.add_parser("history")
    hist.add_argument("--target", action="append", required=True)
    hist.add_argument("--file", default=DEFAULT_FILE)
    hist.set_defaults(fn=cmd_history)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
