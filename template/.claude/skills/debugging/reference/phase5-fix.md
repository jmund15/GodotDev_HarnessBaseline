# Phase 5 — Fix + Verify

Read at Phase 5 of [`../SKILL.md`](../SKILL.md).

**Rule:** Failing test FIRST → single fix → verify.

## Write a failing test FIRST

The project's test-first rule in its `CLAUDE.md` is the floor. Here the test must fail because of the hypothesised cause and pass after the fix. No "I'll add the test once it works" — you will adapt the test to the fix.

A correct seam exercises the **real bug pattern as it occurs at the call site**. A seam too shallow gives false confidence: a single-caller test for a bug that needs several callers, or a unit test that cannot replay the runtime lifecycle chain. When the bug class is the integration itself, the seam test comes first (`testing` §Test first).

## Picking the right suite

The project's test-classification reference names the suite for each change. A memorialised integration regression requires a seam test.

## An architectural cause goes to the Worklog as `arch`

No correct seam is itself the finding: the architecture is preventing the bug from being locked down. Note it explicitly and hand off to the **Worklog** with `arch | <description>`. Make the recommendation **after** the fix is in — you know more then. Do **not** invent a new command for it.

For a "shallow module / leaky abstraction" smell, the **Deletion Test** in `architecture_philosophy` articulates it precisely.

## If a correct seam exists

1. Turn the minimised repro into a failing test at that seam. It must fail for the hypothesised reason only; if it could fail from another cause, narrow it.
2. The same test passes after the fix, re-run on the same tree.
3. Re-run the Phase 1 loop against the original (un-minimised) scenario; the user's bug is gone.

## Single fix per attempt

**Rule:** One change per fix attempt — resist "also clean this up while I'm here." Bundled fixes cannot be bisected on recurrence, and hide which sub-change caused a new regression.

## Verify

- The failing test now passes.
- The original symptom is gone (manual repro or a post-run runtime-log check).
- No error-level log lines in the run output.
- Verify at scope size for a slice; the drive closes with the scope run, and the full gate runs in the main check after the land (`change_control` §Gate cadence). *"Should work now"* is not evidence.

## After one plausibility fix fails

[`../SKILL.md`](../SKILL.md) §Rationalizations to refuse owns the stop-and-question-the-design exit. Who proposed the fix sets the default: when the *user* says "do the recommended fix," ship it; when *you* proposed the failed fixes, stop and ask.
