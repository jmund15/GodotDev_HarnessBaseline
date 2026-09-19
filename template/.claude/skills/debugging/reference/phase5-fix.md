# Phase 5 — Fix + Verify

Read at Phase 5 of [`../SKILL.md`](../SKILL.md).

**Rule:** Failing test FIRST → single fix → verify.

## Write a failing test FIRST

Per `testing`'s Logic-Domain Iron Law, the test must fail because of the hypothesised cause and pass after the fix. No "I'll add the test once it works" — you will adapt the test to the fix.

A correct seam exercises the **real bug pattern as it occurs at the call site**. A seam too shallow — single-caller test when the bug needs multiple callers, Logic unit test that can't replicate the engine-lifecycle chain — gives false confidence.

Even when the domain classifies as Gameplay, if the bug class IS the integration (hot-loop, race, BB-flag-soup), write the seam-level integration test BEFORE shipping (`feedback_strict_tdd_for_integration_regressions.md`).

## Picking the right suite

Per `testing/SKILL.md` domain classification:

- **Pure logic / data / math:** `Tests/Logic/` (no Godot runtime).
- **Cross-system seam** (BT+BTState, Pool+Spawn, HSM+child-state, Spell+Collision+Reaction): `Tests/Integration/` — even if the C# diff looks trivial. Memorialised integration regressions REQUIRE a seam test.
- **Player-observable behavior:** `Tests/Sanity/` or E2E with `ISceneRunner` (POB rule from `testing/SKILL.md`).

## If no correct seam exists, that itself is the finding

Note it explicitly — the architecture is preventing the bug from being locked down. Hand off to the **Worklog** with `arch | <description>`. Do **not** invent a new slash command here, and do not repurpose `/spell_arch_audit` (Spell-only) or `/session_audit` (post-hoc, wrong shape).

## If a correct seam exists

1. Turn the minimised repro into a failing test at that seam.
2. Watch it fail — for the hypothesised reason only; if it could fail for two reasons, narrow it.
3. Apply the fix.
4. Watch it pass.
5. Re-run the Phase 1 loop against the original (un-minimised) scenario to confirm the fix addresses *the user's* bug, not just the test version.

## Single fix per attempt

**Rule:** One change per fix attempt — resist "also clean this up while I'm here." Bundled fixes cannot be bisected on recurrence, and hide which sub-change caused a new regression.

## Verify

- The failing test now passes.
- The original symptom is gone (manual repro or post-run `godot.log` check).
- No `JmoLogger.Error` lines in the run output (errors trigger test failures).
- Run the broader suite (`/regression_gate` if appropriate) before claiming the bug is fixed. *"Should work now"* is not evidence.

## The 3-fixes-failed gate

**Rule:** 3+ failed fixes for the same bug — STOP. This is a redesign signal, not a debugging task.

1. Do NOT attempt fix #4. The pattern is wrong, not the implementation.
2. Question fundamentals: is the architecture sound? Are we sticking with this pattern through inertia? Refactor instead of patching symptoms?
3. **Switch to [`architecture_brainstorm`](../../architecture_brainstorm/SKILL.md)** — its Socratic clarifying phase + 2–3 ranked approaches fits "is this pattern wrong?"; this skill assumes the pattern is sound and merely misapplied. Restarting cold is the other valid exit.
4. **Discuss with the user before attempting fix #4.** Do not silently continue.

The presumption inverts by who proposed the fix: when the *user* says "do the recommended fix," default to shipping (`feedback_recommended_fix_means_implement.md`); when *you* have proposed three failed fixes, default to stopping and asking.
