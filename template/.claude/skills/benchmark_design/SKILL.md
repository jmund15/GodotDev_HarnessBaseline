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

**An instrument that has stopped measuring does not look broken — it looks tidy.** Every check below
is mechanical rather than editorial for that reason.

**Before designing or citing an instrument, run its preflight report** — it must say which scores are
safe, which need only arithmetic, which need a paid re-judge, and whether integrity checks passed.
Never hand-assemble an answer from scattered data roots; a partial file list creates false absence.

## 1. The one design law: scale DEPTH, not COUNT

A rung must require a **qualitatively harder inference** than the rung below, not more instances of
the same one. Count scales tedium: a cliff wherever the arm breaks, no gradation either side, so the
middle rungs separate nobody.

| count-scaled (fails) | depth-scaled (works) |
|---|---|
| sum N numbers, N rising | traverse N sequential hops, each knowable only after the last |
| copy N labelled items | resolve a conflict whose ordering signal gets subtler per rung |
| plant N irrelevant facts | plant one fact whose *relevance to the task* rises per rung |

### Repetition is not depth — the trap that catches the fix

"Follow N sequential hops" looks like depth and is not. **N lookups is still N of the same
operation** — eight steps that cannot go wrong are eight easy things.

**What makes a step hard is a DECISION that can go wrong**, so errors compound instead of accumulate:

- several plausible continuations at each step, resolved by a stated rule
- a dead end that must be recognised and backed out of
- a step whose result changes how the next step is interpreted

**Litmus, in two parts:** *if I doubled a rung's size, would a competent arm need a new capability or
just more patience?* — and — *can any single step go wrong in a way the arm must detect?* If every
step is a lookup, the axis is still count.

### The axis must be CONSTRUCTED, not observed

**An observed quantity cannot be an axis**: mining a corpus for the property at level N fails because
the corpus sets it — a graph that fans out by 2 or by 99 yields nothing for most rungs and a
context-pressure rung where it yields anything.

**Invert it: make the rule range over what you RENDER.** "Among the candidates *included below*,
follow the one with the most references" makes branching a build parameter while every file, reference
and value stays mined from real data. The move is *find data with property P at level N* → *present
exactly N*.

**Constructing one parameter and leaving its neighbour observed fails the same way.** Rendering
exactly k candidates is worthless if the right one is obvious. Constrain the margin too.

**Check the selection criterion is INDEPENDENT of what the item structurally requires.** If the step
the chain must continue through is by construction the one the rule selects, the rule is decidable
without doing the work, and no metric fixes a structural conflict. Escape: a criterion orthogonal to
continuation — an authored value, not a structural one — which also restores tight margins.

Two obligations come with constructing an axis:

- **Hold the confound flat in the unit that causes it.** Flat *document count* is not flat prompt
  length; pad to a fixed CHARACTER budget and assert the spread (≤25%). A rung that failed because its
  prompt was longer is measuring the neighbouring axis.
- **Assert the ladder rises.** Difficulty is computed (hops × branching), so check monotonicity in the
  builder. A staircase whose difficulty does not rise is not one, and the grid will not say so.

**Corpus constraints are coupled.** Each rung's answer target AND answer value is one joint assignment — solve it exhaustively, never greedily (scarcest-first fails too); when infeasible, change a rung's parameters, not the search's strictness; one predicate, one implementation (survey with the code that builds). Procedure: `reference/staircase_construction.md` §Corpus constraints.

## 2. Calibrate BEFORE collecting — two calls decide whether the sweep is worth running

- **CEILING** — the **weakest** arm on the **hardest** rung must **FAIL**. If it passes, the
  instrument's ceiling sits below the entire field and nothing can be bounded. **Conclusive.**
- **FLOOR** — the **strongest** arm on the **easiest** rung must **PASS**. If it fails, suspect the key
  before the arm: a bottom rung a strong arm fails is usually unanswerable, not hard. **Suspicious**,
  not conclusive.

**Run it against the cheapest reference arm too**, not only the weakest local one — the cheap cloud arm
is the ceiling that matters when the decision is "escalate or not".

**A monotone SCALAR does not make a monotone LADDER, and a ceiling failure has TWO causes.** Say which KIND of step each rung adds and check the ladder nests in kind as well as count; read a non-monotonic result as a dissociation before noise. Decide a ceiling failure from the TRACE, never the pass rate: the arm SHORTCUT it (a rule decidable at a glance ⇒ rebuild) or EARNED it (the full chain walked ⇒ the instrument is sound, report a FLOOR for the tier it cannot separate; never extend a ladder until somebody finally fails). Table and evidence: `reference/staircase_construction.md` §Ladder ordering.

