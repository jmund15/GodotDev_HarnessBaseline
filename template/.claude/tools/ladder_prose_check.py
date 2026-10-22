#!/usr/bin/env python3
"""Fail when a Role guidance `±` cell carries run narrative instead of a model tendency.

The ladder is a routing table an orchestrator reads at a glance. Its header has always said numbers,
dates and campaign narrative live in the Obsidian boards -- but nothing enforced it, so every
comparison appended one more "on the N-lens audit it ..." clause and the table drifted toward a
changelog. This checks the half a machine can see: dates, wall-clock figures, arm/lens counts, and
links to per-run write-ups. It cannot judge whether a clause reads as a tendency; that is the
author's job, and the header states the test. It also fails a cell naming a registry family no ladder
row covers: a comparison must lead the reader to a row.

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
     "a board figure; state the work this row is the cheapest reliable pick for, the strongest pick for, or where it fails"),
    ("board-point delta", r"\b\d+(?:\s*[–-]\s*\d+)?\s*points?\b",
     "a board figure; say what it means for a pin instead"),
    ("fraction", r"[¼½¾⅓⅔]",
     "a measured ratio against another row; say cheap or costly for the work"),
    ("price", r"\$\s?\d|per million tokens",
     "registry `price` owns prices; the `cost` column carries the pick-relevant half"),
    ("source narration", r"\bvendor|\b(?:public|independent) boards?\b|\bunconfirmed\b|\bpreliminary\b|\bpredates?\b",
     "the `evidence` column tags the source; the cell states the tendency"),
]

# Effort levels belong to the `effort` column alone; a `±` cell that names one is a second home that drifts
# from it, and pins an effort the reader should weigh case by case.
RUNG = r"(?:low|medium|high|xhigh|max)"
PM_BANNED = [
    ("effort level", r"`" + RUNG + r"`|·" + RUNG + r"\b|\bxhigh\b|\b" + RUNG + r"\s+effort\b",
     "the `effort` column owns every effort level; compare with another row by family and scope, not by its effort"),
]

# A result at one effort shows that effort is enough, not that the others fail.
EFFORT_BANNED = [
    ("absolute", r"\b(?:only|always|never)\b",
     "a starting effort and the signal that moves it; a result at one effort is not a law about the others"),
]

COLUMN_RULES = {"±": PM_BANNED, "effort": EFFORT_BANNED}

TOP_LEVEL_BANNED = [
    ("authoring heading", r"Authoring an entry"),
    ("ingest edit rule", r"Every ingest rewrites"),
    ("comparison edit rule", r"comparison earns an edit"),
    ("version-pooling edit rule", r"Replacing an older cell is a normal edit"),
    ("candidate promotion rule", r"A claim reaches this file only"),
    ("version takeover rule", r"takes over its family's row"),
    ("owner-claim rule", r"owner's statement that a row"),
    ("contradicting-cell rule", r"contradicts an? (?:effort level|rung)"),
    ("authority rule", r"may edit any cell"),
    ("ingest proposal rule", r"proposes a change with its evidence"),
    ("cost-axis scoring rule", r"counted apart"),
]

# Retired authority lines: `/ladder_ingest`'s authority rule replaced each, so none moves anywhere.
RETIRED = [
    ("no-row rule", r"No row means no measured capability"),
    ("candidates file", r"ladder_candidates"),
    ("battery gate", r"until a battery scores it"),
]


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


def dangling_names(path, data=None):
    """(line, model, column, family) per checked cell naming a registry family no ladder row covers.

    A cell may compare its row with another by family name; the name must lead the reader to a row.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import model_registry           # noqa: E402

    data = data if data is not None else model_registry.load()
    with open(path, encoding="utf-8") as fh:
        rows = model_registry.parse_ladder_rows(fh.readlines(), ("model",) + CHECKED_COLUMNS, HEADING)
    covered = set()
    for row in rows:
        covered.update(model_registry.ladder_members(row["model"]))
        covered.update(m.get("family") for m in model_registry.ladder_rows(row["model"], data))
    loose = {str(m.get("family", "")).lower() for m in data["models"]} - {str(c).lower() for c in covered} - {""}
    out = []
    for line_no, model, col, text in cells(path) or []:
        for name in sorted(loose):
            if re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(name), text, re.I):
                out.append((line_no, model, col, name))
    return out


