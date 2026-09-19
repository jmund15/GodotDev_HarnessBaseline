# Configurations and outcomes — the bodies behind SKILL.md §8, §9 and §10

Detail layer for `benchmark_design` §8 (configurations are not repetitions), §9 ("never asked" and
"answered badly" must never look alike) and §10 (closed-book means proving it). Corpus pinning and
transport labelling live in `configuration_and_transport.md`.

## Stamping a configuration

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
  number beside them. Check the payload at plan time and in the gate (`build_plan.frame_check`, C10) —
  recall judging credits prose as readily as JSON, so nothing downstream will notice.

## Absent, degenerate and off-axis outcomes

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

## Closed-book means proving it

- Agents with tools can look the answer up. **Perturb the corpus** so a lookup returns a value that is
  *wrong*, not a shortcut — then a fetched answer is detectable rather than rewarded.
- Record self-reported tool use and mark contaminated rows; **record them, never drop them** — a
  silently missing arm reads as an arm that was never asked.
- Treat self-reports as a **floor, not a proof**.
