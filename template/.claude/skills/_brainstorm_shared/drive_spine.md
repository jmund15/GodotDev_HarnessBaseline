# Drive spine

`/part_drive`, `/part_execute`, `/feature_drive`, `/design_drive` and `/worklog drive` are adapters onto this file.
`/overnight` ships in the pure layer, so it carries the adapter header with no link here and owns its pauses in its
Step 2. Each stage's depth is the sizing's call (§Sizing). A planned element whose owner command the
project lacks (no `.claude/commands/<name>.md`) is an absent result under H4: the drive stops there and never skips it.

## Stages

Every drive runs these stages in order. Each hands over what its **Complete when** line names, at the depth its **Depth** line sets at Intake (§Sizing). What a later stage finds that an earlier stage's Complete-when line names is that stage's leak: the work goes back there (§Leaks).

**Intake.** Know what the owner wants and what will show it, before anything is spent.
- Delivers: the task record (spec below): the title in the owner's words; each requirement and exclusion with its provenance; the oracle (a test, a run or the owner's eye that shows it works). The sizing is stated in the first report (§Sizing) and is not part of the record.
- Complete when: every owner sentence that sets a goal (a quality they want, such as "intuitive to author", as well as a mechanism they ruled), constraint, exclusion or ruling for this work, in this session or a record or state file it holds, is in the record verbatim, and nothing the owner did not say is marked theirs; a goal found later is added with `task_record.py add <task_id> requirement|exclusion`, because a record that misses a goal judges the work against less than they asked; briefs, review seats and plan checks judge against the record, never against a brief author's summary of it, because a summary carries its author's frame; the oracle is nameable, else the work goes to Ground, a spike or a reshape (`execution_depth.md` §Readiness); a worklog item or a stated fix was checked against the commits since its source, and the stated fix is recorded as a lead, never a requirement.
- Depth: how many owner turns and earlier rulings bear on the work.
- Owner: asked before Ground only when the request reads two ways that lead to different work and no read settles which, in one packet (`CLAUDE.interactive.md` §Answering and asking the owner).
- Recording: open the task record with `python3 .claude/tools/task_record.py open <task_id> --spec <spec.json>` before recording conditions or running `task_record.py triage`: one call writes the record, its requirements, exclusions and scope. The spec is a JSON object; an identical spec on an open record is a no-op, a changed one is refused (`add`/`triage` edit an open record), and a replacement needs `--reset`.
  ```json
  {
    "session": "<session id>",
    "title": "<one-line title>",
    "requirements": ["<a goal or constraint the owner's words set>"],
    "exclusions": ["<what the owner ruled out>"],
    "scope": {"owner": null, "files": [".claude/tools/example.py"]}
  }
  ```
  When recording triage, if the owner's words name the run's rigor ("light run", "thorough", "make it perfect"), record those words as a request under `execution_depth.md` §Conditions, then run `python3 .claude/tools/triage.py rigor --set light|normal|thorough --task <task_id>` with the chosen level. Then run `python3 .claude/tools/triage.py rigor --task <task_id>` and state the level in force (recorded, else the project default; `orchestration` reference `rigor.md`) in the first report; every stage report after it names the level. In the same step, run `python3 .claude/tools/triage.py mode --set fix-now|attended|unattended --task <task_id>` when the owner's words name how the run is driven, recording those words as a request, and state the mode in force beside the level (`execution_depth.md` §Mode names each value's effect).

