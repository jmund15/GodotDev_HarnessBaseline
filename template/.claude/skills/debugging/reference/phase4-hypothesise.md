# Phase 4 — Hypothesise + Test

Read at Phase 4 of [`../SKILL.md`](../SKILL.md).

## Rank the hypotheses *visibly* — anti-anchor

Rank every surviving hypothesis before testing any, each with its kill condition. Single-hypothesis generation anchors on the first plausible idea.

Each hypothesis carries its **kill condition**, the observation that would end it:

> Format: "If `<X>` is the cause, then doing `<Y>` must show `<Z>`. If not-`Z`, the hypothesis is dead."

A hypothesis without a kill condition is a hunch: sharpen or discard it.

**Write the ranked list visibly, then proceed.** Do NOT block for user approval of the ranking: Auto Mode prefers continuous execution, and the user often re-ranks instantly with domain knowledge and will interject.

## Test ONE at a time — anti-stack-tracking

Test the top hypothesis only; don't form Hypothesis 2 until Hypothesis 1 is confirmed or refuted. Multi-hypothesis fixes ("fix A, B and C — one is the bug") destroy the evidence: you will not know which fix worked.

## "This seam is correct" is never a terminal finding

Clearing a component narrows the search space; it does not close the investigation. Every refutation ends by naming where the search moves next: the adjacent seam, the layer upstream, or the evidence gap now needing filling. A session ending on *"the code looks right"* has not debugged; report the narrowed space and the next target.

If the top hypothesis is refuted, re-rank the remaining hypotheses. Generate new hypotheses in flight only when the test result changed the field; that is a return to Phase 2, not a Phase 4 mid-step.

## Tool preference for testing

1. **Test assertions** that distinguish hypotheses. One sharper assertion beats ten logs.
2. **Debug-level logs** at the boundaries that distinguish hypotheses.
3. **Never "log everything and grep"**: that turns Phase 4 into a log-reading swamp.

## Tagged-log convention — `[Subsystem][DIAG-<id>]`

Use the logger's **Debug level** as the diagnostic channel: ephemeral, and it does not trigger a test failure the way an error-level line can. Compose the tag after the subsystem prefix, e.g. `[Ability][DIAG-a4f2] cast state=<state> target=<target>`. The `[DIAG-]` half is unique to the session (pick four random hex chars), so Phase 6 cleanup is a single grep. The project's logging rule, where it ships one, owns the composition rule, level rules and canonical tag list.

## The Debug level is usually off

Debug is off by default; skip step 1 below and your instrumentation produces nothing, and you will think the code path didn't execute.

**Phase 4 procedure:**

1. Enable the Debug level (the project's logging rule names the switch).
2. Instrument every Phase 4 prediction with a `[Subsystem][DIAG-<id>]` log.
3. Run the loop, then slice the log by subsystem. Done when each prediction has a log that distinguishes it.

Removing the instrumentation is the Phase 6 checklist's done-condition. A `[DIAG-]` log that no same-session commit removes requires a worklog item.

## Performance branch

For performance regressions logs are usually wrong: adding them changes the timing enough to mask the bug. Instead:

1. Establish a baseline measurement (a monotonic timer, profiler, frame-time sampling).
2. Bisect the regression (`git bisect run`) against the baseline.
3. Measure first, fix second.
