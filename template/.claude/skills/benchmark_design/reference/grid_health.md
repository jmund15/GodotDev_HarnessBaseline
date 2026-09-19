# Grid health signals — the body behind SKILL.md §4

Detail layer for `benchmark_design` §4. Run these on every collected grid before drawing any
conclusion. Any one signal invalidates the *bounds*; report the **grid** instead.
`bench.py health [task]` computes them per task.

## The signals

| signal | meaning |
|---|---|
| **SATURATED** | ≥60% of arms share one bound — it is not separating them |
| **NO CEILING** | the strongest arms pass every rung — cannot rank anything at or above them |
| **NON-MONOTONIC** | an arm passes a rung *above* one it failed — rungs are not ordered, so a "bound" is the first failure in an arbitrary sequence |
| **LOW EVENT COUNT** | ≤2 failures across the whole grid — one event is noise however many cells surround it |
| **RANK INVERSION** | a known-weaker arm outranks a reference arm — the axis is not ordering capability |
| **DEAD RUNG** | a rung that is nobody's bound separates no arm from any other |
| **PER-CELL FLAG AS GATE FINDING** | a judge's per-observation flag (confidence:low on one item in one cell) routed into the regression gate makes every newly scored cell a "regression" and `--accept` a ritual — publish it as an uncertainty band beside that cell's score, and gate only on RECURRENCE: the same item arguable in most cells of a task is the wording's defect |
| **TIE AT MAX** | two or more arms share the top value — the task ranks nothing at or above that tier; pick a new arm's tasks by unsaturated span, never by the catalog's hardness prose |

**Rank inversion needs a cross-arm check** — per-arm monotonicity cannot see it; every arm can be
internally consistent while the ranking between them is scrambled.
