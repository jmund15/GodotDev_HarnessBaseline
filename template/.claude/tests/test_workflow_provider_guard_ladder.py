#!/usr/bin/env python3
"""Proof for workflow_provider_guard.parse_ladder_role_lines (the role-ladder injection).

The parser reads ONE of the ladder's two tables. It used to read both positionally and inject the
work-shape table's `also` cell under an `effort:` label on every Workflow dispatch. Every case here
plants the shape that would let that regress: a second table with different columns, a reordered
header, an inserted column, a missing heading.

Fixtures are literal in-memory markdown -- no temp files, no live-file coupling. The live file is
checked separately at the bottom, because a proof that only ever sees its own fixture cannot tell a
working parser from one whose anchor no longer matches the SSOT.

Run: python3 .claude/tests/test_workflow_provider_guard_ladder.py
"""
import importlib.util
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "..", "hooks", "workflow_provider_guard.py")
spec = importlib.util.spec_from_file_location("wpg", MOD)
wpg = importlib.util.module_from_spec(spec)
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
spec.loader.exec_module(wpg)

# Both tables, in file order, with the column shapes the real ladder carries.
LADDER = """# Model ladder

## Pick by work shape

| work shape | pin | also | never |
|---|---|---|---|
| deep review (T11) | `opus.xhigh` | `luna.max` on the sidecar | opus at `high` |
| scoped architecting (T12) | `opus.high` | `muse.max` | sonnet |

## Role guidance

| model | role | intel | deleg | speed | cost | taste | effort | plusminus |
|---|---|---|---|---|---|---|---|---|
| opus | architect & executor | 8 | 5 | 4 | 4 | 7 | `xhigh` hardest design | + scoped work |
| sonnet | fan-out, validation | 5 | 6 | 6 | 7 | 5 | high for depth | + surveys |
| haiku | scout | 2 | - | 9 | 9 | 1 | low | + locate |

## Axis definitions

| axis | meaning |
|---|---|
| intel | raw reasoning |
"""

# Same content, one column inserted at the FRONT of each table. A positional reader shifts; a
# header-keyed one does not.
LADDER_SHIFTED = """## Pick by work shape

| tier | work shape | pin | also | never |
|---|---|---|---|---|
| A | deep review (T11) | `opus.xhigh` | `luna.max` on the sidecar | opus at `high` |

## Role guidance

| tier | model | role | intel | deleg | speed | cost | taste | effort | plusminus |
|---|---|---|---|---|---|---|---|---|---|
| A | opus | architect & executor | 8 | 5 | 4 | 4 | 7 | `xhigh` hardest design | + scoped work |
| B | sonnet | fan-out, validation | 5 | 6 | 6 | 7 | 5 | high for depth | + surveys |
| C | haiku | scout | 2 | - | 9 | 9 | 1 | low | + locate |
"""

NO_HEADING = """## Pick by work shape

| work shape | pin | also | never |
|---|---|---|---|
| deep review (T11) | `opus.xhigh` | `luna.max` | opus at `high` |
"""

RENAMED_COLUMN = LADDER.replace("| model | role |", "| row | role |")

# A role cell opening with two tier tokens: the row claims both tiers, so the line prints both.
TWO_TIER = LADDER.replace("| opus | architect & executor |", "| opus | `architect` `executor` — design & execution |")

# A non-Anthropic row whose role cell is prose; its tier claim lives in registry `roles` alone.
REGISTRY_ONLY = LADDER.replace("| haiku | scout |",
                               "| sol | fresh architecting | 7 | - | 3 | 5 | - | `high` only | + x |\n| haiku | scout |")
PLANTED_REGISTRY = {"transports": {}, "models": [
    {"alias": "sol", "id": "gpt-6-sol", "transport": "codex", "roles": ["sol", "architect"]},
    {"alias": "opus", "id": "claude-opus-5", "transport": "anthropic", "roles": ["opus"]}]}

# Malformed role cells: the guard prints the tiers it could read and never raises.
MISSPELLED = TWO_TIER.replace("`architect` `executor`", "`architect` `excutor`")
UNCLOSED = TWO_TIER.replace("`architect` `executor`", "`architect")
EMPTY_TABLE = """## Role guidance

| model | role | intel | deleg | speed | cost | taste | effort | plusminus |
|---|---|---|---|---|---|---|---|---|
"""

