# Known-score probes — the body behind SKILL.md §5

Detail layer for `benchmark_design` §5: the simulated answers whose correct score you already know, run
before any real arm. **Build these as code, not as prompts.** The positive control an all-fail tier
needs, and the sub-floor ordering claim, live in `controls_and_subfloor.md`.

## The probes

- **REACHABILITY** — the keyed answer is re-derivable from the evidence the key itself cites. **The
  highest-value probe**: a mis-keyed item penalises every arm equally, never appears as an outlier, and
  reads as a difficulty wall.
- **ANSWERABLE-FROM-PROMPT** — every token the key demands appears in the prompt the arm is *shown*.
  Validating against material you do not render is the same defect one level up.
- **PERFECT** — a fully-informed answer scores full. Catches an unpassable rung.
- **GUESSER** — an answer produced without consulting the evidence scores zero.
- **DECOY** — the shallow answer is graded wrong.
- **CONTRADICT** — PERFECT plus planted contradictions of a named constraint source; the expected figure is
  exact. A precision or false-positive term that has only ever returned zero is unverified, not clean.
- **SINGLE-BRANCH** — the keyed answer is not reachable from any one input alone.
- **MONOTONIC / NESTING** — rung *n+1* is strictly harder and contains rung *n*'s material, so an
  adjacent failure is a difficulty step rather than a different question.

## What probes cannot prove

**Probes prove satisfiability; only real answers reveal brittleness** — every probe can pass while the
rule is satisfiable *only by bad writing* (§7). Budget a review of real responses before trusting a grid.

## A universal miss is a key defect

Detect it mechanically: any item missed by every graded arm is excluded from the conclusions until
re-derived — and it is never a denominator. A "reachable ceiling" built from what arms have found
rescales the instrument by the field it measures and moves every time a cell runs; the scale stays the
ideal result, and the misses are an audit queue (KEEP / REWRITE / REMOVE per item).