## 3. Replication — the two directions carry very different weight

**One PASS is conclusive. N FAILURES are not.** A weak arm clearing the hardest rung once proves it
clearable, so the ceiling is below the field. 0 passes in *n* trials only bounds the true pass rate.

| trials with 0 passes | true pass rate could still be as high as |
|---|---|
| 1 | ~95% |
| 3 | ~63% |
| 5 | ~45% |
| 10 | ~26% |

So **n=1 refutes a ceiling but never establishes one**. Treat 3 as the floor for a calibration claim
and say what the number does *not* prove. A single failure reported as "the arm cannot do this" is the
most inviting overstatement in this discipline.

**Determinism is per arm class, and a rep that changes nothing is theatre.**

- **Local, fixed seed, single-shot — NOT byte-identical on a GPU. Verify it; never assume it.** A seed
  fixes the SAMPLER'S DRAWS, not the DISTRIBUTION: GPU reduction order, quantised KV-cache (`q8_0`)
  rounding and batch shape inside a multi-arm sweep all move it. Assuming byte-identity is what
  licenses collecting a battery at n=1. **Check it with two runs of one cell and a byte compare.**
- **Separate the two variance questions, because they indict different things.** PINNED-seed reps
  measure *reproducibility*, which decides whether a grid can be published; VARIED-seed reps measure the
  arm's *sampling variance*. Pooled they answer neither: unstable-at-fixed indicts the harness,
  unstable-only-at-varied indicts the arm.
- **A conjunctive pass bit is the least stable statistic in the grid.** ANDing k field checks inherits
  the union of k error rates while reporting one bit. Report the components beside it.
- **A ONE-SEED BOUND CAN BE BIASED, NOT MERELY NOISY.** Only VARIED seeds measure the arm — fixed-seed
  reps are one measurement repeated on a deterministic arm and harness noise on a non-deterministic one.
  A single draw of a transition-zone cell can sit on the wrong side of it, understating an arm by a
  whole rung. **Replicate every BOUNDARY cell across seeds and report a pass RATE**, not a bound.
- **Cloud** — stochastic without help; repeat the call.
- **Multi-turn / agentic** — non-deterministic even locally; the fixed-seed exemption does not apply.

**Repeat with independent agents, not one agent asked N times** — an agent seeing its own earlier
attempts measures self-consistency, not reliability.

**A bound is a reliability claim**: a rung passes only when EVERY rep passes. And a **flaky bottom rung
invalidates everything above it** — every bound inherits that instability.

**Produce a boundary worklist, not a point estimate.** For each task, list the cheapest-competent pin
and the pin one rung or tier below it, with sample counts. Replicate those cells. For each model/task,
also test the adjacent effort rung: lower when the current pin is maximal or tied; higher when room
remains. Close the bracket only when a pass sits beside a fail or adjacent uncertainty bands overlap.
A cited pin is a closed bracket, never a single rung.

## 4. Health checks — run on every collected grid, before drawing any conclusion

Seven mechanical signals. Any one invalidates the *bounds*; report the **grid** instead.

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

## 5. Validate against known-score agents before any real arm runs

Simulated answers whose correct score you know. **Build these as code, not as prompts.**

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

**Probes prove satisfiability; only real answers reveal brittleness** — every probe can pass while the
rule is satisfiable *only by bad writing* (§7). Budget a review of real responses before trusting a grid.

**A universal miss is a key defect, not a finding.** Detect it mechanically: any item missed by every
graded arm is excluded from the conclusions until re-derived — and it is never a denominator. A
"reachable ceiling" built from what arms have found rescales the instrument by the field it measures and
moves every time a cell runs; the scale stays the ideal result, and the misses are an audit queue
(KEEP / REWRITE / REMOVE per item).

**A tier where NOTHING passes needs a positive control before it can be read** — the parent's corpus byte-for-byte with only the task header swapped, its own instrument name (never gate-legal for the parent axis), graded in stages, an answer no blind guess lands on, run at several seeds and passed if ANY passes. **A SUB-FLOOR rung is an ordering claim checked per arm**: run it for every arm collected before it existed, and never report a bound across a gap. Recipe: `reference/controls_and_subfloor.md`.

