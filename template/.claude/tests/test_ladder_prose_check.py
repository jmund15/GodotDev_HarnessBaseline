#!/usr/bin/env python3
"""Proof for tools/ladder_prose_check.py.

The check exists because the ladder-ingest authoring contract went unenforced and every comparison
appended one more run description. The load-bearing cases are PLANTED violations: a check that only
ever sees a clean table is indistinguishable from one whose patterns match nothing.

Run: python3 .claude/tests/test_ladder_prose_check.py
"""
import importlib.util
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "..", "tools", "ladder_prose_check.py")
spec = importlib.util.spec_from_file_location("lpc", MOD)
lpc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lpc)

LIVE = os.path.join(HERE, "..", "reference", "model_ladder_evidence.md")

HEAD = "## Role guidance\n\n| model | role | effort | ± |\n|---|---|---|---|\n"


def table(pm_cell, effort_cell="`low` converged"):
    """A minimal Role guidance table carrying `pm_cell` and `effort_cell`.

    Both columns are checked, so the fixture must have both: a table missing `effort` is now
    UNREADABLE (exit 2), not clean — which is how this fixture caught its own staleness.
    """
    fd, path = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# Model Ladder\n\n## Pick by work shape\n\n| a | b |\n|---|---|\n| x | y |\n\n")
        fh.write(HEAD + "| somemodel | a role | %s | %s |\n\n## Next section\n"
                 % (effort_cell, pm_cell))
    return path


def code(pm_cell):
    return lpc.main([table(pm_cell)])


def effort_code(effort_cell):
    """Same check, aimed at the EFFORT cell — where the banned figures went once ± was enforced."""
    return lpc.main([table("+ fine; - also fine", effort_cell)])


def top_level_code(text):
    path = table("+ ordinary tendency")
    with open(path, "r+", encoding="utf-8", newline="\n") as fh:
        original = fh.read()
        fh.seek(0)
        fh.write("# Model Ladder\n\n" + text + "\n\n" + original)
        fh.truncate()
    return lpc.main([path])


CASES = [
    # ---- edit-only contracts belong in /ladder_ingest, never this reader ----
    ("the authoring heading fails", lambda: top_level_code("## Authoring an entry") == 1),
    ("an ingest edit rule fails", lambda: top_level_code("Every ingest rewrites an existing clause.") == 1),
    ("the version-pooling edit rule fails",
     lambda: top_level_code("Replacing an older cell is a normal edit.") == 1),
    ("ordinary reader guidance passes",
     lambda: top_level_code("Availability comes from the registry.") == 0),

    # ---- PLANTED violations: each must FAIL -------------------------------
    ("a date in a ± cell fails", lambda: code("+ good at things since 2026-09-08") == 1),
    ("a wall-clock figure fails", lambda: code("+ thorough but takes 91 min per lens") == 1),
    ("an arm count fails", lambda: code("+ led every arm on a 5-lens audit") == 1),
    ("a multiplier fails", lambda: code("+ finds more, at ~4x the cost of a cheap row") == 1),
    ("an evidence-file link fails", lambda: code("+ solid (`evidence_some_run_2026_09_08`)") == 1),

    # The exact clause the owner rejected, verbatim in shape.
    ("the rejected clause shape fails",
     lambda: code("+ on a frozen 5-lens harness audit it led every arm on real defects found at "
                  "zero false positives, buying that with ~4x the wall-clock of the cheapest arm "
                  "(`evidence_session_audit_four_arm_2026_09_08`)") == 1),

    # ---- NEGATIVES: a tendency clause must PASS ---------------------------
    ("the same insight written as a TENDENCY passes",
     lambda: code("+ exhaustive over a diff at `max` -- highest finding count of any row, and the "
                  "defects only it reports hold up; - slow: budget several times a cheap row's "
                  "wall-clock for the same lens set") == 0),

    ("an ordinary ± cell passes", lambda: code("+ read-heavy surveys; - open-ended design") == 0),

    # A tier token is not a duration -- the check must not reach for it, or it fires on every row.
    ("effort rungs do not trip it",
     lambda: code("+ `max` on open lenses, top of the review board; - mid-board on scoped work") == 0),

    # The boards are keyed by task id and the header says so once; a cell repeating one is
    # restating the trace path instead of stating the tendency.
    ("a battery task id in a cell fails",
     lambda: code("+ top of the review board at `high` (T11)") == 1),

    ("a board score fails",
     lambda: code("+ tops scoped architecting, 53/77 with clean cites") == 1),

    ("...and the same claims written WITHOUT the figures pass",
     lambda: code("+ tops the board on scoped architecting and cites cleanly there") == 0),

    # ---- the EFFORT column, checked on the same terms ----------------------
    # Enforcing ± alone did not remove the figures, it relocated them: task ids, scores and
    # multipliers all reappeared one cell left, in prose doing the same job.
    ("a task id in the EFFORT cell fails",
     lambda: effort_code("`xhigh` deep review -- `high` misses most (T11)") == 1),

    ("a score in the EFFORT cell fails",
     lambda: effort_code("`high` more depth; never rescues a lens (2 / 4 of 28)") == 1),

    # The `×` form, which the ASCII-only multiplier pattern used to walk straight past.
    ("a Unicode `2×` multiplier fails, not just the ASCII `2x`",
     lambda: effort_code("`xhigh` matched `high` at 2× the tokens") == 1),

    ("...and the ASCII form still fails",
     lambda: effort_code("`xhigh` matched `high` at 2x the tokens") == 1),

    ("the same guidance without figures passes",
     lambda: effort_code("`xhigh` deep review -- below it most planted defects go unfound") == 0),

    # Effort cells are mostly rung tokens; the check must not fire on the ordinary shape.
    ("an ordinary effort cell passes",
     lambda: effort_code("`high` while ambiguous / `medium` exec / `low` tight specs") == 0),

    # ---- unreadable input is NOT a pass -----------------------------------
    ("a table with no ± column exits 2, never 0",
     lambda: lpc.main([_no_pm_column()]) == 2),

    # An absent file used to raise FileNotFoundError out of main -- exit 1, which a caller reads as
    # "violations found" rather than "could not check". Indeterminate has its own code.
    ("an absent file exits 2, not 1 and not a traceback",
     lambda: lpc.main(["/no/such/ladder.md"]) == 2),

    # ---- the live file ----------------------------------------------------
    ("the shipped ladder is clean", lambda: lpc.main([LIVE]) == 0),
]


def _no_pm_column():
    fd, path = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("## Role guidance\n\n| model | role |\n|---|---|\n| m | r |\n")
    return path


def main():
    failed = 0
    for name, fn in CASES:
        try:
            ok, detail = bool(fn()), ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
    print("\n%d/%d passed" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
