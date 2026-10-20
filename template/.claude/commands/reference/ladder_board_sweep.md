---
disable-model-invocation: true
---

# Ladder board sweep — job brief

Read-only. You compare the battery's results board with the model ladder for the tasks your dispatch names, and report each place the board should change a pin and the ladder does not say so. You edit nothing.

## Inputs
- Ladder: `.claude/reference/model_ladder_evidence.md`: the `±` and `effort` cells, the `cost` column and the selection line.
- What a cell states: `.claude/commands/ladder_ingest.md` §What lands on the ladder.
- Tier claims: the file your dispatch names, which lists the rows whose registry `roles` claim each tier.
- Results board: `RESULTS-BOARD.md` in the vault battery folder your dispatch names. Read its "At a glance" table and your tasks' sections only. Each section has a ranked table, a run-to-run spread line and a judge-sensitivity band. Two rows tie where the board marks the lower one's rank `≈`; never re-derive the band yourself.
- Work shape per task: the **Archetype** line in `TASKS.md` beside the board.

## What counts as a gap
For each task, and each ladder family with current-instrument cells on it, compared against every row claiming that shape's tier:
1. **Cheapest reliable pick missing:** the row ties the strongest rows, costs less (`cost`), and its cell does not say it is the cheapest reliable pick for the shape.
2. **Strongest pick missing:** the row leads outside the band, and its cell does not say so.
3. **Failure missing:** the row falls well below the field, or fails a FLOOR item, where a pin could land, and its cell does not warn.
4. **Contradiction:** a cell claims something the board refutes outside the band.
5. **Effort mismatch:** another rung gives better quality per token on the shape than the `effort` cell names. A rung the board never ran is unmeasured, not a mismatch.
6. **Rung without merit:** a rung or shape the dominance rule in §What lands would propose removing: same-plan pins dominate it on every shape its cell claims for it. Run `bench.py ladder-dominance` on it and report the listing as a proposal, never an edit.
7. **Cell breaks a cell rule:** a rung in a `±` cell, an absolute in an `effort` cell, a shape term the ladder does not define, or a claim about the model or rung in general that rests on one task, one rung or runs whose spreads overlap.

Mark a result resting on one run, or on a gap narrower than the spread, as thin. A row is evidence only when the ladder-lag ledger counts it: ranked, current, and not stale, VOID, unverified, sealed, archived or a `control:` row. A board model name maps to the ladder row of its family (`model_registry.py` `family`); context or compaction variants are configurations of a row, not new rows.

## Return
1. A ```claims fence, one JSON object per line: `{"claim": "<the gap as a fact>", "evidence": [<items>]}`, each item `{"file": "<path>", "quote": "<verbatim text>"}`. Quote the board line that shows the result and the ladder cell as it stands.
2. A table: `| # | ladder row | shape (task) | kind (the numbered gap above, or thin) | what the cell should say, in ladder voice |`, one line per claim in fence order.
3. A coverage line per task: the ladder families you checked.