## 6. Make guessing worthless

- **High-entropy answers.** A terminus of 0, 1, 2 or 3 is guessable without doing the work. Prefer the
  most distinctive value available and say so when none exists.
- **Decoy floor.** Pad so a blind guess is worth ≤1/12. Three rendered documents means a 1-in-3 guess
  contaminates the bound.
- **Decoys must be indistinguishable by shape.** Every decoy declares the same attribute as the answer,
  so scanning returns many candidates and only the real work picks one.
- **The join operator must not be satisfiable from one input.** A *maximum* returns one of its inputs,
  so a single-branch agent scores correct without joining; use a **sum**, a count, or a traversal.
- **Verify decoys are unreachable.** A decoy the real path can reach creates a second legal answer.

## 7. Scoring — the failure mode is punishing correct behaviour

**Before you believe a scorer, read six raw answers against the key by hand — once, out loud.** A broken
scorer emits clean, confident numbers, and the shape it emits most often is *the arm failed* —
indistinguishable from the finding a calibration exists to produce. Every other rule here says what to
fix; this one is how you learn there is anything to fix.

**A scorer that has only ever scored WRONG answers has not been tested.** Rejecting silence cannot prove
a scorer accepts a right answer. Three fixtures: silence must fail *as undelivered*, an exact correct
answer must pass, and a wrong-but-well-shaped answer must fail *as delivered*.

**Score-dict keys are a SHARED NAMESPACE across instruments, because the collector formats from them.**
A name reused with a different SHAPE crashes the collector rather than the scorer, where it would be
obvious. **Gate paired keys on both members, never the first**; and **give a new instrument's fields
distinct names** rather than borrowing a neighbour's.

- **Do the hand-check on the FIRST run against real responses**, not after a result looks strange — a
  wrong result matching your expectations never looks strange.
- **Test the scorer on a KNOWN-CORRECT answer**, not only on an empty or garbage one; the two failures
  point opposite ways.
- **The scorer's read-set is the arm's input set.** Every constraint source the key cites is handed to the judge or
  verifier that applies it; a verifier that cannot see a document the arm was shown charges a quote of it as a
  fabrication.
- **Parse the field the prompt asked for.** Scanning for the first, last, or any integer reads the arm's
  *working* rather than its answer.
- **Never conflate instruction-following with the measured capability.** Accept a bare answer when the
  wrapper is not the thing being measured.
- **Never require the absence of a term the correct answer must contain.** A migration note must NAME
  what changed, so scoring supersession by requiring the stale token absent scores textbook answers
  zero. Score the **claim**, not the token.
- **Never require a literal word from the source when paraphrase is correct behaviour.** Report overlap
  as a diagnostic; do not gate on it. **Its fingerprint is a RANK INVERSION, not a low score** — such a
  key rewards copiers and penalises writers, so the weakest model finishes above every reference arm and
  the grid stays orderly. Treat any inversion of a known capability ordering as a scorer bug until
  proven otherwise; that ordering is the only oracle a scorer has.
- **The ANSWER must be unique, not just the path to it.** "Report the value of `collision_layer`"
  against a `.tscn` whose many nodes each declare one keys the last occurrence while a top-down reader
  reports the first, scoring a correct arm zero. Restrict answers to properties occurring exactly once
  — tie-rejection applied to the answer: a rule with two legal outcomes cannot be scored.
- **Normalise case and separators.** `caster-separation` must match "Caster separation". Fold
  separators; keep the word sequence exact so an omission still fails.
- **Scope proximity rules to the sentence, not a character window** — a window wide enough to catch a
  real marker also reaches the neighbouring list entry.
- **Score sub-axes separately when they dissociate.** Refutation and aggregation are different
  capabilities; an arm can excel at one and fail the other in the same cell.
- **Be strict in the direction that UNDERSTATES.** A bound too low costs some routing headroom; a bound
  too high sends real work to an arm that cannot do it.

### Judge tier and key freshness

- **A judge's anchor is a claim about the deliverable, and the deliverable can check it.** Match every
  found-vote quote mechanically against the deliverable, one span at a time when text is spliced.
  Discard unverified anchors and count the rejection. Before changing the match threshold, calibrate it
  against real accepted anchors and a planted fabrication.

