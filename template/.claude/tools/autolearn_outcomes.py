#!/usr/bin/env python3
"""The lesson-outcome ledger: one row per /autolearn packet row the owner applied or declined, and one
per /codify pass.

Usage:
    autolearn_outcomes.py record --date YYYY-MM-DD --session S --target T --signal G --recommended R
                                 --outcome applied|declined|recorded --reason TEXT [--fix KIND]
                                 [--row LABEL] [--metric KEY] [--cap N] [--file PATH]
    autolearn_outcomes.py lookup --target T [--file PATH]
    autolearn_outcomes.py history --target T [--target T ...] [--file PATH]
    autolearn_outcomes.py unswept [--rows] [--file PATH]
    autolearn_outcomes.py show ID [--file PATH]
    autolearn_outcomes.py swept [--through YYYY-MM-DD] [--file PATH]

`record` validates the row shape, appends it and keeps only the newest `--cap` rows (default 500).
`recorded` counts a slip that landed nothing. A `recorded` row prints, after the confirmation, the earlier rows of any
outcome nearest its signal (date, outcome, session, shared words and paths, signal), best matches of any date or
target, so the agent judges whether the slip is the same choice and sees a fix the owner already declined for it; a path
is one shared item, weighed by how few rows name it. `--fix` names what a landing changed: text, reach,
tool, hook, remove (a deletion or narrowing), or none. `metric` is the dashboard metric key the fix is meant to move.
`lookup` prints, as JSON lines, the declined rows whose target equals T, so the re-proposal check
is a deterministic read: a decline whose reason still holds is not proposed again.
Every row has an id: eight hex digits of a digest of the whole row (`row_id`). A row is never edited, only
appended and dropped by the cap, so its content is its identity: the id survives a rotation and needs no stored
field, and two rows that differ in any field have different ids. Every listing prints it (`record`, the repeat
check, `lookup`, `history`, `unswept --rows`), and `show ID` prints the row back. Cite a row by its id, never
by its line number, which a rotation shifts.
`history` prints every row, any outcome, whose target names one of the given paths (alone, among
several joined by `;`, with a `#anchor`, or followed by a section name), so /codify Step 0 reads which fixes a rule's homes already had.
"""
import argparse
import collections
import hashlib
import json
import math
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_usage  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
from _file_lock import locked  # noqa: E402
from _hook_state import replace_path  # noqa: E402

LOCK_TIMEOUT = 5.0

DEFAULT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "autolearn_outcomes.jsonl")
DEFAULT_CAP = 500
# Basis (a replay of the ledger, in commit 3b93f523's message): the cap, floor and stop list list the most earlier
# partners within a list read whole. A partner sharing fewer than two items is reached by codify Step 5's search by
# the choice. Dropping the stop list, or replacing it with a frequency cutoff, lists fewer.
REPEAT_SHOWN = 8
MIN_SHARED = 2
STOP_WORDS = frozenset("the an and or of to in on for with without that this it is was be by as at from not no its into "
                       "which when than then so are were has had have but if one two".split())
OUTCOMES = ("applied", "declined", "recorded")
FIXES = ("text", "reach", "tool", "hook", "remove", "none")
REQUIRED = ("date", "session", "target", "signal", "recommended", "outcome", "reason")
RECORD_ACCEPTS = ("record --date YYYY-MM-DD --session S --target T --signal G --recommended R "
                  "--outcome applied|declined|recorded --reason TEXT [--fix text|reach|tool|hook|remove|none] "
                  "[--row LABEL] [--metric KEY] [--cap N] [--file PATH]")
RECORD_EXAMPLE = ("python3 .claude/tools/autolearn_outcomes.py record --date 2026-10-29 --session navtools "
                  "--target .claude/skills/instruction_quality/reference/skills_and_commands.md "
                  "--signal 'a refusal printed no shape' --recommended 'state the accepts: and example: lines' "
                  "--outcome applied --reason 'one wire format, one owner' --fix text")


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


def row_id(row):
    """The id of a ledger row: a digest of the row's content, independent of field order and file position."""
    text = json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def listed(row):
    """The JSON line a listing prints for `row`: its id, then its fields."""
    return json.dumps({"id": row_id(row), **row}, ensure_ascii=False)


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