**Ground.** Put in front of the design every fact its decisions will rest on, verified, as wide as the change reaches, so design is judgment and not discovery.
- Delivers: checked claims, from the session's own reads for a narrow change or from `/explore`, on whatever the design's decisions rest on. The usual ground, as examples and not a list to clear: what already owns this concern; what consumes what would change; why each piece the change edits or removes exists (its origin commit, the owner's words or design it cites, its fix history); what the owner has designed, said and ruled on the subject; where else the change runs (another project, other platforms, concurrent sessions, other providers; in a game, every entity that can carry the trait, effect or system); what the engine, library, tests and data show now; and defects in the code the change touches.
- Complete when: each fact a decision will rest on that a read can settle is settled, or listed UNCOVERED with its reason; it holds no recommendation; a Ground the session ran inline is in the task record (`task_record.py add <task_id> artifact "ground: <one line>" --evidence <path>`, or a `decision` with its evidence items), so plan check's preflight finds it.
- Depth: how much the decisions rest on facts not verified this session, and how far the change reaches. A question one read or script settles is answered inline; a question that needs a survey goes to `/explore`, which owns how its lenses are seated.
- Hand-off: the result reaches the design job through the engine's hand-off (under `/explore`, its checked dossier, which `/explore` owns); the session neither restates nor filters it.

**Design.** Decide the ideal solution from those facts: the smallest system that meets every requirement and holds beyond the case it targets.
- Delivers: `design_contract.md` clause 9's hand-off.
- Complete when: no decision rests on an unverified fact (each element the design adds is checked by its own reads, or comes back as a ground request naming the survey it needs); the whole-design answers are in the design, not left to review; each open item is a fork with options or an uncertainty naming what settles it.
- Depth: how open the design is and what a wrong direction would cost. A design the owner approved, a known root cause or a pattern the codebase already uses is the session's to record: the simpler-path line and the decisions go in the task record (`orchestration` reference `brief_checks.md` §Gather, then judge). An open design is one design job's, and a part-sized one is `/design_drive`'s design doc, its challenge and its roadmap. You judge which the work is, and say why in the sizing.

**Plan.** Make the design executable by a cold executor without the conversation.
- Delivers: the plan file (`plan_file_format.md`), or a short plan in the record when one sitting carries the work.
- Complete when: it settles what its drafter's reads settle; each requirement maps to a slice and the evidence that will show it; each replaced surface's callers are listed; an unknown only execution can settle is scoped in its job row; no owner decision is open.
- Depth: size (`execution_depth.md` §Fields).

**Check.** Read the design or plan as someone who did not write it: its frame, whole, against the owner's words, over everything it touches (`/plan_check`; the design challenge at size parts).
- Delivers: findings with evidence, each saying whether the subject's inputs held what it turns on.
- Complete when: `/plan_check` Phase 3's stop holds, and each finding judged the subject against the task record's requirements and exclusions, never against its author's summary of them.
- Depth: whether the subject holds a decision no reviewer has seen, what a miss costs once executed, and how much the author's frame could hide. A plan holding a decision no reviewer has seen (such as a new type, export, authored surface, family or rule; you judge what counts as one) gets `/plan_check` with its holistic seat, because an author cannot see the frame it wrote in. A check after a checked design reads only what the plan adds (`/plan_check` Phase 2). Seats and their pins are chosen per job, never copied from an earlier run.

**Revise.** Answer each finding at the stage that owns it (`orchestrator_action_protocol.md` Step 4).
- Complete when: each set ruling and literal text fix is folded; a verified fact is reused, and only missing evidence is gathered before its decision is re-ruled; each finding whose answer adds material design went back to Design under `later_rounds.md`'s rule, and Design checked what it adds; each owner-class finding reached the owner.

**Execute, Verify, Review, Land, Close** run [`drive_execution.md`](drive_execution.md) §Slices and [`drive_close.md`](drive_close.md) §Close. A slice is complete when it is green alone with the evidence its change kind needs; a failure its callers' tests or its defect class would have shown is an Execute leak.

## Sizing

Each entry command states the sizing in its first report, before Ground runs: each stage's planned depth with one reason, read from the work's facts (reach, whether the design is settled, what will show it works, size, the rigor in force; `execution_depth.md` §Fields), with `triage.py lookup`'s derived reach and oracle beside it; a sizing below a derived value says why. The floors stay: `/design_drive`'s existing-doc check and design-lock, and plan check for a plan holding a decision no reviewer has seen. A stage whose return changes a later stage's depth re-sizes it, and the next report says so.

## After each stage

Read each stage's return against its Complete-when line, as `orchestration` reference `integration.md` §When it returns says, and take the next step it names.

## Leaks

A finding, halt or review result that an earlier stage's Complete-when line names is that stage's leak. The session's set ruling (`orchestrator_action_protocol.md` Step 4) names the stage and sends the work back there; it never patches the leak where it surfaced. The close report lists the drive's leaks by stage, each as a contract gap (the line did not ask for it: a harness fix, through `/codify`) or a job's miss (the line asked and the job did not deliver: that dispatch's rating at close). A check whose findings are mostly leaks says the earlier stages under-delivered, never that the drive needs more checks.

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

- **H1: a load-bearing premise failed verification.** State it as "plan says X / reality is Y", and name the stage
  whose Complete-when line owed the premise (§Leaks). Examples: `/plan_part`
  macro drift, a plan-named file or type that is missing, a depended-on deliverable that is present but behaves wrong,
  a needed decision the plan lacks. A claim that confirms the plan does not halt. Residual design ambiguity found
  mid-drive is a kick-back signal, never grounds for an effort raise (session effort: `orchestration` reference `drive_dispatch.md` *Plan drafts*).
- **H2: scope cannot be bounded.** The file list cannot be bounded at plan time, or execution finds the work
  materially larger. Growth alone re-plans the milestones. Scope found mid-run to fail the Size litmus (a milestone
  that cannot be planned until earlier work lands) is a failed triage premise: it halts and proposes promotion;
  re-sizing to size = parts calls for Decomposition, and design runs only when design = open. Near the context limit, checkpoint and continue
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
- **H5: non-convergence.** A unit that stopped converging (`skills/orchestration/reference/later_rounds.md`) reaches a
  dead end on its return to the stage that designed it, revisions stop adding evidence, or the executor is stuck with no
  new information. Annotating a disposition and proceeding is never the exit. Never present a known-critical plan for
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
| "The design is in the conversation, so skip the plan file." | Conversation context dies at compaction; the plan, in a file or in the record, is what survives it. | §Stages Plan |
| "We discussed it thoroughly, so skip /plan_check." | A resolved design is not an audited plan; whether a check runs is the sizing's call, stated with its reason, and a plan holding a decision no reviewer has seen always gets one. | §Sizing |
| "It grew, but we're mid-drive, so keep going." | Growth re-plans the milestones; scope with a milestone that cannot be planned until earlier work lands halts and proposes promotion to parts. | H2 |
