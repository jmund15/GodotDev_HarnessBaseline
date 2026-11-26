#!/usr/bin/env python3
"""Report whether `/autolearn` is due.

Due when either holds:
  - 5 or more `review-by` triggers in the review scope (CLAUDE.md, `rules/**`, top-level
    `auto-memory/`) have passed, per `rule_retirement.review_due`. `/autolearn`'s retirement
    pass proposes keep, retire or demote for each;
  - one or more evidence markers (`<!-- basis: ... -->`, grammar in `rule_retirement.py`) in a tracked
    `.claude/**/*.md` file have fired or are malformed, per `rule_retirement.evidence_due`. The
    author chose each marker's count, so one crossing is due; `/autolearn` proposes confirm,
    revise or retire for each.

    python3 .claude/tools/self_improvement_due.py [--root .claude] [--today YYYY-MM-DD] [--line]

`--line` prints only the due line for a hook to relay verbatim; each due reason names its own
command, and the line is empty (no output) when nothing is due. Without it, prints a short report on
every check. Exit: 0 on a report or a due line (a scan that fails counts as due, never as a crash).
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REVIEW_THRESHOLD = 5


def reviews_due(root, today):
    """(due: bool, reason: str). A scan that cannot import or raises is due with its error, never
    silent, and never takes the marker check down with it."""
    try:
        import rule_retirement
        count = len(rule_retirement.review_due(root, today.isoformat()))
    except Exception as exc:  # advisory: report the unknown instead of hiding it
        return True, "review-by scan failed (%s)" % exc.__class__.__name__
    if count >= REVIEW_THRESHOLD:
        return True, "%d instruction reviews past their review-by date" % count
    return False, "%d instruction reviews past their review-by date (< %d)" % (count, REVIEW_THRESHOLD)


def markers_due(root):
    """(due: bool, reason: str). A scan that cannot import or raises is due with its error, as
    `reviews_due` is."""
    try:
        import rule_retirement
        found = rule_retirement.evidence_due(root)
    except Exception as exc:  # advisory: report the unknown instead of hiding it
        return True, "evidence scan failed (%s)" % exc.__class__.__name__
    fired = sum(1 for row in found if row["bucket"] == "fired")
    malformed = len(found) - fired
    if found:
        return True, "%d evidence markers fired, %d malformed" % (fired, malformed)
    return False, "no evidence markers fired or malformed"


def due_line(root, today, reviews=None, markers=None):
    """The single advisory line, or '' when nothing is due. `reviews` and `markers` take
    already-computed results so a caller scans once."""
    checks = (reviews if reviews is not None else reviews_due(root, today),
              markers if markers is not None else markers_due(root))
    parts = ["%s (run /autolearn)" % reason for due, reason in checks if due]
    if not parts:
        return ""
    return "Self-improvement due: %s." % "; ".join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Report whether /autolearn is due to run.")
    parser.add_argument("--root", default=".claude", help="the .claude directory (default: .claude)")
    parser.add_argument("--today", help="YYYY-MM-DD (default: today)")
    parser.add_argument("--line", action="store_true", help="print only the due line; empty output when nothing is due")
    args = parser.parse_args(argv)

    if args.today:
        try:
            today = datetime.date.fromisoformat(args.today)
        except ValueError:
            print("UNKNOWN: --today is not a real YYYY-MM-DD date: %s" % args.today)
            return 0
    else:
        today = datetime.date.today()

    reviews, markers = reviews_due(args.root, today), markers_due(args.root)
    if args.line:
        line = due_line(args.root, today, reviews=reviews, markers=markers)
        if line:
            print(line)
        return 0

    print("== Self-improvement due check ==")
    print("reviews:    %-5s %s" % ("DUE" if reviews[0] else "live", reviews[1]))
    print("evidence:   %-5s %s" % ("DUE" if markers[0] else "live", markers[1]))
    print("due: %s" % (reviews[0] or markers[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
