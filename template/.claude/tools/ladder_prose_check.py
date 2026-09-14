#!/usr/bin/env python3
"""Fail when a Role guidance `±` cell carries run narrative instead of a model tendency.

The ladder is a routing table an orchestrator reads at a glance. Its header has always said numbers,
dates and campaign narrative live in the Obsidian boards -- but nothing enforced it, so every
comparison appended one more "on the N-lens audit it ..." clause and the table drifted toward a
changelog. This checks the half a machine can see: dates, wall-clock figures, arm/lens counts, and
links to per-run write-ups. It cannot judge whether a clause reads as a tendency; that is the
author's job, and the header states the test.

Exit 0 clean, 1 with findings, 2 if the table cannot be read (a silent pass on an unreadable file
would make this check indistinguishable from a clean one).

Run: python3 .claude/tools/ladder_prose_check.py [path]
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HEADING = "## Role guidance"

# Each: (name, pattern, why it is run narrative rather than a tendency).
BANNED = [
    ("date", r"\b20\d\d-\d\d-\d\d\b",
     "a date pins the clause to one run; a tendency has no date"),
    ("wall-clock", r"\b\d+(?:\.\d+)?\s*(?:min|minutes|hours|hrs|sec|seconds)\b",
     "a duration is one run's measurement -- say 'slow' and what it is slow relative to"),
    ("arm/lens count", r"\b\d+\s*-?\s*(?:lens|arm|arms|lenses)\b",
     "the shape of one comparison, not a property of the model"),
    # `×` (U+00D7) as well as ASCII `x`: the cell that first evaded this check wrote `2× tokens`.
    ("multiplier", r"~?\d+(?:\.\d+)?\s*[x×](?![\w-])",
     "a measured ratio against whatever else ran that day"),
    ("evidence-file link", r"`evidence_[a-z0-9_]+`",
     "a per-run write-up; fold the durable half into the clause and drop the link"),
    ("battery task id", r"\bT\d+[A-Za-z]?\b",
     "the boards are keyed by task id and the header says so once — a ± cell citing one is "
     "repeating the trace path instead of stating the tendency"),
    ("score", r"\b\d+\s*/\s*\d+\b",
     "a board figure; say what it means for a pin instead ('tops the board', 'a tier below')"),
]

# The `Pick by work shape` table is EXEMPT by construction: each of its rows IS a task, so the id
# names the row rather than decorating a claim. Only Role guidance's ± cells are checked.


# Both tendency columns are checked. `effort` is not decoration: it says which rung to pin, in the
# same voice as `±`, and it was where every banned figure went once `±` alone was enforced. A rule
# that binds one of two adjacent cells relocates the prose instead of removing it.
CHECKED_COLUMNS = ("±", "effort")


def cells(path):
    """(line, model, column, text) per checked cell of the Role guidance table, None if unreadable.

    The table SCAN is `model_registry.parse_ladder_rows` — the same single reader this session
    consolidated two copies into. Writing a third here would have re-created the defect while the
    commit message claimed to have removed it.

    Line numbers are recovered separately because that reader returns cells, not positions, and a
    finding a reader cannot navigate to is a finding they will not act on.
    """
    if not os.path.isfile(path):
        return None                 # unreadable is INDETERMINATE (exit 2), never a clean pass
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import model_registry           # noqa: E402 - late, so this stays runnable from any cwd

    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    rows = model_registry.parse_ladder_rows(lines, ("model",) + CHECKED_COLUMNS, HEADING)
    if not rows:
        return None if not _has_table(lines) else []

    out = []
    for row in rows:
        needle = "| %s " % row["model"]
        line_no = next((i for i, raw in enumerate(lines, 1) if raw.startswith(needle)), 0)
        for col in CHECKED_COLUMNS:
            out.append((line_no, row["model"], col, row.get(col) or ""))
    return out


def _has_table(lines):
    """Is there a Role guidance table at all? Distinguishes 'no rows' from 'no table'."""
    return any(raw.strip() == HEADING for raw in lines)


def main(argv):
    path = argv[0] if argv else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "reference", "model_ladder_evidence.md")
    try:
        rows = cells(path)
    except OSError as exc:
        print("cannot read %s: %s" % (path, exc))
        return 2
    if rows is None:
        print("cannot read the Role guidance table (absent file, or a missing %s column): %s"
              % (" / ".join(CHECKED_COLUMNS), path))
        return 2
    if not rows:
        print("Role guidance table has no data rows in %s" % path)
        return 2

    bad = 0
    for line, model, col, cell in rows:
        for name, pat, why in BANNED:
            for m in re.finditer(pat, cell, re.I):
                bad += 1
                print("%s:%d  %s  [%s]  %s %r -- %s"
                      % (path, line, model.split("(")[0].strip(), col, name, m.group(0), why))
    print("\n%d cell(s) checked across %s, %d run-narrative hit(s)"
          % (len(rows), " + ".join(CHECKED_COLUMNS), bad))
    if bad:
        print("These cells state what the model TENDS to do and which rung to pin. Rewrite the "
              "clause as a tendency, or drop it -- see the file's own 'Authoring an entry' rule.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
