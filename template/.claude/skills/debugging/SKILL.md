---
name: Debugging Methodology
description: >-
  Use before proposing fixes for bugs, crashes, regressions, flaky behavior or performance regressions whose root cause is unknown.
---

# Debugging Methodology

Applies to **any bug in any domain**: logic, behavior, framework, build, runtime. (The project's test-first rule, CLAUDE.md §Development Philosophy: Hybrid TDD, governs *test-driven development* instead.)

## The Iron Law

```
NO FIXES WITHOUT ROOT-CAUSE INVESTIGATION FIRST
```

Proposing a fix before Phase 1 is complete → **stop and investigate**. Confidence is not evidence.

On a runtime failure, search auto-memory for the literal error text first (`CLAUDE.core.md` §Proactive Context Loading), and run a control on the primary checkout before blaming a branch. Trace a duration before calling it normal: a stall or a slow run is explained from a trace, never from theory.

## The Recurrence Law

A symptom reported again after a fix shipped for it is a NEW investigation with a higher bar, not a retry:

1. Presume the prior fix's root-cause claim wrong or partial: re-read it as evidence, never as foundation. A fix that repaired a real-but-adjacent defect keeps the symptom alive while every review reads it as progress. A different cause of the same symptom after a correct fix is recurrence too: the causes share whatever admits them, so name it before fixing the next one.
2. **Instrument before iterating.** Add `[DIAG-<id>]` logs at the seams that DISCRIMINATE the surviving hypotheses (where the entity IS vs whether it rendered; event offered vs event consumed) so the next run is decisive even if the fix misses. Plausibility-based fixing is capped at ONE attempt per symptom.
3. No fix ships without discriminating evidence: a repro seen red for the claimed cause, or a log line only that cause can produce.
4. **This binds dispatched lanes:** `orchestration` reference `brief_checks.md` carries the brief obligation.
5. **A user report describes what was rendered, not the state behind it.** "Always charged", "never splits", "doesn't damage" are observations of what the user SAW; first discriminate which layer diverged, the state or the visual bound to it. A stuck overlay reads identically to a stuck state, and fixing the state a report seems to name while the visual lies keeps the symptom alive.

---

## Phase 0 — Symptom triage router

Check the symptom against the `symptom_router` reference's table before building a loop, where the project ships one. The Route column owns the depth; don't re-derive it.

---

## Phases 1–6 — read each phase's file at its step

Phase 0's Route column may end the investigation here; otherwise work the phases in order, reading the named file when you reach that step.

| Phase | What the step must produce | Read at this step |
|---|---|---|
| 1 LOOP | memory seed (`disposal`/`pool`/`process`) → one fast, deterministic, agent-runnable pass/fail signal, **seen RED** before Phase 2 | `.claude/skills/debugging/reference/phase1-loop.md` |
| 2 REPRO | the **user's** symptom reproduced, minimised until every element is load-bearing, recent changes checked | `.claude/skills/debugging/reference/phase2-reproduce.md` |
| 3 PATTERN | a working example found via LSP `findReferences`, and the difference from the failing site named | `.claude/skills/debugging/reference/phase3-pattern.md` |
| 4 RANK+TEST | every surviving hypothesis written visibly with its kill condition, tested one at a time; refuted ≠ done | `.claude/skills/debugging/reference/phase4-hypothesise.md` |
| 5 FIX | a failing test at the right seam first, then one fix, then verification against the original scenario | `.claude/skills/debugging/reference/phase5-fix.md` |
| 6 CLEAN | loop re-run green, `[DIAG-` removed, the Debug switch restored, the gotcha saved to auto-memory | `.claude/skills/debugging/reference/phase6-cleanup.md` |

## Red Flags & Rationalizations

Catching yourself in any of these → return to Phase 1 and gather evidence. The remaining rationalizations are tabled under `## detailed` at the end of this file.

| Rationalization | Reality |
|---|---|
| "Should work now" / "Probably fixed" / "Seems to be passing" | Documented [refused claims](../../commands/agents/orchestrator_action_protocol.md#claims-to-refuse). Cite evidence (test output, log line, exit code) or use future tense honestly. |
| "Three fixes failed but the next one will work" | When a fix fails and the next idea lacks discriminating evidence, STOP and question the design instead of trying another fix: `architecture_brainstorm`, a cold restart, or discussion with the owner before the next attempt. |

## Cross-references

**auto-memory:** `feedback_recommended_fix_means_implement.md` (inverse rule for *user-recommended* fixes; composes with the stop-and-question exit above) · `CLAUDE.core.md` §Preferences (no performative agreement: no *"you're absolutely right!"* opener on diagnostic feedback) · plus those cited inline above.

**Skills:** the project's testing skill, where it ships one (failing-test-first for Phase 5; suite classification) · [`architecture_philosophy`](../architecture_philosophy/SKILL.md) (lifecycle and constructor rules behind init-timing bugs; the Deletion Test) · [`architecture_brainstorm`](../architecture_brainstorm/SKILL.md) (handoff target when the architecture itself is the suspect)

**Commands:** the project's regression gate (`change_control` §Gate cadence names it; Phase 5 broader verification) · `/test_skill debugging` (adversarial validation of this skill)

## detailed

Read this section only if your session tier line says `detailed`.

### Red flags & rationalizations — the remaining rows

| Rationalization | Reality |
|---|---|
| "I think I see what's wrong, let me just patch it" | Confidence is not evidence. Run Phase 1 first. |
| "The error is obvious — no need to reproduce" | "Obvious" errors that resist a one-line fix are usually mis-diagnosed. |
| "I'll just test the top hypothesis without writing the others down" | Single-hypothesis generation anchors. Write the surviving hypotheses visibly, each with a kill condition, even when testing only the first. |
| "The loop passes, so the bug must be gone / not there" | A loop never seen red proves nothing. Watch it fail on unfixed code first. |
| "I checked that seam and the code is correct" | Ruling out is progress, not a finding. Name where the search moves next. |
| "I'll add the test once the fix works" | You will adapt the test to the fix. Write the test from the failing behavior FIRST. |
| "Let me also fix this other thing while I'm here" | Bundled fixes destroy bisection. One fix per attempt. |
| "I don't have time to instrument boundaries" | You have time to debug for 90 minutes; you don't have 5 minutes for a boundary log through the project's logger? |
| "The bug doesn't reproduce, but I know what's wrong" | If it doesn't reproduce, you cannot verify the fix. Reproduction is non-negotiable. |
| "I can't build a loop, I'll just stare at the code" | Stop and say so explicitly. Add temporary logger instrumentation, or ask for environment access or captured artifacts. |
