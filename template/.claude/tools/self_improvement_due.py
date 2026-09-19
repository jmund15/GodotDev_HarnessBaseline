#!/usr/bin/env python3
"""Report whether the self-improvement loop (`/eval_dashboard`, `/autolearn`) is due.

Due when any holds:
  - 10 or more self-evaluate rows have landed since the last `/eval_dashboard` stamp
    (`<root>/logs/eval_dashboard_last.json`), including a missing stamp;
  - `<root>/orchestration_candidates.json` is more than 7 days old, including absent;
  - 5 or more `review-by` triggers in the review scope (CLAUDE.md, `rules/**`, top-level
    `auto-memory/`) have passed, per `rule_retirement.review_due`. `/autolearn`'s retirement
    pass proposes keep, retire or demote for each.

Row counting uses `self_eval_archive_store.effective_entries`, the store's cheap
identity-deduped ledger view — never `analyze_eval_archive.py`'s full legacy-classification
pass, which the dashboard needs but a startup check does not.

    python3 .claude/tools/self_improvement_due.py [--root .claude] [--today YYYY-MM-DD]
        [--line | --stamp]

`--line` prints only the due line for a hook to relay verbatim; each due reason names its own
command, and the line is empty (no output) when nothing is due. `--stamp` is `/eval_dashboard`'s
own stamp step: it writes the current row count and today's date to
`<root>/logs/eval_dashboard_last.json`, the file the row check above reads back. Without either
flag, prints a short report on every due check. Exit: 0 on a report or a due line (an unreadable
archive or stamp counts as due, never as a crash); `--stamp` exits 2 when the archive itself cannot
be read.
"""
import argparse
import datetime
import json
import os
import sys

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _TOOLS_DIR)
import self_eval_archive_store as store  # noqa: E402

ROW_THRESHOLD = 10
CANDIDATES_MAX_AGE_DAYS = 7
REVIEW_THRESHOLD = 5
ARCHIVE_NAME = "self_evaluate_archive.json"
STAMP_NAME = os.path.join("logs", "eval_dashboard_last.json")
CANDIDATES_NAME = "orchestration_candidates.json"


def load_row_count(archive_path):
    """(count, None), or (None, reason) when the archive cannot be read. A ledger lock that
    stays held past the store's wait has its own reason; the caller treats both as due."""
    try:
        return len(store.effective_entries(archive_path)), None
    except TimeoutError:
        return None, ("self-evaluate archive lock still held after %.0f s at %s"
                      % (store.LOCK_TIMEOUT_SECONDS, archive_path))
    except (OSError, ValueError):
        return None, "self-evaluate archive unreadable at %s" % archive_path


def current_row_count(archive_path):
    """None when the archive cannot be read — the caller treats that as due, not silent."""
    return load_row_count(archive_path)[0]


def _read_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def rows_due(root):
    """(due: bool, reason: str)."""
    archive_path = os.path.join(root, ARCHIVE_NAME)
    count, error = load_row_count(archive_path)
    if count is None:
        return True, error
    stamp = _read_json(os.path.join(root, STAMP_NAME))
    if stamp is None:
        return True, "no eval_dashboard stamp (%d self-evaluate rows now)" % count
    stamped_count = stamp.get("row_count")
    if not isinstance(stamped_count, int):
        return True, "eval_dashboard stamp has no row_count"
    delta = count - stamped_count
    if delta >= ROW_THRESHOLD:
        return True, "%d self-evaluate rows since the last stamp (>= %d)" % (delta, ROW_THRESHOLD)
    return False, "%d self-evaluate rows since the last stamp (< %d)" % (delta, ROW_THRESHOLD)


