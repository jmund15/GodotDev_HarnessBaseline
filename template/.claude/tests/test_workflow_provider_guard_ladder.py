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

| model | strengths | evidence | intel | deleg | speed | cost | taste | effort | plusminus |
|---|---|---|---|---|---|---|---|---|---|
| opus | architect & executor | bench | 8 | 5 | 4 | 4 | 7 | `xhigh` hardest design | + scoped work |
| sonnet | fan-out, validation | bench | 5 | 6 | 6 | 7 | 5 | high for depth | + surveys |
| haiku | scout | bench | 2 | - | 9 | 9 | 1 | low | + locate |

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

| tier | model | strengths | evidence | intel | deleg | speed | cost | taste | effort | plusminus |
|---|---|---|---|---|---|---|---|---|---|---|
| A | opus | architect & executor | bench | 8 | 5 | 4 | 4 | 7 | `xhigh` hardest design | + scoped work |
| B | sonnet | fan-out, validation | bench | 5 | 6 | 6 | 7 | 5 | high for depth | + surveys |
| C | haiku | scout | bench | 2 | - | 9 | 9 | 1 | low | + locate |
"""

NO_HEADING = """## Pick by work shape

| work shape | pin | also | never |
|---|---|---|---|
| deep review (T11) | `opus.xhigh` | `luna.max` | opus at `high` |
"""

RENAMED_COLUMN = LADDER.replace("| model | strengths |", "| row | strengths |")

# A strengths cell carrying tier tokens: registry `roles` alone decides what the row claims.
TOKEN_CELL = LADDER.replace("| opus | architect & executor |", "| opus | `scout` — design & execution |")

# A row whose strengths cell is prose; its tier claim lives in registry `roles`, as on every row.
REGISTRY_ONLY = LADDER.replace("| haiku | scout |",
                               "| sol | fresh architecting | bench | 7 | - | 3 | 5 | - | `high` only | + x |\n| haiku | scout |")
PLANTED_REGISTRY = {"transports": {}, "models": [
    {"alias": "sol", "id": "gpt-6-sol", "transport": "codex", "roles": ["architect"]},
    {"alias": "opus", "id": "claude-opus-5", "transport": "anthropic", "roles": ["architect", "executor"]}]}

EMPTY_TABLE = """## Role guidance

| model | strengths | evidence | intel | deleg | speed | cost | taste | effort | plusminus |
|---|---|---|---|---|---|---|---|---|---|
"""

# The evidence column removed: a table without it is schema drift, and every reader says nothing.
NO_EVIDENCE = LADDER.replace("| model | strengths | evidence |", "| model | strengths |").replace(
    "|---|---|---|---|---|---|---|---|---|---|", "|---|---|---|---|---|---|---|---|---|").replace(" | bench |", " |")

# Family rows and the scout pool: each cell covers every registry row of the families it names, on
# any transport. The `±` column carries the bar half a `capability:` citation is verified against.
FAMILY_LADDER = """## Role guidance

| model | strengths | evidence | effort | ± |
|---|---|---|---|---|
| luna | volume reading | bench+use | `low` converged | + surveys; − broad review seats |
| kimi | agentic coding | pub | `high` (unmeasured) | + long agentic runs; − vendor-only numbers |
| scout pool (minimax, hy) | locate and extract | pub | `low` (unmeasured) | + locate, enumerate; − never authors a patch or a spec |
"""
FAMILY_REGISTRY = {"transports": {}, "models": [
    {"alias": "luna", "id": "gpt-6-luna", "transport": "codex", "family": "luna", "roles": ["fanout", "scout"]},
    {"alias": "go-luna56", "id": "go/gpt-5.6-luna", "transport": "go", "family": "luna", "roles": []},
    {"alias": "go-kimi", "id": "kimi-k3", "transport": "go", "family": "kimi", "roles": ["fanout"]},
    {"alias": "go-minimax", "id": "minimax-m3", "transport": "go", "family": "minimax", "roles": ["scout"]},
    {"alias": "go-hy3", "id": "go/hy3", "transport": "go", "family": "hy", "roles": ["scout"]}]}


def _fixture_path(text):
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


# Cheapest first by the table's cost cell; the effort cell is never copied (its first rung names one shape).
EXPECTED = [
    "opus: architect & executor, cost 4",
    "sonnet: fan-out, validation, cost 7",
    "haiku: scout, cost 9",
]


def parse(text):
    return wpg.parse_ladder_role_lines(text.splitlines(True), {"models": []})


SUPP_LADDER = """## Role guidance

