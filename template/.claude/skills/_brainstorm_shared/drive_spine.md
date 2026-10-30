# Drive spine

`/part_drive`, `/part_execute`, `/feature_drive`, `/design_drive` and `/worklog drive` are adapters onto this file.
`/overnight` ships in the pure layer, so it carries the adapter header with no link here and owns its pauses in its
Step 2. A stage runs as `execution_depth.md` §Admission table admits it. An admitted element whose owner command the
project lacks (no `.claude/commands/<name>.md`) is an absent result under H4: the drive stops there and never skips it.

## Stages

| Stage | Runs when | Owner | Halts |
|---|---|---|---|
| Intake | always: record the triage per `execution_depth.md` §Recording the triage; §Readiness; §Paths are outputs routes the fast path | `task_record.py triage` | H1, H2 |
| Ground | the Grounding row; the Spike row | `/explore`; the prototype route | H1, H6 |
| Design | the Design, Owner design decision, Design challenge and Decomposition rows | the brainstorm skills, `/design_drive` | H3, H5 |
| Plan | the Plan file row; the Plan check row | `plan_file_format.md`; `/plan_check` | H2, H3, H5, H6 |
| Execute | an admitted or approved plan, or the fast path | §Slices | H1, H2, H3, H5 |
| Verify | always, by chain position; the Integration verification and Objective pre-screen rows | the project's regression gate (`change_control` §Gate cadence names it); for oracle = runtime, the matching sensitivity tag's probe | H4 |
| Review | the Candidate review row | §Close | H4 |
| Land | always; the Owner approval row when reversibility = none | `change_control` §Commits | H4 |
| Close | always | §Close | — |

- **Intake.** Open an admitted task record with `python3 .claude/tools/task_record.py open <task_id> --session <session id> --title "<title>"` before recording conditions or running `task_record.py triage`. When recording triage, if the owner's words name the run's rigor ("light run", "thorough", "make it perfect"), record those words as a request under `execution_depth.md` §Conditions, then run `python3 .claude/tools/triage.py rigor --set light|normal|thorough --task <task_id>` with the chosen level. Then run `python3 .claude/tools/triage.py rigor --task <task_id>` and state the level in force (recorded, else the project default; `orchestration` reference `rigor.md`) in the first report; every stage report after it names the level.
- **Requirements.** Before the first brief, record each goal or constraint the owner's words set for the result with `task_record.py add <task_id> requirement|exclusion`. Record quality goals ("intuitive to author") as well as mechanism rulings. Briefs, review seats and plan checks judge against the record, never against a brief author's summary of it.
- **Ground.** When Ground runs: run `/explore` before drafting. Absorb every `premise-contradiction` claim and close every UNCOVERED
  dimension the design touches before drafting; `/explore` owns lens selection and evidence reuse (`execution_depth.md` §Boundary).
  Read the files the plan commits to first-party.
- **Plan.** A plan reaching approval holds no open owner decision and no undecided seam; an unknown only execution can settle is scoped in its job row (`plan_file_format.md`). Convergence and folds
  follow `/plan_check`.
- **Execution directive.** The invocation, or a plan the owner approved or the calling drive approved under explicit
  unattended authority, is the execution directive; never ask whether to continue. Approval authorizes every step the
  plan's Definition of Done names. The only pauses are H1–H6, the adapter's owner stop, and a step outside the
  Definition of Done that the Owner approval row admits (reversibility = none), which waits for the owner. A rejected
  tool call stops only that step: finish everything it does not depend on, then re-present the rejected step with its
  cost. Deferrals found while planning ride the plan or the task record, and the worklog write is a close action.
- **Parts path.** `execution_depth.md` §Paths are outputs and the Decomposition row own Parts admission and
  milestone re-triage, including the milestone report, which is not a pause. Dependencies set what waits on what, never one unit at a time: at each wake, every Part, plan or slice whose inputs are settled and that waits on no owner answer gets its job now (a pin, a spike, a plan draft or a build), up to the transport's concurrency limit (`orchestration` §2). A unit left waiting names its input in the task record's next step; an input the drive can produce itself is a job to launch, never a wait. A Part or a slice is planned as soon as every
  seam its plan reads is settled, so the next plan drafts while the current slice executes, and it is re-validated
  before it executes against each Part or slice that landed after it was planned. Gate
  and commit timing follow `change_control` §Gate cadence.

## Adapter header

Each adapter's first non-blank lines after its H1 are the bold labels `**Input:**`, `**Enters at:**` and
`**Owner stop:**`, in that order. The next paragraph links this file and says the adapter holds only what its input
adds; `/overnight` omits that link. The header is the only home of those facts, and this file holds no copy. Which adapter fits the work stays in `execution_depth.md` §Entry routing.

## Halts

