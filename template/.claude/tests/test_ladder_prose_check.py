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

HEAD = "## Role guidance\n\n| model | strengths | effort | ± |\n|---|---|---|---|\n"


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


def planted_live_code(line):
    """(exit code with `line` planted after the live ladder's title, exit code once it is removed)."""
    fd, path = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    with open(LIVE, encoding="utf-8") as fh:
        original = fh.read()
    title, _, rest = original.partition("\n")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(title + "\n\n" + line + "\n" + rest)
    planted = lpc.main([path])
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(original)
    return planted, lpc.main([path])


# A planted registry: the live one's families change as rows land and retire.
REGISTRY = {"models": [{"id": "x-" + f, "family": f, "alias": f} for f in ("opus", "terra", "pro", "mimo-pro")]}


def named_code(pm_cell, rows=("opus", "somemodel")):
    """Exit code for a table whose rows are `rows`, the last carrying `pm_cell`, over REGISTRY."""
    fd, path = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(HEAD)
        for name in rows[:-1]:
            fh.write("| %s | a role | `low` | + fine |\n" % name)
        fh.write("| %s | a role | `low` | %s |\n\n## Next section\n" % (rows[-1], pm_cell))
    return lpc.main([path], data=REGISTRY)


# A planted claim: one family claiming two tiers, plus an added review seat (supplementary).
COVER_REG = {"models": [{"id": "x-cmodel", "family": "cmodel", "alias": "cmodel", "roles": ["fanout", "executor"],
                         "supplementary": ["architect"]}]}


def coverage_code(effort_cell):
    """Exit code for a one-row table whose row claims fanout and executor, plus a review seat."""
    fd, path = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(HEAD + "| cmodel | a role | %s | + fine |\n\n## Next section\n" % effort_cell)
    return lpc.main([path], data=COVER_REG)


# A planted row whose registry avoids two efforts, and claims no tier, so only the avoid check can fire.
# The older version carries no avoid list, as a family's retired version does; only the current one decides.
AVOID_REG = {"models": [{"id": "x-amodel", "family": "amodel", "alias": "amodel", "lifecycle": "current",
                         "effort": {"avoid": ["low", "max"], "avoidRef": "planted"}},
                        {"id": "x-amodel-old", "family": "amodel", "lifecycle": "retired"}]}