| model | strengths | evidence | intel | deleg | speed | cost | taste | effort | plusminus |
|---|---|---|---|---|---|---|---|---|---|
| haiku | scout | bench | 2 | - | 9 | 10 | 1 | low | + locate |
| flash | middling | bench | 4 | - | 8 | 8 | 2 | high | - false positives |
| plain | design | bench | 5 | - | 5 | 5 | 5 | high | + x |
"""
SUPP_REGISTRY = {"transports": {}, "models": [
    {"alias": "haiku", "id": "claude-haiku-5-5", "transport": "anthropic", "family": "haiku",
     "roles": ["fanout", "scout", "executor"], "supplementary": ["architect"]},
    {"alias": "flash", "id": "deepseek-flash", "transport": "deepseek", "family": "flash",
     "roles": ["architect", "executor", "fanout"], "supplementary": ["architect"]},
    {"alias": "plain", "id": "plain-1", "transport": "anthropic", "family": "plain", "roles": ["architect"]}]}

CASES = [
    # (name, callable -> bool, why it matters)
    ("work-shape rows are absent from the output",
     lambda: not any("deep review" in l or "scoped architecting" in l for l in parse(LADDER)),
     "the live defect: the second table's rows were emitted as role rows"),

    ("work-shape `also` cell never appears as an effort",
     lambda: not any("on the sidecar" in l for l in parse(LADDER)),
     "`also` sat at cells[-2] in a 4-column table, exactly where effort sits in the 9-column one"),

    ("every Role-guidance row emits its own model, role and cost, in ladder order (no sort reads as a pick)",
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

    ("a row prints every tier its registry `roles` claim, on any transport",
     lambda: {"opus: architect/executor, cost 4", "sol: architect, cost 5"}
             <= set(wpg.parse_ladder_role_lines(REGISTRY_ONLY.splitlines(True), PLANTED_REGISTRY)),
     "the registry is the only home of every row's tier claim"),

    ("a tier token in a strengths cell claims nothing: the registry decides",
     lambda: next(l for l in wpg.parse_ladder_role_lines(TOKEN_CELL.splitlines(True), PLANTED_REGISTRY)
                  if l.startswith("opus:")) == "opus: architect/executor, cost 4",
     "a cell restating a tier would drift from the registry; `model_registry.py --check` rejects it"),

    ("a supplementary-only tier prints after the plain tiers, marked",
     lambda: wpg.parse_ladder_role_lines(SUPP_LADDER.splitlines(True), SUPP_REGISTRY)[0]
     == "haiku: executor/fanout/scout +architect (supplementary), cost 10",
     "the role line is the one place a pinning session reads that a tier is a seat constraint, not a claim"),

    ("a tier held in both roles and supplementary is marked in place",
     lambda: wpg.parse_ladder_role_lines(SUPP_LADDER.splitlines(True), SUPP_REGISTRY)[1]
     == "flash: architect (supplementary)/executor/fanout, cost 8",
     "flash authors at architect and fills a review seat there only beside a qualified one"),

    ("a row with no supplementary entry prints as before",
     lambda: wpg.parse_ladder_role_lines(SUPP_LADDER.splitlines(True), SUPP_REGISTRY)[2] == "plain: architect, cost 5",
     "the marker must not touch a plain claimant"),

    ("the header carries the supplementary exception",
     lambda: "A pin must claim its job's tier, except a tier marked supplementary, which fills a review seat "
             "beside a qualified seat only (ladder §Role definitions); which claimant to use" in wpg.ROLE_LADDER_HEADER,
     "a session that reads only the header and role lines must learn the exception"),

    ("an empty Role guidance table returns []",
     lambda: parse(EMPTY_TABLE) == [],
     "no rows, no line, no raise"),

    ("empty input returns []",
     lambda: parse("") == [],
     "the degraded path the caller already treats as 'no ladder'"),

    ("a table without the evidence column returns []",
     lambda: parse(LADDER) == EXPECTED and parse(NO_EVIDENCE) == [],
     "a removed column is schema drift; the injection fails closed"),

    ("a family row and the scout pool print their members' registry tiers",
     lambda: wpg.parse_ladder_role_lines(FAMILY_LADDER.splitlines(True), FAMILY_REGISTRY)
     == ["luna: fanout/scout, cost ?", "kimi: fanout, cost ?", "scout pool: scout, cost ?"],
     "a ladder name resolves to every registry row of its family, or of each family a pool lists"),

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

    ok = all(re.search(r"^[^:]+: .+, cost (\d+|\?)$", l) for l in live)
    failed += not ok
    print("%s every live row is `model: tier, cost N`, whole tokens only" % ("ok  " if ok else "FAIL"))

    order = [r["model"] for r in wpg.model_registry.parse_ladder_rows(
        open(wpg._ladder_path(), encoding="utf-8").read().splitlines(), ("model",), wpg.LADDER_ROLE_HEADING)]
    ok = ([l.split(":", 1)[0].replace(" (excluded)", "") for l in live]
          == [re.sub(r"\s*\([^)]*\)", "", m).replace("`", "").strip().removesuffix(" excluded") for m in order])
    failed += not ok
    print("%s live rows keep the ladder's own order" % ("ok  " if ok else "FAIL"))

    ok = not any("on the sidecar" in l or "T11" in l or "T12" in l for l in live)
    failed += not ok
    print("%s no live work-shape row leaked into the injection" % ("ok  " if ok else "FAIL"))

    ok = any(l.startswith("sol: architect/executor/fanout, ") for l in live)
    failed += not ok
    print("%s the live sol row prints the tiers it claims in registry `roles`, like every row"
          % ("ok  " if ok else "FAIL"))

    ok = wpg.ladder_role_lines(path=os.path.join(HERE, "does-not-exist.md")) == []
    failed += not ok
    print("%s an unreadable ladder returns [] rather than raising" % ("ok  " if ok else "FAIL"))

    total = len(CASES) + 6
    print("\n%d/%d passed" % (total - failed, total))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