def _targets(row):
    """The file paths a row's target names: the first word of each `;`-joined part, its `#anchor` and a leading
    `.claude/` dropped, so the words after a path name no path and both spellings of a harness file match."""
    out = set()
    for part in str(row.get("target") or "").split(";"):
        words = part.split()
        if words:
            path = words[0].split("#", 1)[0]
            out.add(path[len(".claude/"):] if path.startswith(".claude/") else path)
    return out


def _items(row):
    """What a row can share with another: the words of its signal and the paths of its target."""
    words = {w for w in re.findall(r"[a-z][a-z0-9_]+", str(row.get("signal") or "").lower()) if w not in STOP_WORDS}
    return words | {"path " + p for p in _targets(row)}


def nearest_rows(rows, row):
    """The earlier rows sharing at least MIN_SHARED items with `row`, best first, at most REPEAT_SHOWN, each with the
    items it shares. An item weighs its rarity across those rows, so a path that most rows name (`CLAUDE.core.md`) or a
    word most signals use counts little. Any date, target and outcome qualify: a choice is recognized by its words, a
    fix of it may have landed in another file, and a declined row is the owner's ruling on a fix for it."""
    earlier = list(rows)
    items = [_items(r) for r in earlier]
    seen = collections.Counter(i for group in items for i in group)
    mine = _items(row)
    scored = []
    for r, group in zip(earlier, items):
        shared = mine & group
        if len(shared) >= MIN_SHARED:
            scored.append((sum(math.log(1 + len(earlier) / seen[i]) for i in shared), r, sorted(shared)))
    scored.sort(key=lambda t: -t[0])
    return [(r, shared) for _, r, shared in scored[:REPEAT_SHOWN]]


def repeat_lines(rows, row):
    """Facts for a `recorded` row: the nearest earlier rows (id, date, outcome, session, shared items, signal), oldest first."""
    if row["outcome"] != "recorded":
        return []
    near = nearest_rows(rows, row)
    if not near:
        return []
    near.sort(key=lambda t: str(t[0].get("date")))
    return (["autolearn_outcomes: repeat check: %d earlier row(s) share the most with this slip; judge whether any is the same choice:"
             % len(near)]
            + ["  %s %s %s %s [%s] %s" % (row_id(r), r.get("date"), r.get("outcome"), str(r.get("session"))[:8],
                                         ", ".join(shared), str(r.get("signal"))[:140]) for r, shared in near])


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
        sys.stderr.write(cli_usage.refusal(RECORD_ACCEPTS, RECORD_EXAMPLE))
        return 1
    temp = None
    try:
        directory = os.path.dirname(os.path.abspath(args.file))
        os.makedirs(directory, exist_ok=True)
        with locked(args.file + ".lock", LOCK_TIMEOUT):
            rows, _ = read_rows(args.file)
            repeats = repeat_lines(rows, row)
            rows.append(row)
            rows = rows[-args.cap:]
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                             dir=directory, delete=False) as fh:
                temp = fh.name
                for r in rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            replace_path(temp, args.file)
            temp = None
    except (TimeoutError, OSError) as exc:
        print("autolearn_outcomes: could not record in %s: %s" % (args.file, exc), file=sys.stderr)
        return 1
    finally:
        if temp is not None:
            os.unlink(temp)
    print("autolearn_outcomes: recorded %s for %s as %s (%d rows kept)"
          % (row["outcome"], row["target"], row_id(row), len(rows)))
    for line in repeats:
        print(line)
    return 0


def cmd_lookup(args):
    rows, bad = read_rows(args.file)
    if bad:
        print("autolearn_outcomes: %d unreadable row(s) in %s" % (bad, args.file), file=sys.stderr)
    for r in rows:
        if r.get("outcome") == "declined" and r.get("target") == args.target:
            print(listed(r))
    return 0


def cmd_history(args):
    rows, bad = read_rows(args.file)
    if bad:
        print("autolearn_outcomes: %d unreadable row(s) in %s" % (bad, args.file), file=sys.stderr)
    paths = _targets({"target": ";".join(args.target)})
    for r in rows:
        if paths & _targets(r):
            print(listed(r))
    return 0