A halt fires only on information the plan could not have known: surface the specific evidence and stop, and never
improvise a fix to an out-of-plan problem. An adapter's pre-flight abort (a missing plan, a Part that is not
`plan-pending`) is not a halt. A halt re-triages when `execution_depth.md` §Re-triage lists its cause. Under an
unattended arming (`/overnight` Step 2), a drive the run launches turns each halt into a Park row and continues the
work that does not depend on it. A halt leaves the shared checkout green: the halted slice's uncommitted edits, a
RED-first test above all, move out of the tree into `<plan>.<slice>.patch` beside the plan, and the plan's slice names
that patch as its resume point. A red test left in the tree fails every other session's close gate.

- **H1: a load-bearing premise failed verification.** State it as "plan says X / reality is Y". Examples: `/plan_part`
  macro drift, a plan-named file or type that is missing, a depended-on deliverable that is present but behaves wrong,
  a needed decision the plan lacks. A claim that confirms the plan does not halt. Residual design ambiguity found
  mid-drive is a kick-back signal, never grounds for an effort raise (session effort: `orchestration` reference `drive_dispatch.md` *Plan drafts*).
- **H2: scope cannot be bounded.** The file list cannot be bounded at plan time, or execution finds the work
  materially larger. Growth alone re-plans the milestones. Scope found mid-run to fail the Size litmus (a milestone
  that cannot be planned until earlier work lands) is a failed triage premise: it halts and proposes promotion;
  re-triage to size = parts admits Decomposition, and design runs only when design = open. Near the context limit, checkpoint and continue
  (`orchestration` reference `checkpointing.md`). If the complete plan still cannot be planned and reviewed, that is the same promotion, split by behaviour;
  never truncate a half-converged plan. Executor load splits into jobs, not Parts. Dropping or shrinking a planned
  slice is a halt with an owner question, never a silent cut.
- **H3: an owner-domain fork (taste, appetite, or a done-condition that is subjective taste).**
  `appetite_invariant.md` classifies it. It is batched at the adapter's owner stop when one exists; otherwise the
  drive halts. It is never resolved by an agent default. Taste no test can assert: implement the mechanism, flag
  it for the owner, and continue, unless the done-condition is that taste. Usability is not taste
  (`appetite_invariant.md` *Usability is analytical*): §Close step 1's walk judges it.
- **H4: the drive stops before any commit on any of these:** a red gate that is not a trivial in-scope fix; an
  unaccounted baseline drop; any critical finding left unresolved (neither fixed nor refuted with evidence); an absent, partial or timed-out gate, battery
  or lens result, which is never green (a lens that returns nothing is uncovered: `orchestration` §8). An owner's
  quoted waiver of a named check exits H4 for that check only. The record keeps the quote, and the waiver packet
  recommends the scoped run over the changed files as its default. A blocking verdict is never downgraded to advisory
  to open a gate. For an inconclusive result, recover the missing artifact or rerun only the uncovered check when its
  cause is understood. A baseline drop that the work's test arithmetic accounts for exactly (N retired, M added) is
  not unaccounted; the report and the commit message disclose it.
- **H5: non-convergence.** The same critical recurs, revisions stop adding evidence, a supported critical in an applied remedy has no answer in the existing design
  (`skills/orchestration/reference/later_rounds.md`), or the executor is stuck with no new information. Annotating a disposition and proceeding is never the exit. Never present a known-critical plan for
  approval. A dead-end verdict from an adversarial panel is H5.
- **H6: prior-art conflict with a verified existing owner.** An `exp-prior-art` claim naming an owner whose concern
  the plan would duplicate halts before drafting, for an ownership decision. Another session's in-flight plan over the
  same files is such an owner: add the work to that plan, or halt for the owner, never a parallel plan.

## Slices

- RED before GREEN per behaviour change in the domain the project's test-first rule names, and for a regression's test in any domain (it reproduces the bug, `debugging`). A new check in any other domain (a rendered or integration row) runs green, with assertions that name the slice's change. Whether a new row could pass on the old build is a review question at the merge, never a per-slice run of the old build.
- Inside a slice: only the tests of the items it changes, each run alone, and after a failure only the failed tests. At each merge of slices: the dispatcher runs the scope the merged slices touched. Once at drive close: the full gate. When a selective runner at an inner tier cannot place a changed path, it names the path and keeps to that tier's scope; it never widens to the full gate, which the close runs anyway. A test that cannot run alone is a test-design defect fixed in the test, never a reason to widen a run. The project's `change_control` §Gate cadence binds each tier to its runner.
- A plan still in plan check runs no slice, even one no open finding touches: the next fold can still rewrite it, and its landed code then misleads the next round.
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
- A separate spec verifier (one `shape: review` job through `/delegate` at the executor tier, reading only the slice's plan section
  and the diff) runs for a delegated slice whose executor is on probation for the tier it was dispatched at
  (`orchestration` §5). Consume its verification per `orchestrator_action_protocol.md` Step 1.5.
- Seam review (the Seam review row): beside the consumer's first slice, never blocking its start, run
  `/session_audit --files <the boundary's files> --lens sa-architecture-sweep --plan <plan path> --task <task id>`.
  The boundary's files are its slices' write sets that the Session File Identification Procedure reconciles. Each
  finding routes into the consuming slice's scope as a slice repair, a later round (`orchestration` §2 *Later
  rounds*), dispositioned before that slice's scoped verification closes it. A finding below the run's bar is noted in the seam report and routes into no slice. A skipped seam review is a
  `task_record.py add <task id> decision` naming the boundary and the reason. It is not an absent lens result under
  H4, because §Close step 2 sweeps the seam files the ledger still shows uncovered.
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

