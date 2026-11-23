---
name: Debugging Methodology
description: >-
  Use before proposing a fix for a bug, crash, regression, flaky result or slowdown whose root cause is unknown.
---

# Debugging Methodology

Applies to any bug in any domain: logic, behaviour, framework, build, runtime. Test-first development is `testing`'s.

## The Iron Law

No fix before the root cause is investigated. Proposing a fix before Phase 1 is done means stop and investigate: confidence is not evidence.

On a runtime failure, search auto-memory for the literal error text first, and run a control on the default branch's checkout before blaming a branch. Explain a stall or a slow run from a trace, never from theory.

## The Recurrence Law

A symptom reported again after a fix shipped for it is a new investigation with a higher bar, not a retry.

1. Presume the prior fix's root-cause claim wrong or partial: read it as evidence, never as foundation. A fix that repaired a real but adjacent defect keeps the symptom alive while every review reads it as progress. A different cause of the same symptom after a correct fix is recurrence too: name what admits both causes before fixing the next one.
2. Instrument before iterating. Log at the seams that tell the surviving hypotheses apart (where the object is against whether it rendered; event sent against event handled), so the next run decides even if the fix misses. A fix chosen on plausibility gets one attempt per symptom.
3. No fix ships without discriminating evidence: a repro seen red for the claimed cause, or a log line only that cause produces.
4. A brief that sends a recurrence to a delegate carries these three points.
5. A user report describes what was rendered, not the state behind it. "Always charged" or "never saves" is what the user saw; first find which layer diverged, the state or the view bound to it. A stuck view reads exactly like stuck state.

## Phase 0 — Check the symptom rows

Check the symptom against the project's symptom rows before building a loop; the repo's `CLAUDE.md` names its symptom-router file. A row's route owns the depth: follow it, do not re-derive it. One row holds everywhere: a test command that hangs forever is usually output piped through `tail` or `head`, which buffer the whole stream, or a timeout that orphaned a child holding a pipe; rerun without the pipe.

## Phases 1–6 — read each phase's file at its step

| Phase | The step produces | Read |
|---|---|---|
| 1 Loop | memory searched; one fast, deterministic, agent-runnable pass/fail signal, seen red | `skills/debugging/reference/phase1-loop.md` |
| 2 Reproduce | the user's symptom reproduced, minimised until every element is load-bearing, recent changes read | `skills/debugging/reference/phase2-reproduce.md` |
| 3 Pattern | a working example found and its difference from the failing site named | `skills/debugging/reference/phase3-pattern.md` |
| 4 Hypothesise | every surviving hypothesis written with its kill condition, tested one at a time | `skills/debugging/reference/phase4-hypothesise.md` |
| 5 Fix | a failing test at the right seam, one fix, the original scenario re-run | `skills/debugging/reference/phase5-fix.md` |
| 6 Clean up | loop green, diagnostic logs removed, the gotcha saved to memory | `skills/debugging/reference/phase6-cleanup.md` |

## Rationalizations to refuse

Catching yourself in any of these means return to Phase 1 and gather evidence.

| Rationalization | Answer |
|---|---|
| "Should work now" / "probably fixed" | Cite the evidence (test output, log line, exit code) or say it is unverified. |
| "Three fixes failed, the next will work" | Without discriminating evidence for the next idea, stop and question the design: a cold restart, `architecture_brainstorm`, or the owner. |
| "I see what's wrong, let me patch it" | Confidence is not evidence; build the loop. |
| "The error is obvious, no need to reproduce" | An obvious error that resists a one-line fix is usually misdiagnosed. |
| "I'll test the top hypothesis without writing the others" | One hypothesis anchors. Write all survivors with kill conditions first. |
| "The loop passes, so the bug is gone" | A loop never seen red proves nothing. |
| "That seam is correct" | Ruling out narrows the search; name where it moves next. |
| "I'll add the test once the fix works" | You will fit the test to the fix. Write it from the failure first. |
| "Let me also fix this other thing" | Bundled fixes cannot be bisected. One fix per attempt. |
| "It doesn't reproduce, but I know the cause" | Without a repro the fix cannot be verified. |
| "I can't build a loop, I'll read the code" | Say so, add temporary logging, or ask for the environment or a captured artifact. |