# The everyday shapes of each tier (the Role definitions table), each as the words an effort cell uses for it. A
# row's effort cell names a start rung for every shape of every tier its registry rows claim, measured or not, so
# no reader guesses a rung at dispatch. A `supplementary` tier is a review seat: its shape is that seat.
TIER_SHAPES = {
    "orchestrator": (("session", r"session|long-horizon|orchestrat"),),
    "architect": (("architecting or design", r"architect|design"), ("planning", r"plann")),
    "executor": (("execution", r"execut|coding|refactor|converged"), ("debugging", r"debug")),
    "fanout": (("surveys and explore lenses", r"survey|explor"),
               ("mechanical fan-out (extraction, enumeration, comparison, rubric checks)",
                r"extract|enumerat|comparison|rubric|mechanical|copyable")),
    "scout": (("locate, enumerate, extract", r"locat|enumerat|extract|copyable"),),
}
SUPPLEMENTARY_SHAPE = ("an added review seat", r"review|plan-check|audit seat|added .*seat")


def coverage_gaps(path, data=None):
    """(line, model, tier, shape) per claimed tier shape the row's effort cell names no rung for."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import model_registry           # noqa: E402

    data = data if data is not None else model_registry.load()
    out = []
    for line_no, model, col, text in cells(path) or []:
        if col != "effort":
            continue
        members = model_registry.ladder_rows(model, data)
        roles = {r for m in members for r in (m.get("roles") or [])}
        supplementary = {r for m in members for r in (m.get("supplementary") or [])} - roles
        wanted = [(tier, shape, pat) for tier in sorted(roles) for shape, pat in TIER_SHAPES.get(tier, ())]
        wanted += [(tier,) + SUPPLEMENTARY_SHAPE for tier in sorted(supplementary)]
        for tier, shape, pat in wanted:
            if not re.search(pat, text, re.I):
                out.append((line_no, model, tier, shape))
    return out


def avoided_efforts(path, data=None):
    """(line, model, effort) per effort an effort cell names that every current registry row of its family avoids.

    A retired version (`lifecycle` other than current) stays out: it carries no avoid list and no pin reaches it."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import model_registry           # noqa: E402

    data = data if data is not None else model_registry.load()
    out = []
    for line_no, model, col, text in cells(path) or []:
        if col != "effort":
            continue
        members = model_registry.ladder_rows(model, data)
        members = [m for m in members if m.get("lifecycle") == "current"] or members
        if not members:
            continue
        avoided = set.intersection(*(set((m.get("effort") or {}).get("avoid") or []) for m in members))
        for level in sorted({m.group(1) for m in re.finditer(r"`(" + RUNG + r")`", text)} & avoided):
            out.append((line_no, model, level))
    return out


def _has_table(lines):
    """Is there a Role guidance table at all? Distinguishes 'no rows' from 'no table'."""
    return any(raw.strip() == HEADING for raw in lines)


def main(argv, data=None):
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
    with open(path, encoding="utf-8") as handle:
        source_lines = handle.readlines()
    for line_no, source_line in enumerate(source_lines, 1):
        for name, pat in TOP_LEVEL_BANNED:
            if re.search(pat, source_line, re.I):
                bad += 1
                print("%s:%d  top-level  %s -- move it to `.claude/commands/ladder_ingest.md`"
                      % (path, line_no, name))
        for name, pat in RETIRED:
            if re.search(pat, source_line, re.I):
                bad += 1
                print("%s:%d  retired  %s -- delete it; `/ladder_ingest`'s authority rule replaced it"
                      % (path, line_no, name))
    for line, model, col, cell in rows:
        for name, pat, why in BANNED + COLUMN_RULES.get(col, []):
            for m in re.finditer(pat, cell, re.I):
                bad += 1
                print("%s:%d  %s  [%s]  %s %r -- %s"
                      % (path, line, model.split("(")[0].strip(), col, name, m.group(0), why))
    for line, model, col, name in dangling_names(path, data):
        bad += 1
        print("%s:%d  %s  [%s]  names %r, which no ladder row covers -- compare with a row the reader can find"
              % (path, line, model.split("(")[0].strip(), col, name))
    for line, model, tier, shape in coverage_gaps(path, data):
        bad += 1
        print("%s:%d  %s  [effort]  claims %s but names no starting effort for %s -- give one, marked (unmeasured) "
              "when no source measured it" % (path, line, model.split("(")[0].strip(), tier, shape))
    for line, model, level in avoided_efforts(path, data):
        bad += 1
        print("%s:%d  %s  [effort]  names `%s`, which the row's registry effort.avoid lists -- end the range or "
              "move the shape to an effort the row allows" % (path, line, model.split("(")[0].strip(), level))
    print("\n%d cell(s) checked across %s, %d prose-contract hit(s)"
          % (len(rows), " + ".join(CHECKED_COLUMNS), bad))
    if bad:
        print("These cells state what the model TENDS to do and which effort to pin. Rewrite the "
              "clause as a tendency, or drop it -- see `.claude/commands/ladder_ingest.md`.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
