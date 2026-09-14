---
name: feedback_gate_invalid_targeted_verification
description: After an INVALID gate with attributable green phases and a small delta, verify the delta targeted with composite Verified lines — don't reflexively re-run the full gate
metadata:
  type: feedback
---

When a gate returns INVALID, decompose it before re-running: which phases produced real green results during the invalidated run, and what is the delta since the last full PASS? Small delta + attributable greens → verify the delta with targeted `run_test_suite.ps1` runs and commit with composite `Verified:` lines naming each phase's provenance. Reserve a full re-run for broad deltas or un-attributable failures.

**Why:** the user stopped a third 10+ minute full-gate run — the invalidated run's Integration phase had genuinely passed 1984/0 over the same content, and the remaining delta was two using-lines and one cref signature. (2026-08-14, audio drive Part-6 close-out.)

**How to apply:** on an INVALID verdict, write down per-phase status (real-green / unparsed / timeout) and the file delta vs the last PASS; if the delta is narrow and every affected suite either ran green or is covered by a targeted run, commit with a composite line like `Verified (composite): Integration N/0 full run over this content set, targeted suites M/M, Sanity 48/0` — never a bare unqualified `Verified:` for a composite. Related: [[gotcha_tres_import_pass_tree_changed_untracked]] names one INVALID cause that needs no full re-run at all.
