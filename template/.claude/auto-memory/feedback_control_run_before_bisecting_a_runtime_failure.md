---
name: feedback-control-run-before-bisecting-a-runtime-failure
description: "When a test host, engine or tool fails, search the FAILING domain's memory with the literal failure string and re-run the identical command in the PRIMARY checkout before forming any hypothesis about the change under test. A fresh throwaway worktree is not a control — it differs from the primary checkout in ways that emit the same symptom as a real defect."
metadata: 
  node_type: memory
  type: feedback
  modified: 2026-08-19T15:22:37.972Z
---

Two rules, in this order, before any hypothesis about the code under test:

1. **Search memory for the literal failure string** — the FATAL text, the exit code, the tool's own
   error line. Entering a new domain mid-task is exactly the moment its gotchas have not been read,
   and a runtime failure is a domain entry even when the *task* is a merge, a refactor, or a review.
2. **Re-run the identical command in the primary checkout.** Passing there means the defect is
   environmental and every further minute spent on the diff is wasted.

**A throwaway `git worktree` is not a control environment.** Measured differences that each emit a
failure indistinguishable from a real one:

- `.runsettings` is **gitignored**, so it is absent in a fresh worktree. `dotnet test` then dies in
  ~1.4 s with *"The Settings file could not be found"* — which reads as an instant hard crash.
- `.godot/` is cold, so the first run pays a full asset import inside the test process, or wedges on
  it. A wedge here is indistinguishable from the executor hang.
- `.godot/mono` build output is not shared, so a "clean build" claim from the primary checkout does
  not transfer.

**Why:** on 2026-08-19 a PR-112 merge session spent most of its budget bisecting merge content
against a test host that died at boot. The mechanism was already recorded in memory, the primary
checkout ran the same suites green, and two of the four "experiments" run to isolate it were invalid
for the worktree reasons above — each believed before it was checked.

**How to apply:**

- Bisect the **filter** before the **code**: narrow to one suite, then one class, then one test. A
  code bisect assumes the change is implicated, which is the assumption under test.
- Treat an *invalid* experiment as costing more than no experiment — it manufactures false evidence.
  Before running a control, state what would make its result meaningless, and check that first.
- **"Reboot" is not a diagnosis** and is not an acceptable remedy to offer. Neither is "environmental"
  without a named mechanism.
- When the tooling reports a *category* (`SILENT_SKIP`, `INVALID`, `connect failed`), do not adopt
  that category as the cause — read the log for the signature the tool actually matched.

Related: [[gotcha_accumulating_fatal_makes_batch_and_filter_disagree]],
[[gotcha_runtime_suite_pipe_contention]], [[gotcha_unit_filtered_test_run_fake_green]].