## Close

1. Verify by chain position per `change_control` §Gate cadence, which also owns fix re-verification. At each Part's
   close, run its plan's root-acceptance rows (`plan_file_format.md` §Root acceptance) on the Part's candidate through
   the project's agent playtest command; at drive close, before the close gate, run the rows of every plan serving the goal on their
   merged candidate the same way. Record each requirement met, partial, not met or owner-judges with its run
   evidence; a requirement no row maps is not met, and only taste is owner-judges (`appetite_invariant.md`
   *Usability is analytical*). A partial or not-met row is repaired as a slice and re-run before the gate, and no
   push precedes this verification, even after a green gate. Step 6 writes the record into the Part's roadmap row
   (`roadmap_triggers_mvp.md` §6.10, the `complete` row). The owner's play checklist is built from these rows, and
   an owner-deferred, partial or unprobed row is never reported met.
2. Candidate review (the Candidate review row), run only at the drive's close (`change_control`
   §Gate cadence). Any other close runs step 1, and the seam review when its row admits it. The chain
   position places this step, not the commit: a plan that commits per job runs it over the landed commits.
   When the Plan file row admitted none, first write the conversation's short plan to
   `.claude/scratch/<task>/short_plan.md` and pass that path. Run
   `/session_audit --files <the drive's files> --plan <plan path> --task <task id>` and read its report: the audit's fold applies the findings it can and the drive rules the report's `held` ids and failed checks per `orchestrator_action_protocol.md` Step 1.5 and Step 4. The drive's files are
   `git diff --name-only <base>..HEAD` plus the session files the Session File Identification Procedure reconciles
   (its Step 4) that lie in the plan's write set, or in the short plan's file list when no plan file exists. The
   report holds an `sa-architecture-sweep` verdict or the ledger row that covers it. It reads the review ledger
   (`tools/review_ledger.py`, `orchestration` §2).
   Every finding is dispositioned (applied by the fold, with its `applied` entry; fix now by a refold; worklog under a `worklog_reference` §Do-now gate reason, or refuse with
   evidence; or below bar: noted); an ASK goes to the adapter's owner stop, or halts the drive under H3 when there is none. A fix-now disposition routes per `orchestration` §2 *Later rounds*. Close is done when every finding has a disposition; an undispositioned finding stops the commit sequence.
   The close report lists each seam review the drive's boundaries admitted: the runs from
   `review_ledger.py rows --task <task id>`, the skips from the task record's decisions.
3. When preparing a branch for a PR or merge, run `/pr_ready` over the readiness checks the diff admits, reusing
   current coverage. A clean battery means every admitted lens returned and none reported a critical finding; one is H4.
4. Close out dispatch ratings (`/orchestration_metrics` §Incremental rating, *Phase close*) when consumed dispatches
   remain unrated.
5. Commit per the project's commit rules (`change_control` §Commits). The drive does not push; a later push follows
   `CLAUDE.core.md` §Git, and one the Owner approval row admits waits for the owner.
6. The adapter's write-back, which its header's owner stop or close clause names: the roadmap flip for a Part,
   `/worklog complete` with the commit ref per item in the session it lands, or none.

## Rationalizations to refuse

| Rationalization | Reality | Rule |
|---|---|---|
| "It's a deletion Part — nothing to TDD." | Changed behaviour still gets RED before GREEN; §Slices scopes the parity capture and retired-name check. | the project's test-first rule: no carve-out for self-evident logic |
| "The plan is detailed, so RED-before-GREEN is covered." | A detailed plan is starter material, never a license to skip RED-before-GREEN. | the project's test-first rule |
| "It's ad-hoc, so relax TDD or the gate." | The domain split and the regression gate bind on provenance-blind rules: every gated change, no carve-outs. | the project's test-first rule; `change_control` §Gate cadence |
| "This taste question is small — pick the sensible default to keep the drive moving." | The owner stop decides where taste goes, never whether it is honoured. Batch it or halt on it. | H3 |
| "Criticals remain but I've run enough rounds — present the plan anyway." | Approval-ready and known-critical are mutually exclusive. | H5 |
| "Run every slice in parallel to finish sooner." | Coupled slices serialize; only independent lanes run as dispatch chains, and execution state stays in the task record. | §Slices |
| "The design is in the conversation, so skip the plan file." | Conversation context dies at compaction; the plan file exists whenever the Plan file row admits it. | `execution_depth.md` §Admission table |
| "We discussed it thoroughly, so skip /plan_check." | A resolved design is not an audited plan; plan check runs whenever its row admits it. | `execution_depth.md` §Admission table |
| "It grew, but we're mid-drive, so keep going." | Growth re-plans the milestones; scope with a milestone that cannot be planned until earlier work lands halts and proposes promotion to parts. | H2 |