- **A recognition key tolerates a judge a tier below the arm.** Deciding whether a listed item was FOUND
  is recognition — the judge never has to produce the item — so the panel need not match the arm. An
  open-surface lens (unique valid defects, no item list) is generation: its judge must be able to
  produce the finding to weigh it, and a weaker judge inverts the ordering.
- **Beyond-key yield is the key's staleness signal.** Arms that keep returning valid claims outside the
  item list are measuring what the key knew, not what the arm can do. Assemble the beyond-key set as
  its own score and refresh the key when it grows; a key that never grows was never checked.
- **A new task scores OUTCOMES, never opinions.** Every item is a claim the root can verify — a file, a
  line, a behaviour. A judge asked whether the arm "argued well" scores its own taste.

### The instrument's TRANSPORT must match the deployment's transport

Name each deployment shape the battery routes and label every axis with the shape it tests. A FAIL measured on the richer transport (tools, loop) holds for the poorer one; a PASS establishes nothing about it; a transport stamp prevents pooling and does not make a verdict transfer. Detail: `reference/configuration_and_transport.md` §Transport.

## 8. Configurations are not repetitions

A change of context window, cache precision, quantisation, output cap, or prompt revision creates a
**different configuration**. Pooling averages a resident cell with a spilled one, or ANDs a stale
verdict into a corrected rung.

- **Stamp every row** with its configuration. A property unreadable from the API (server-start settings)
  is invisible unless recorded deliberately.
- **A stamp read from the CLIENT is a belief, not a measurement — and it fails silently.** The runner's
  env var stamps what the caller *intended*, and a faithfully-stamped row is indistinguishable from a
  controlled variable. Read the setting back from the server where the API exposes it; where it does
  not, **validate the stamp with one control cell at a deliberately-set alternate value** — identical
  results across two stamps otherwise mean either the parameter does not matter or both runs were the
  same configuration.
- **Stamp non-applicable rows explicitly** (`n/a`) rather than with a plausible default — a wrong
  literal reads as measured, where a blank reads as unknown.
- **Archive superseded rows; never delete — and never display them beside current rows.** A deleted row is
  indistinguishable from a cell nobody ran; a superseded row on the board reads as a comparison however it is
  marked. Re-run the pin on the current instrument; the old figure is history only.
- **The deliverable's FORM is part of the frame.** A cell that returned prose where its peers returned
  the task's schema JSON ran a different configuration: publish it as VOID-across-frames, never as a
  number beside them. Check the payload shape at plan time and in the gate — recall judging credits
  prose as readily as JSON, so nothing downstream will notice.

**Hold the corpus fixed and vary only the REQUEST** wherever the axis allows — one byte-identical corpus per rung removes the length confound instead of balancing it. **The corpus pin must cover EVERY input**: pin every SHA the builder reads and record them in the key; score across arms on the item-id INTERSECTION, reported with its size; diff the item set whenever an arm is added late. Detail: `reference/configuration_and_transport.md` §Corpus pin.

## 9. "Never asked" and "answered badly" must never look alike

The most repeated defect across every generation: **a runtime or configuration limit misread as a
property of the model** — an overflow pin, a clamped context window, a killed run recorded as a spill,
an output cap truncating prose.

- Mark unasked cells with a distinct outcome (`OVERFLOW_SKIP`) and report them separately from failures.
  **The rule binds the REDUCERS, not just the scorer** — every walker, percentage, roll-up and console
  summary is a fresh chance to map absent onto failed, and the language's defaults do it for free. Carry
  the third state in the type and assert the buckets sum to the source row count:
  `arch_rule_absent_input_needs_its_own_outcome_value`.
- **Re-deriving from a stored response has an ABSENT-INPUT branch, and it is not `False`.** A row with no
  stored response has nothing to re-derive, so scoring it returns a fail for a cell nobody graded. Fall
  back to the recorded verdict and mark the row not-re-derivable.
- **DEGENERATION is a third outcome and it arrives labelled `ok`.** A repetition loop returns successful
  status, a full token budget, and no answer — it looks like a fail and is not one; the arm never
  attempted the task. Detect it mechanically before scoring (`len(set(tail)) <= 2`) and report it as its
  own class. Routing consequence a pass/fail grid hides: the model produces garbage rather than a
  partial answer, so any deployment trusting its output needs a shape check, not a plausibility check.
- **Check truncation directly.** Backends may truncate silently and discard the *front* of a prompt —
  where the corpus sits — while the question at the end survives, which is indistinguishable from a
  comprehension failure. Compare tokens actually evaluated against the real prompt length.
