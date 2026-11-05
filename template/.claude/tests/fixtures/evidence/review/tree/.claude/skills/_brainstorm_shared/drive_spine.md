## Slices

- RED before GREEN per behaviour change, under the project's test-first rule.
- A slice reaches green alone, or per-slice verification proves nothing. Slice 1 of a multi-slice plan is a thin
  end-to-end tracer touching every layer the feature crosses. A wide mechanical refactor, whose blast radius is too
  large for any vertical slice to land green, runs expand–contract: add the new path, migrate callers in batches,
  delete the old path, each step green with targeted verification. The plan records the shape
  (`plan_file_format.md` §Slice shape).
- Map slices to jobs from their write sets and dependencies before the first dispatch, and state the map. Shape
  follows `orchestration` §0 and §11, and model and effort follow §5. Apply its Pre-Dispatch Checklist, record each
  dispatched job with `task_record.py job`, and dispatch through `dispatch.js` with `agentType: "general-purpose"` and
  the resolved model and effort. `orchestration` §2 owns the map's rules; disjoint slices may also run as dispatch
  chains (§0, §11). A delegate's
  edits get the parent's review before commit.
  Execution state lives in the task record (`orchestration` §10), never in a Workflow. The driving session keeps spec
  ownership, diff review, gate adjudication and commits.
- Lane/slice verification and lane/Part/drive close follow `change_control` §Gate cadence.
- The orchestrator records a verdict on every delegated slice diff, as an inline read with no agent. A rejected slice
  is re-dispatched to repair the named violation over its diff; the repaired diff's review is a later round
  (`orchestration` §2 *Later rounds*). A violation that invalidates the slice's design re-plans it.
- A separate spec verifier (one `dispatch.js` review job at the executor tier, reading only the slice's plan section
  and the diff) runs for a delegated slice whose executor is on probation for the tier it was dispatched at
  (`orchestration` §5). Its brief's done-condition names `python3 .claude/tools/post_check.py review --findings <file>`, and the parent runs it.
- Seam review (the Seam review row): beside the consumer's first slice, never blocking its start, run
  `/session_audit --files <the boundary's files> --lens sa-architecture-sweep --plan <plan path> --task <task id>`.
  The boundary's files are its slices' write sets that the Session File Identification Procedure reconciles. Each
  finding routes into the consuming slice's scope as a slice repair, a later round (`orchestration` §2 *Later
  rounds*), dispositioned before that slice's scoped verification closes it. A skipped seam review is a
  `task_record.py add <task id> decision` naming the boundary and the reason. It is not an absent lens result under
  H4, because §Close step 2 sweeps the seam files the ledger still shows uncovered.
- Completion is proven by artifacts: a RED line, a quoted filter run with its counts, a gate line bound to a tree
  digest. A run cited in a comparison records its command, filter and tree digest. An error-level log line in a run is
  a failure, and slice close needs the plan's verification evidence, not only a green test.
- Each configuration surface a slice adds beyond the plan's authored surfaces answers `rules/design_litmus.md` #1 in
  the slice rationale.
- A regression the slice introduced is fixed in the slice. A bug it did not introduce is fixed in the slice when the
  slice depends on it; otherwise it goes through `worklog_reference` §Do-now gate, whose Collision reason covers a fix
  that writes into the slice's write set.
- Retirement or refactor work first stands up a self-baselining scratch parity test with fixed-seed golden outputs
  pinning current behaviour. After deletion it runs a retired-name grep that includes doc comments. A check written
  only to diagnose is removed before the slice closes unless the plan names it.

## Close
