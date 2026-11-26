#!/usr/bin/env python3
"""A review seat's findings, recorded one row at a time beside its brief, so a failed structured return loses none.

A seat returns its findings as one JSON object through the client's structured-output tool. One malformed emission
(an unterminated string) fails the whole object, the client keeps 2048 bytes of the failed input, and the seat's paid
work is gone. A seat therefore records each finding as it settles it and the review close reads the rows back.

    python3 .claude/tools/seat_findings.py add <brief path> --json '<one JSON object>'     (or the object on stdin)

A row is a finding (it has `action`) or the seat's coverage (it has `checked`, optionally `gaps` and `report`); the tool refuses any other
object or any text that is not one JSON object, so every stored line parses alone and the seat resends only that row.
The file is `path_for(brief)`: the brief's path with its extension replaced by `.findings.jsonl`, the rule the seat's
brief (`lens_briefs.py`) and the review tail (`review_result.prepare_review`) share through `path_for`; both engines
mirror it by regex on a brief path that always has an extension, and `test_seat_findings.py` pins only their suffix
literal.
`recover` rebuilds the seat's object from the rows, skipping a torn line, and reads nothing older than its brief.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import _hook_state  # noqa: E402

SUFFIX = ".findings.jsonl"


def path_for(brief):
    """The findings file of the seat whose brief is at `brief`."""
    return Path(brief).with_suffix(SUFFIX)


def row_of(text):
    """The row object `text` holds, else ValueError naming what the seat resends."""
    try:
        row = json.loads(text)
    except ValueError as err:
        raise ValueError(f"not one JSON object ({err}); resend this finding alone") from err
    if not isinstance(row, dict) or not ("action" in row or "checked" in row):
        raise ValueError("a row is a finding (it has `action`) or the coverage row (it has `checked`)")
    return row


def add(brief, text):
    """Append the row `text` holds to the seat's findings file; ValueError when it is no row."""
    row = row_of(text)
    path = path_for(brief)
    # A seat may record several findings in one turn's parallel calls, so the append takes the file lock; the
    # close reads every row, so it never rotates.
    if not _hook_state.append_jsonl_rotating(str(path), [row], max_bytes=None):
        raise OSError("could not append to %s under its lock; record this finding again" % path)
    return path


def recover(brief):
    """The seat's object `{"findings": [...], "checked"?, "gaps"?, "report"?}` from its recorded rows, or None when the file is
    absent, older than the brief (an earlier run's), or holds no row. A line that does not parse is skipped."""
    path = path_for(brief)
    try:
        if Path(brief).is_file() and path.stat().st_mtime < Path(brief).stat().st_mtime:
            return None
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    findings, coverage = [], {}
    for line in lines:
        try:
            row = row_of(line)
        except ValueError:
            continue
        if "action" in row:
            findings.append(row)
        else:
            coverage = row
    if not findings and not coverage:
        return None
    return {"findings": findings, **{k: coverage[k] for k in ("checked", "gaps", "report") if k in coverage}}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="seat_findings.py", description=__doc__.split("\n")[0])
    parser.add_argument("verb", choices=["add"])
    parser.add_argument("brief", help="the seat's brief path")
    parser.add_argument("--json", dest="text", help="the row; read from stdin when absent")
    args = parser.parse_args(argv)
    try:
        path = add(args.brief, args.text if args.text is not None else sys.stdin.read())
    except (ValueError, OSError) as err:
        print(f"seat_findings: {err}", file=sys.stderr)
        return 2
    print(f"recorded in {path.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