def avoid_code(effort_cell):
    """Exit code for a one-row table whose row's registry avoids `low` and `max`."""
    fd, path = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(HEAD + "| amodel | a role | %s | + fine |\n\n## Next section\n" % effort_cell)
    return lpc.main([path], data=AVOID_REG)


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
    ("the version takeover rule fails",
     lambda: top_level_code("A new version takes over its family's row.") == 1),
    ("the owner-claim rule fails",
     lambda: top_level_code("The owner's statement that a row does a shape sets it.") == 1),
    ("the contradicting-cell rule fails",
     lambda: top_level_code("A cell that contradicts a rung goes to the owner.") == 1
     and top_level_code("A cell that contradicts an effort level goes to the owner.") == 1),
    ("the authority rule fails",
     lambda: top_level_code("- **Authority:** the owner may edit any cell at any time.") == 1),
    ("the ingest proposal rule fails",
     lambda: top_level_code("An agent proposes a change with its evidence through `/ladder_ingest`.") == 1),
    ("the cost-axis scoring rule fails",
     lambda: top_level_code("- **cost:** tokens per result (output, uncached input and cache reads "
                            "counted apart) times the row's price.") == 1),
    ("ordinary reader guidance passes",
     lambda: top_level_code("Availability comes from the registry.") == 0),
    ("the evidence-column legend passes",
     lambda: top_level_code("`evidence` tags each row's sources: `pub` published results, `bench` our "
                            "battery, `use` owner rulings and real use; no source gates a pin.") == 0),

    # ---- retired authority lines: the header's authority rule is their one replacement ----
    ("the retired no-row rule planted in the live ladder fails, and the restored copy passes",
     lambda: planted_live_code("- **No row means no measured capability.**") == (1, 0)),
    ("the retired candidates file planted in the live ladder fails",
     lambda: planted_live_code("The `ladder_candidates` file holds unverified claims.")[0] == 1),
    ("the retired battery gate planted in the live ladder fails",
     lambda: planted_live_code("A new family gets no ladder row until a battery scores it.")[0] == 1),

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
     lambda: code("+ exhaustive over a diff -- highest finding count of any row, and the "
                  "defects only it reports hold up; - slow: budget several times a cheap row's "
                  "wall-clock for the same lens set") == 0),

    ("an ordinary ± cell passes", lambda: code("+ read-heavy surveys; - open-ended design") == 0),

    # `effort` owns every rung: a rung in a ± cell is a second home that drifts from it, and it
    # pins the rung the reader should weigh case by case.
    ("a backticked rung in a ± cell fails", lambda: code("+ debugging at `high`; - slow") == 1),
    ("a rung beside a row name in a ± cell fails",
     lambda: named_code("- below opus·high on chained research") == 1),
    ("a bare xhigh in a ± cell fails", lambda: code("+ plan review at xhigh matches the strongest row") == 1),
    ("an 'effort' phrase in a ± cell fails", lambda: code("- low effort does not think") == 1),
    ("a ± cell without a rung passes the same claims",
     lambda: named_code("+ debugging and plan review; - below opus on chained research") == 0),
    ("a rung word used as an ordinary adjective passes",
     lambda: code("+ strong agentic coding at a low draw per task; - high variance across repeats") == 0),

    ("...and the routing verdict passes",
     lambda: code("+ the cheapest reliable execution of a converged plan; - weak at review") == 0),
    ("...and the strongest-pick verdict passes",
     lambda: code("+ the strongest pick for architecting; test authoring as strong as any row") == 0),

    # The boards are keyed by task id and the header says so once; a cell repeating one is
    # restating the trace path instead of stating the tendency.
    ("a battery task id in a cell fails",
     lambda: code("+ reliable review at `high` (T11)") == 1),

    ("a board score fails",
     lambda: code("+ tops scoped architecting, 53/77 with clean cites") == 1),

    ("...and the same claims written WITHOUT the figures pass",
     lambda: code("+ reliable scoped architecting with clean cites") == 0),

    # The `evidence` column tags a row's sources and the registry owns prices, so a cell that
    # narrates either is a second copy an orchestrator reads on every pick.
    ("a price in a cell fails", lambda: code("+ scoped execution; $2 in / $10 out per million tokens") == 1),
    ("vendor-report narration fails",
     lambda: code("+ its vendor reports coding up on the previous version") == 1),
    ("public-board narration fails", lambda: code("+ public boards rank it high on agentic coding") == 1),
    ("an evidence-era note fails", lambda: code("- slow; our cells predate the latest version") == 1),
    ("a board-point delta fails", lambda: code("+ coding up 6–7 points") == 1),
    ("a fraction ratio fails", lambda: code("+ about ¼ of a costly row per task") == 1),
    ("...and the same tendencies without source narration pass",
     lambda: code("+ strong on agentic coding at a low draw per task; - weak on "
                  "open-ended judgment") == 0),

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

    # A result at one rung shows that rung is enough; "only/always/never" turns it into a law.
    ("'only' in the EFFORT cell fails",
     lambda: effort_code("`xhigh` plan review only: below it a review misses much") == 1),
    ("'never' in the EFFORT cell fails", lambda: effort_code("`high` debugging; never lower") == 1),
    ("'always' in the EFFORT cell fails", lambda: effort_code("`high` for debugging, always") == 1),
    ("a start rung with its moving signal passes",
     lambda: effort_code("`medium` to start; rises with how well a defect can hide") == 0),

    # Effort cells are mostly rung tokens; the check must not fire on the ordinary shape.
    ("an ordinary effort cell passes",
     lambda: effort_code("`high` while ambiguous / `medium` exec / `low` tight specs") == 0),

    # ---- a cell may name another row's family, never one the reader cannot find ----
    ("naming a family with a ladder row passes",
     lambda: named_code("- trails opus on open design; cheaper") == 0),
    ("naming a registry family with no ladder row fails",
     lambda: named_code("- trails terra on open design") == 1),
    ("a hyphenated family is one name, not its shorter neighbour",
     lambda: named_code("+ matches mimo-pro on coding", rows=("mimo-pro", "somemodel")) == 0),

    # A column added to CHECKED_COLUMNS without a rule set would be checked by the base rules alone.
    ("every checked column has its own rule set",
     lambda: set(lpc.COLUMN_RULES) == set(lpc.CHECKED_COLUMNS)),

    # ---- unreadable input is NOT a pass -----------------------------------
    ("a table with no ± column exits 2, never 0",
     lambda: lpc.main([_no_pm_column()]) == 2),

    # An absent file used to raise FileNotFoundError out of main -- exit 1, which a caller reads as
    # "violations found" rather than "could not check". Indeterminate has its own code.
    ("an absent file exits 2, not 1 and not a traceback",
     lambda: lpc.main(["/no/such/ladder.md"]) == 2),

    # ---- every claimed tier's everyday shapes have a start rung ------------
    ("a claimed fanout tier with no survey or mechanical rung fails",
     lambda: coverage_code("`low` converged execution and debugging; an added review seat") == 1),
    ("a claimed executor tier with no debugging rung fails",
     lambda: coverage_code("`max` surveys and an added review seat / `low` converged execution and extraction") == 1),
    ("a supplementary tier with no review-seat rung fails",
     lambda: coverage_code("`max` surveys and debugging / `low` converged execution and extraction") == 1),
    ("every claimed shape named passes",
     lambda: coverage_code("`max` surveys, debugging and an added review seat / `low` converged execution and extraction") == 0),
    ("an effort cell naming an effort its registry row avoids fails",
     lambda: avoid_code("`medium` planning · `max` an added review seat") == 1),
    ("a range ending at an avoided effort fails",
     lambda: avoid_code("`medium`–`max` chained research") == 1),
    ("a cell naming only allowed efforts passes",
     lambda: avoid_code("`medium` planning · `high`–`xhigh` chained research") == 0),

    # ---- the live file ----------------------------------------------------
    ("the shipped ladder is clean", lambda: lpc.main([LIVE]) == 0),
]


def _no_pm_column():
    fd, path = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("## Role guidance\n\n| model | strengths |\n|---|---|\n| m | r |\n")
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
