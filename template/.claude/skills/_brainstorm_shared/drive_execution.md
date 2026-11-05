# Drive execution

`drive_spine.md` §Stages routes the Execute stage here. This file holds the slice rules that stage runs.

## Slices

- RED before GREEN per behaviour change in the domain the project's test-first rule names, and for a regression's test in any domain (it reproduces the bug, `debugging`). A new check in any other domain (a rendered or integration row) runs green, with assertions that name the slice's change. Whether a new row could pass on the old build is a review question at the merge, never a per-slice run of the old build.
- Inside a slice: only the tests of the items it changes, each run alone, and after a failure only the failed tests. At each merge of slices: the dispatcher runs the scope the merged slices touched. Once at drive close: the full gate. When a selective runner at an inner tier cannot place a changed path, it names the path and keeps to that tier's scope; it never widens to the full gate, which the close runs anyway. A test that cannot run alone is a test-design defect fixed in the test, never a reason to widen a run. The project's `change_control` §Gate cadence binds each tier to its runner.
- A plan still in plan check runs no slice, even one no open finding touches: the next revision can still change it, and its landed code then misleads the next round.
- A slice reaches green alone, or per-slice verification proves nothing. Slice 1 of a multi-slice plan is a thin
  end-to-end tracer touching every layer the feature crosses. When the feature has visible runtime behavior, the
  tracer's milestone ends with a live caller receipt: its rendered scenario (`plan_file_format.md` §Root acceptance)
  run and retained. No slice clones the pattern across further instances (creatures, archetypes, variants) before
  that receipt. A wide mechanical refactor, whose blast radius is too
  large for any vertical slice to land green, runs expand–contract: add the new path, migrate callers in batches,
  delete the old path, each step green with targeted verification. The plan records the shape
  (`plan_file_format.md` §Slice shape).
- Before the first slice is written, route every slice in the plan's job map (`plan_file_format.md` §Job map): a
  job, from its write set and dependencies, or `inline` with its `orchestration` §0 reason. Shape
  follows `orchestration` §0 and §11, and model and effort follow §5. Apply its Pre-Dispatch Checklist, record each
  dispatched job with `task_record.py job`, and dispatch through `/delegate` at
  the resolved model and effort. `orchestration` §2 owns the map's rules; disjoint slices may also run as dispatch
  chains (§0, §11). A delegate's
  edits get the parent's review before commit.
  Execution state lives in the task record (`orchestration` reference `checkpointing.md`), never in a Workflow. The driving session keeps spec
  ownership, diff review, gate adjudication and commits.
- Lane/slice verification and lane/Part/drive close follow `change_control` §Gate cadence.
- The orchestrator records a verdict on every delegated slice diff, as an inline read with no agent. A rejected slice
  is re-dispatched to repair the named violation over its diff; the repaired diff's review is a later round
  (`orchestration` §2 *Later rounds*). A violation that invalidates the slice's design re-plans it.
- A separate spec verifier (one `kind: reviewer` job through `/delegate` at the executor tier, reading only the slice's plan section
  and the diff) runs for a delegated slice whose executor is on probation for the tier it was dispatched at
  (`orchestration` §5). Consume its verification per `orchestrator_action_protocol.md` Step 1.5.
- Seam review: beside the consumer's first slice, never blocking its start, run
  `/session_audit --files <the boundary's files> --lens sa-architecture-sweep --plan <plan path> --task <task id>`.
  The boundary's files are its slices' write sets that the Session File Identification Procedure reconciles. Each
  finding routes into the consuming slice's scope as a slice repair, a later round (`orchestration` §2 *Later
  rounds*), dispositioned before that slice's scoped verification closes it. Rigor and the bar: the Seam review row of `rigor_stages.md` and `orchestration` reference `rigor.md`. A skipped seam review is a
  `task_record.py add <task id> decision` naming the boundary and the reason. It is not an absent lens result under
  H4, because `drive_close.md` §Close step 2 sweeps the seam files the ledger still shows uncovered.
- Completion is proven by the artifacts the slice's change kind and oracle require under the project's test-first
  rule and `change_control` §Gate cadence. A run cited in a comparison records its command, filter and tree digest. An error-level log line in a run is
  a failure, and slice close needs the plan's verification evidence, not only a green test.
- Each configuration surface a slice adds beyond the plan's authored surfaces answers `reference/rules/design_litmus.md` #1 in
  the slice rationale.
- A regression the slice introduced is fixed in the slice. A bug it did not introduce is fixed in the slice when the
  slice depends on it; otherwise it goes through `worklog_reference` §Do-now gate, whose Collision reason covers a fix
  that writes into the slice's write set.
- **Behavioural baseline.** Retirement or refactor work that keeps or reroutes behaviour first runs a self-baselining
  scratch test on the pre-change tree with fixed-seed golden outputs, then runs the same probe and seeds on the changed
  tree and compares those outputs. Before a commit or report claims preserved behaviour, retain both run receipts:
  command, filter/seeds, tree digest, exit status and output artifact, plus the comparison verdict. A compile-only RED
  is not a before-run receipt. `/session_audit`'s deleted-surface
  parity screen cannot supply this baseline. Deleting unreferenced code needs only a retired-name grep including doc
  comments. Run that grep after every deletion. A check written only to diagnose is removed before the slice closes
  unless the plan names it.
