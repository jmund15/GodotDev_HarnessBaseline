---
name: benchmark_design
description: >-
  Use when designing, building, scoring, auditing or extending any benchmark, eval or measurement
  instrument — model comparisons, capability staircases, judge panels, regression batteries. Covers
  the failure classes that make an instrument produce confident numbers while measuring nothing, and
  the checks that catch each one before budget is spent. SKIP for running an existing validated
  battery unchanged, and for ordinary test-writing (that is the testing skill).
---

# Benchmark Design

**An instrument that has stopped measuring does not look broken — it looks tidy.**

**For the model-effort battery, run the battery's `bench.py doctor` before
designing or citing anything** — it reports which published scores are safe, which need only
arithmetic, and which need a paid re-judge, plus the G4 integrity sweep. Never hand-assemble an
answer from the data roots; `bench.py where` lists all six.

Section numbers are cited across the harness and from `scripts/benchmark_campaign/` — never renumber.
Each section states its verdict and names the file to read at that step; read only the sections your
current step needs.

## 1. The one design law: scale DEPTH, not COUNT

A rung must require a **qualitatively harder inference** than the rung below, not more instances of
the same one.

**Litmus, in two parts:** *if I doubled a rung's size, would a competent arm need a new capability or
just more patience?* — and — *can any single step go wrong in a way the arm must detect?* If every
step is a lookup, the axis is still count.

While drafting rungs, read `reference/axis_design.md` §Count-scaled versus depth-scaled, §Repetition
is not depth, §The axis must be constructed, not observed, and §Two obligations that come with
constructing an axis. At corpus build, read `reference/staircase_construction.md` §Corpus constraints
are coupled for the joint-assignment procedure.

## 2. Calibrate BEFORE collecting

Two calls decide whether the sweep is worth running: the **weakest** arm must **FAIL** the **hardest**
rung (conclusive), and the **strongest** arm must **PASS** the **easiest** one (suspicious, not
conclusive). Read `reference/calibration_and_replication.md` §Two calls decide whether the sweep is
worth running before picking the calibration arms.

On a non-monotonic result or a ceiling failure, read `reference/staircase_construction.md` §A monotone
SCALAR does not make a monotone LADDER and §A ceiling failure has TWO causes — the disposition is
decided from the trace, never the pass rate.

## 3. Replication — the two directions carry very different weight

**One PASS is conclusive. N FAILURES are not.**

Read `reference/calibration_and_replication.md` at the step you are on: §What zero passes in n trials
bounds when sizing reps, §Determinism is per arm class when planning reps for a given arm, §Reading
reps into a bound when reducing them, and §Boundary reps and the effort bracket when choosing the next
cells to run.

## 4. Health checks — run on every collected grid, before drawing any conclusion

Mechanical signals, any one of which invalidates the *bounds*: report the **grid** instead. Read
`reference/grid_health.md` §The signals and run `bench.py health [task]` before writing a conclusion.

## 5. Validate against known-score agents before any real arm runs

Simulated answers whose correct score you know. **Build these as code, not as prompts.** Read
`reference/validation_probes.md` §The probes while building them, and §What probes cannot prove before
trusting a grid.

**A universal miss is a key defect, not a finding** — excluded from the conclusions until re-derived,
and never a denominator (`reference/validation_probes.md` §A universal miss is a key defect).

On an all-fail tier, or before prepending a rung beneath an existing floor, read
`reference/controls_and_subfloor.md` §A tier where NOTHING passes needs a positive control and §A
SUB-FLOOR rung is a claim about ordering.

## 6. Make guessing worthless

Answer values, decoys and the join operator all have to defeat a blind guess. Read
`reference/axis_design.md` §Make guessing worthless while authoring them.

## 7. Scoring — the failure mode is punishing correct behaviour

**Before you believe a scorer, read six raw answers against the key by hand — once, out loud.**

Read `reference/scoring.md` §A scorer that has only scored wrong answers is untested and §Scorer rules
while writing or auditing the scorer, §Score-dict keys are a shared namespace when naming score
fields, and §Judge tier and key freshness when choosing a panel, replacing a judge or refreshing a
key. That section is the one home of judge succession: exact-id pins, Admit by correctness, Pool by
agreement, the self-judge audit and the retirement warning.

Before citing a task, read `bench doctor` §0 and every lettered subsection under it: unruled gate
findings, tasks with no current record, probe state (§0c), descriptors without a `defaultPanel` (§0d),
judge availability (§0e), paid cells never scored, and a stale board or index.

When labelling an axis, read `reference/configuration_and_transport.md` §The instrument's TRANSPORT
must match the deployment's transport — a FAIL on the richer transport holds for the poorer one, a
PASS establishes nothing.

## 8. Configurations are not repetitions

A change of context window, cache precision, quantisation, output cap, prompt revision, or the model
version served behind an unchanged alias creates a **different configuration**. Identify the arm by
the model its record attests or the server reports, never by the dispatch token: a bare alias moves
to each new version. Pooling averages a resident cell with a spilled one, or ANDs a stale
verdict into a corrected rung. Read `reference/configurations_and_outcomes.md` §Stamping a
configuration before stamping or publishing any row.

At corpus build and again at scoring, read `reference/configuration_and_transport.md` §Hold the corpus
fixed and vary only the REQUEST and §The corpus pin must cover EVERY input.

## 9. "Never asked" and "answered badly" must never look alike

The most repeated defect across every generation: **a runtime or configuration limit misread as a
property of the model.** Read `reference/configurations_and_outcomes.md` §Absent, degenerate and
off-axis outcomes while building reducers and whenever a ladder stops.

## 10. Closed-book means proving it, not asking for it

Read `reference/configurations_and_outcomes.md` §Closed-book means proving it before running any
tool-equipped arm.

## 11. Reporting

A pin is measured, VOIDED, or unmeasured by design — three states, never two. Read
`reference/board_publication.md` §Three states, never two at every roster end, and §Writing the board
while writing it.

## 12. Delegated corpus work needs independent re-derivation

A verification pass returning zero refutations is a signal, not a clearance. Read
`reference/board_publication.md` §Delegated corpus work needs independent re-derivation after any
delegated mining pass.

## Anti-patterns

- **Don't add rungs to a saturated instrument.** If the ceiling is below the field, more rungs at the
  same *kind* of difficulty change nothing — change the axis (§1).
- **Don't read a bound off a flagged grid.** Report the grid.
- **Don't trust an orderly table.** Orderliness is what a dead instrument produces.
- **Don't fix a scorer without re-scoring collected cells** — verdicts are derived, responses are the
  durable artifact.