def candidates_due(root, today):
    """(due: bool, reason: str)."""
    path = os.path.join(root, CANDIDATES_NAME)
    if not os.path.isfile(path):
        return True, "%s is absent" % CANDIDATES_NAME
    try:
        mtime = datetime.date.fromtimestamp(os.path.getmtime(path))
    except (OSError, OverflowError, ValueError):
        return True, "%s has no readable mtime" % CANDIDATES_NAME
    age_days = (today - mtime).days
    if age_days > CANDIDATES_MAX_AGE_DAYS:
        return True, "%s is %d days old (> %d)" % (CANDIDATES_NAME, age_days, CANDIDATES_MAX_AGE_DAYS)
    return False, "%s is %d days old (<= %d)" % (CANDIDATES_NAME, age_days, CANDIDATES_MAX_AGE_DAYS)


def reviews_due(root, today):
    """(due: bool, reason: str). A scan that cannot import or raises is due with its error, never
    silent, and never takes the row and candidate checks down with it."""
    try:
        import rule_retirement
        count = len(rule_retirement.review_due(root, today.isoformat()))
    except Exception as exc:  # advisory: report the unknown instead of hiding it
        return True, "review-by scan failed (%s)" % exc.__class__.__name__
    if count >= REVIEW_THRESHOLD:
        return True, "%d instruction reviews past their review-by date" % count
    return False, "%d instruction reviews past their review-by date (< %d)" % (count, REVIEW_THRESHOLD)


def due_line(root, today, rows=None, candidates=None, reviews=None):
    """The single advisory line, or '' when nothing is due. `rows`, `candidates` and `reviews`
    take already-computed results so a caller loads the archive once. Each reason carries the
    command that clears it."""
    checks = (
        (rows if rows is not None else rows_due(root), "/eval_dashboard"),
        (candidates if candidates is not None else candidates_due(root, today), "/eval_dashboard"),
        (reviews if reviews is not None else reviews_due(root, today), "/autolearn"),
    )
    parts = ["%s (run %s)" % (reason, command) for (due, reason), command in checks if due]
    if not parts:
        return ""
    return "Self-improvement due: %s." % "; ".join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Report whether /eval_dashboard or /autolearn are due to run.")
    parser.add_argument("--root", default=".claude", help="the .claude directory (default: .claude)")
    parser.add_argument("--today", help="YYYY-MM-DD (default: today)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--line", action="store_true",
                      help="print only the due line; empty output when nothing is due")
    mode.add_argument("--stamp", action="store_true",
                      help="write logs/eval_dashboard_last.json with the current row count and date")
    args = parser.parse_args(argv)

    if args.today:
        try:
            today = datetime.date.fromisoformat(args.today)
        except ValueError:
            print("UNKNOWN: --today is not a real YYYY-MM-DD date: %s" % args.today)
            return 0
    else:
        today = datetime.date.today()

    if args.stamp:
        archive_path = os.path.join(args.root, ARCHIVE_NAME)
        count = current_row_count(archive_path)
        if count is None:
            print("REFUSED: cannot stamp — self-evaluate archive unreadable at %s" % archive_path)
            return 2
        stamp_path = os.path.join(args.root, STAMP_NAME)
        os.makedirs(os.path.dirname(stamp_path), exist_ok=True)
        with open(stamp_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"row_count": count, "date": today.isoformat()}, handle,
                      indent=2, sort_keys=True)
            handle.write("\n")
        print("Stamped %s: row_count=%d date=%s" % (stamp_path, count, today.isoformat()))
        return 0

    row_is_due, row_reason = rows_due(args.root)
    cand_is_due, cand_reason = candidates_due(args.root, today)
    rev_is_due, rev_reason = reviews_due(args.root, today)

    if args.line:
        line = due_line(args.root, today, rows=(row_is_due, row_reason),
                        candidates=(cand_is_due, cand_reason), reviews=(rev_is_due, rev_reason))
        if line:
            print(line)
        return 0

    print("== Self-improvement due check ==")
    print("rows:       %-5s %s" % ("DUE" if row_is_due else "live", row_reason))
    print("candidates: %-5s %s" % ("DUE" if cand_is_due else "live", cand_reason))
    print("reviews:    %-5s %s" % ("DUE" if rev_is_due else "live", rev_reason))
    print("due: %s" % (row_is_due or cand_is_due or rev_is_due))
    return 0


if __name__ == "__main__":
    sys.exit(main())
