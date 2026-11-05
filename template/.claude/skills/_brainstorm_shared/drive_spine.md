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
| Execute | an admitted or approved plan, or the fast path | [`drive_execution.md`](drive_execution.md) §Slices | H1, H2, H3, H5 |
| Verify | always, by chain position; the Integration verification and Objective pre-screen rows | the project's regression gate (`change_control` §Gate cadence names it); for oracle = runtime, the matching sensitivity tag's probe | H4 |
| Review | the Candidate review row | [`drive_close.md`](drive_close.md) §Close | H4 |
| Land | always; the Owner approval row when reversibility = none | `change_control` §Commits | H4 |
| Close | always | [`drive_close.md`](drive_close.md) §Close | — |

- **Intake.** Open an admitted task record with `python3 .claude/tools/task_record.py open <task_id> --spec <spec.json>` before recording conditions or running `task_record.py triage`: one call writes the record, its requirements, exclusions, scope and triage. The spec is a JSON object; an identical spec on an open record is a no-op, a changed one is refused (`add`/`triage` edit an open record), and a replacement needs `--reset`.
  ```json
  {
    "session": "<session id>",
    "title": "<one-line title>",
    "requirements": ["<a goal or constraint the owner's words set>"],
    "exclusions": ["<what the owner ruled out>"],
    "scope": {"owner": null, "files": [".claude/tools/example.py"]},
    "triage": {
      "fields": {"reach": "<reach>", "reversibility": "<reversibility>", "design": "<design>",
                 "grounding": "<grounding>", "oracle": ["<oracle>"], "size": "<size>"},
      "paths": [".claude/tools/example.py"],
      "outward": []
    }
  }
  ```
  When recording triage, if the owner's words name the run's rigor ("light run", "thorough", "make it perfect"), record those words as a request under `execution_depth.md` §Conditions, then run `python3 .claude/tools/triage.py rigor --set light|normal|thorough --task <task_id>` with the chosen level. Then run `python3 .claude/tools/triage.py rigor --task <task_id>` and state the level in force (recorded, else the project default; `orchestration` reference `rigor.md`) in the first report; every stage report after it names the level.
- **Requirements.** Before the first brief, write each goal or constraint the owner's words set for the result into the spec's `requirements` and `exclusions` (the example under Intake), so the one `open --spec` records them; a goal found later is added with `task_record.py add <task_id> requirement|exclusion`. Record quality goals ("intuitive to author") as well as mechanism rulings. Briefs, review seats and plan checks judge against the record, never against a brief author's summary of it.
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

## Rationalizations to refuse

| Rationalization | Reality | Rule |
|---|---|---|
| "It's a deletion Part — nothing to TDD." | Changed behaviour still gets RED before GREEN; `drive_execution.md` §Slices scopes the parity capture and retired-name check. | the project's test-first rule: no carve-out for self-evident logic |
| "The plan is detailed, so RED-before-GREEN is covered." | A detailed plan is starter material, never a license to skip RED-before-GREEN. | the project's test-first rule |
| "It's ad-hoc, so relax TDD or the gate." | The domain split and the regression gate bind on provenance-blind rules: every gated change, no carve-outs. | the project's test-first rule; `change_control` §Gate cadence |
| "This taste question is small — pick the sensible default to keep the drive moving." | The owner stop decides where taste goes, never whether it is honoured. Batch it or halt on it. | H3 |
| "Criticals remain but I've run enough rounds — present the plan anyway." | Approval-ready and known-critical are mutually exclusive. | H5 |
| "Run every slice in parallel to finish sooner." | Coupled slices serialize; only independent lanes run as dispatch chains, and execution state stays in the task record. | `drive_execution.md` §Slices |
| "The design is in the conversation, so skip the plan file." | Conversation context dies at compaction; the plan file exists whenever the Plan file row admits it. | `execution_depth.md` §Admission table |
| "We discussed it thoroughly, so skip /plan_check." | A resolved design is not an audited plan; plan check runs whenever its row admits it. | `execution_depth.md` §Admission table |
| "It grew, but we're mid-drive, so keep going." | Growth re-plans the milestones; scope with a milestone that cannot be planned until earlier work lands halts and proposes promotion to parts. | H2 |