def _unread(rows, path):
    marker = read_swept(path)
    return [r for r in rows if r.get("outcome") == "recorded" and str(r.get("date") or "") > marker]


def cmd_show(args):
    rows, bad = read_rows(args.file)
    if bad:
        print("autolearn_outcomes: %d unreadable row(s) in %s" % (bad, args.file), file=sys.stderr)
    found = [r for r in rows if row_id(r) == args.id]
    for r in found:
        print(listed(r))
    if not found:
        print("autolearn_outcomes: no row with id %s in %s (a dropped row has no id)" % (args.id, args.file),
              file=sys.stderr)
    return 0 if found else 1


def read_swept(path):
    """Return the newest YYYY-MM-DD date marked swept beside `path`, or ''."""
    try:
        with open(path + ".swept", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def unswept_summary(path=DEFAULT_FILE):
    """Return the SessionStart fact line for unread `recorded` rows, or ''."""
    rows, _ = read_rows(path)
    if not rows:
        return ""
    unread = _unread(rows, path)
    if not unread:
        return ""
    oldest = min(str(r.get("date")) for r in unread)
    return "autolearn: %d correction row(s) no sweep has read, oldest %s" % (len(unread), oldest)


def cmd_unswept(args):
    if args.rows:
        for r in _unread(read_rows(args.file)[0], args.file):
            print(listed(r))
        return 0
    line = unswept_summary(args.file)
    if line:
        print(line)
    return 0


def cmd_swept(args):
    through = (args.through or "").strip()
    if through:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", through):
            print("autolearn_outcomes: --through must be YYYY-MM-DD, got %r" % through, file=sys.stderr)
            return 1
    else:
        rows, _ = read_rows(args.file)
        dates = [str(r.get("date")) for r in rows if r.get("outcome") == "recorded" and r.get("date")]
        if not dates:
            return 0
        through = max(dates)
    temp = None
    marker_path = os.path.abspath(args.file) + ".swept"
    directory = os.path.dirname(marker_path)
    try:
        os.makedirs(directory, exist_ok=True)
        with locked(args.file + ".lock", LOCK_TIMEOUT):
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                             dir=directory, delete=False) as fh:
                temp = fh.name
                fh.write(through + "\n")
            replace_path(temp, marker_path)
            temp = None
    except (TimeoutError, OSError) as exc:
        print("autolearn_outcomes: could not write swept marker %s: %s" % (marker_path, exc), file=sys.stderr)
        return 1
    finally:
        if temp is not None:
            os.unlink(temp)
    return 0


def build_parser():
    ap = cli_usage.ShapeParser(prog="autolearn_outcomes.py", description=__doc__.split("\n")[0])
    ap.accepts, ap.example = RECORD_ACCEPTS, RECORD_EXAMPLE
    sub = ap.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser(
        "record", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Append one validated ledger row.",
        epilog="required: --date YYYY-MM-DD --session S --target T --signal G --recommended R "
               "--outcome applied|declined|recorded --reason TEXT\n"
               "optional: --fix text|reach|tool|hook|remove|none, --row LABEL, --metric KEY, --cap N, --file PATH\n"
               + cli_usage.refusal(RECORD_ACCEPTS, RECORD_EXAMPLE))
    rec.accepts, rec.example = RECORD_ACCEPTS, RECORD_EXAMPLE
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
    show = sub.add_parser("show")
    show.add_argument("id")
    show.add_argument("--file", default=DEFAULT_FILE)
    show.set_defaults(fn=cmd_show)
    unsw = sub.add_parser("unswept")
    unsw.add_argument("--rows", action="store_true", help="print each unread recorded row, with its id")
    unsw.add_argument("--file", default=DEFAULT_FILE)
    unsw.set_defaults(fn=cmd_unswept)
    sw = sub.add_parser("swept")
    sw.add_argument("--through", default="")
    sw.add_argument("--file", default=DEFAULT_FILE)
    sw.set_defaults(fn=cmd_swept)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