EXPECTED = [
    "opus: architect & executor, xhigh",
    "sonnet: fan-out, validation, high",
    "haiku: scout, low",
]


def parse(text):
    return wpg.parse_ladder_role_lines(text.splitlines(True))


CASES = [
    # (name, callable -> bool, why it matters)
    ("work-shape rows are absent from the output",
     lambda: not any("deep review" in l or "scoped architecting" in l for l in parse(LADDER)),
     "the live defect: the second table's rows were emitted as role rows"),

    ("work-shape `also` cell never appears as an effort",
     lambda: not any("on the sidecar" in l for l in parse(LADDER)),
     "`also` sat at cells[-2] in a 4-column table, exactly where effort sits in the 9-column one"),

    ("every Role-guidance row emits its own model, role and effort",
     lambda: parse(LADDER) == EXPECTED,
     "one line per row, keyed by header name"),

    ("a column inserted into EITHER table does not shift the emitted cells",
     lambda: parse(LADDER_SHIFTED) == EXPECTED,
     "the whole reason S1 blocks every other ladder edit"),

    ("a following section ends the table",
     lambda: not any("raw reasoning" in l for l in parse(LADDER)),
     "Axis definitions is also a `| ` table and must not bleed in"),

    ("a file with no Role guidance heading returns []",
     lambda: parse(NO_HEADING) == [],
     "fail silent, never fall back to whatever table is present"),

    ("a renamed required column returns [] rather than guessing",
     lambda: parse(RENAMED_COLUMN) == [],
     "schema drift must not emit a plausible-looking wrong row"),

    ("a row whose role cell opens with two tier tokens prints both tiers",
     lambda: parse(TWO_TIER)[0] == "opus: architect/executor, xhigh",
     "reading only the first token hid the row's second tier from every Workflow dispatch"),

    ("a tier claimed only in registry `roles` prints, beside the row's role prose",
     lambda: wpg.parse_ladder_role_lines(REGISTRY_ONLY.splitlines(True), PLANTED_REGISTRY)[2]
             == "sol: architect (fresh architecting), high",
     "the registry is the only home of a non-Anthropic row's tier claim; the line hid it"),

    ("a misspelled second tier token prints the tier it could read, without raising",
     lambda: parse(MISSPELLED)[0] == "opus: architect, xhigh",
     "the guard never crashes a dispatch; `model_registry.py --check` names the bad token"),

    ("an unclosed backtick prints the role prose, without raising",
     lambda: parse(UNCLOSED)[0].startswith("opus: architect"),
     "an unreadable token run falls back to the cell's words"),

    ("an empty Role guidance table returns []",
     lambda: parse(EMPTY_TABLE) == [],
     "no rows, no line, no raise"),

    ("empty input returns []",
     lambda: parse("") == [],
     "the degraded path the caller already treats as 'no ladder'"),
]


def main():
    failed = 0
    for name, fn, why in CASES:
        try:
            ok = bool(fn())
        except Exception as exc:                    # a raise is a failure, not a crash
            ok, why = False, "raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s" % ("ok  " if ok else "FAIL", name))
        if not ok:
            print("       %s" % why)

    # Live-file coupling: the anchor and column names must still match the real SSOT.
    live = wpg.ladder_role_lines()
    ok = len(live) >= 5
    failed += not ok
    print("%s live ladder parses to %d role rows (anchor still matches the SSOT)"
          % ("ok  " if ok else "FAIL", len(live)))

    ok = all(re.search(r"^[^:]+: .+, (low|medium|high|xhigh|max)$", l) for l in live)
    failed += not ok
    print("%s every live row is `model: tier, effort`, whole tokens only" % ("ok  " if ok else "FAIL"))

    ok = not any("on the sidecar" in l or "T11" in l or "T12" in l for l in live)
    failed += not ok
    print("%s no live work-shape row leaked into the injection" % ("ok  " if ok else "FAIL"))

    ok = any(l.startswith("sol: architect (") for l in live)
    failed += not ok
    print("%s the live sol row prints the `architect` tier it claims in registry `roles`"
          % ("ok  " if ok else "FAIL"))

    ok = wpg.ladder_role_lines(path=os.path.join(HERE, "does-not-exist.md")) == []
    failed += not ok
    print("%s an unreadable ladder returns [] rather than raising" % ("ok  " if ok else "FAIL"))

    total = len(CASES) + 5
    print("\n%d/%d passed" % (total - failed, total))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