- **Re-derive from stored responses**, not recorded verdicts, so a scorer fix reaches collected cells.
- **A hole in the prompt produces a well-formed wrong answer.** A template interpolating a field the
  payload does not carry hands the arm fluent instructions containing `undefined`, and it returns a
  schema-valid empty result — exactly what a capability failure looks like. Two cheap defences: assert
  every interpolated field is present and non-empty BEFORE dispatching, and treat **zero tool calls as a
  harness fault** reported separately and never scored.
- **Never route material that must arrive verbatim through a model.** A subagent asked to return a file
  "exactly as stored" may reshape it, and a permissive schema validates the reshaped object. If the
  orchestration layer cannot read files, the orchestrator pushes the data inline — delegation is for
  judgement, never for transport.
- **A staircase may stop only on an ON-AXIS failure**, one carrying evidence about the quantity the
  instrument scales; otherwise the reported bound names the wrong capability *and* discards the axis's
  own question. Watch for it wherever a **conjunctive pass bit degenerates at a ladder extreme**: one
  conjunct goes vacuous and the verdict silently becomes a test of a different capability — a no-leak
  AND grounding bit reduces at zero temptation to pure extraction, and arms stopped there with zero
  leaks can leak higher up.

## 10. Closed-book means proving it, not asking for it

- Agents with tools can look the answer up. **Perturb the corpus** so a lookup returns a value that is
  *wrong*, not a shortcut — then a fetched answer is detectable rather than rewarded.
- Record self-reported tool use and mark contaminated rows; **record them, never drop them** — a
  silently missing arm reads as an arm that was never asked.
- Treat self-reports as a **floor, not a proof**.

## 11. Reporting

- **Three states, never two.** A pin is measured, VOIDED (reason plus re-dispatch campaign named), or
  unmeasured by design (reason named). At every roster end, run a completeness check that halts on a
  dispatched cell with neither a record nor a void row. Give each new roster model a probe wave before
  its main sweep.

- **No composite scores across incommensurable axes.** Arms dissociate hard: one frontier-level on
  mechanism comprehension while inventing a value for every absent key lands mid-field under any
  average, erasing the exact failure the battery exists to catch.
- **Generate tables from data; never transcribe.** A stale figure copied forward reads exactly like a
  measured one and nothing in the document can contradict it. Print `--` for anything uncomputable.
- **Mark short denominators (LOW-N) on the number itself**, not in a footnote — otherwise a percentage
  over a small denominator is ranked against a full one. A percentage can move purely because the
  denominator did.
- **State each axis's scale beside it**, and what it must not be compared to.
- **List every unmeasured cell with its reason**, so a gap cannot be read as a result.
- **The scale is the ideal result; never rescale it by what arms found.** An arm-derived "reachable
  ceiling" measures the field, not the task, and moves whenever a cell runs; a low score on a hard task
  is the finding. Items no cell finds are the key's audit queue (§5), printed beside the scale.
- **One instrument generation per board.** Cells scored on an earlier generation are archive-only and
  their pins are re-run on the current frames — never shown beside current rows, never carried forward.
- **A ceilinged task is read as a gate, never a ranker** — derived from the cells (some current cell at
  the maximum), never authored per task: it answers "cheapest competent pin"; §4 TIE AT MAX is the same
  observation and needs no separate flag once the board prints the task as a gate.

## 12. Delegated corpus work needs independent re-derivation

- An adversarial verification pass returning **zero refutations is itself a signal** — equally consistent
  with accurate mining and with a rubber stamp, indistinguishable from the totals. Re-derive a sample
  mechanically from the pinned source.
- **Pin the corpus to one commit** and require every claim to be true *at that commit*. An item whose
  premise is a historical revision is unanswerable against a rendered snapshot.
- **Bound each item's evidence.** Bounding batch composition is not enough: one item citing a large
  accumulating file produced a prompt no arm could be asked at all.

## Anti-patterns

- **Don't add rungs to a saturated instrument.** If the ceiling is below the field, more rungs at the
  same *kind* of difficulty change nothing — change the axis (§1).
- **Don't read a bound off a flagged grid.** Report the grid.
- **Don't trust an orderly table.** Orderliness is what a dead instrument produces.
- **Don't fix a scorer without re-scoring collected cells** — verdicts are derived, responses are the
  durable artifact.
