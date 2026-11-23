---
description: Ship an approved plan, a state-file slug or a roadmap Part to landed, verified code
---

# /drive — ship approved work

**Input:** `/drive <plan-path> | <slug> | <part>`.

- **A plan path** that the owner approved. Its state file is `.claude/work/<slug>.md` for the plan's slug. Write the file from [`/plan`](plan.md)'s template if it is missing.
- **A slug** resumes `.claude/work/<slug>.md`, which names the plan.
- **A Part**, resolved on the nearest-ancestor `roadmap.md`. A `plan-pending` Part runs [`/plan_part <part>`](plan_part.md)'s brief, then `/plan`'s Plan and Review steps. Its design was approved at design-lock, so the converged plan goes straight to execution, and the owner is asked only about a fork the plan itself raised, batched into one packet. An `arch-pending` or `arch-rework` Part whose open piece needs no owner fork (a pin, a spike, an upstream Part) is design work under the approved lock: run that piece as a job, let `/update_roadmap` re-route the Part, then plan it. An open piece that is an owner fork goes to `/plan`.

With no argument, resume the state file this session already points at, else ask what to drive. An unreadable plan path, an unresolved Part name or a Part in another state is a pre-flight refusal: name it and stop. A refusal is not a halt.

**Owner stop:** none before land. Approval was the gate.

## Authority

The approved plan, or the invocation, is the execution directive. Never ask whether to continue. Approval authorizes every step the plan's Definition of Done names. The only pauses are §Halts, an owner fork, and an action outside the Definition of Done that only the owner may authorize (a push or publish the standing facts do not cover). A rejected tool call stops only that step: finish everything that does not depend on it, then re-present the rejected step with its cost.

The plan bounds what is built, never which domain rules apply. Path-scoped rules (`rules/`), the project's domain skills and the `arch_rule_*` and `gotcha_*` memories stay in force. When a slice introduces something the plan did not describe (a new type, an exported field, a scene node), consult them. The plan and the state file are the authority for scope; a prior transcript is not. Verify each load-bearing empirical claim (a path, a type, a prior-art assertion) before acting on it. A claim that fails is H1. An explicit owner request to add or drop a step overrides the plan, except a floor check or an owner approval.

## Steps

1. **Resume.** Read the plan and the state file. Append a status line to the state file's `## Slices`: the write points this session at it, so the compact hook re-prints it at each compaction. Skip `done` slices and resume the one `in progress`.
2. **Route the job map.** Each row of the plan's `## Job map` runs `inline` with its reason, or as a job (`orchestration`). At each wake, every row whose inputs are settled and that waits on no owner answer gets its job now, up to the transport's concurrency. A row left waiting names its input in the state file. An input the drive can produce itself is a job to launch, never a wait.
   - A job gets an execute brief: the settled spec, its target files and its unit's fast test (`skills/orchestration/reference/brief_checks.md`). Send it through [`/delegate`](delegate.md).
   - A dependent run with no judgment between hops goes as one `dispatch.js` Workflow with its `chains` argument: lanes run in parallel, and each lane runs its jobs in order. Each hop otherwise costs a parent turn at full context.
   - A wait on a long run (an engine gate, a build, a main check) is never sent to a delegate. Start it in the background and act on its one wake.
   - Read every delegated diff and record a verdict before commit. A rejected diff goes back to its job with the named violation. A violation that breaks the slice's design re-plans the slice. Ask [`/review`](review.md) diff mode for a second look when a diff needs one.
3. **Slices.** Each slice follows §Slice rules and lands in one commit. Update its state-file line in the same turn.
4. **Parts in sequence.** In a design with several Parts, a Part is planned as soon as every seam its plan reads is settled, so the next plan drafts while the current slice runs. Re-check a plan against each Part that landed after it was planned before it executes. A halt in one Part stops only the Parts that depend on it; report the Parts completed and remaining.
5. **Close.** Run §Close.

## Slice rules

- **Test first.** RED before GREEN for each behaviour change in the domain the project's test-first rule names, and for a regression's test in any domain: it reproduces the bug. A new check in another domain (a rendered or integration row) runs green, with assertions that name the slice's change.
- **Test scope by tier.** Inside a slice, run only the tests of the items it changes, and after a failure only the failed ones. At each merge of slices, run the scope the merged slices touched. At drive close, run the scope of the drive's domains. The full gate is the main check after the land. `change_control` §Gate cadence binds each tier to its runner. A test that cannot run alone is a test defect, fixed in the test, never a reason to widen a run.
- **Shape.** Each slice reaches green alone. Slice 1 of a multi-slice plan is a thin end-to-end tracer through every layer the feature crosses. When the feature has visible runtime behaviour, the tracer's milestone ends with its rendered scenario run and its log or capture kept, and no slice copies the pattern to further instances before that. A refactor too wide for any vertical slice runs expand–contract: add the new path, migrate callers in batches, delete the old path, each step green.
- **Behaviour kept.** Retirement or refactor work that keeps behaviour first records a fixed-seed baseline on the unchanged tree, then runs the same probe on the changed tree and compares. Keep both runs (command, seeds, tree digest, exit status, output) before a commit claims preserved behaviour. After every deletion, grep the retired names, doc comments included. A check written only to diagnose is removed before the slice closes, unless the plan names it.
- **Evidence.** A slice closes on the evidence its change kind needs, not only a green test. An error-level log line in a run is a failure. A run cited in a comparison records its command, filter and tree digest.
- **Bugs met on the way.** A regression the slice introduced is fixed in the slice. A bug it did not introduce is fixed in the slice when the slice depends on it; otherwise it goes through `worklog_reference` §Do-now gate.
- **New configuration.** A configuration surface the plan did not name answers `reference/rules/design_litmus.md` #1 in the slice rationale.

## Close

1. **Acceptance.** At each Part's close, run its plan's root-acceptance rows on the candidate through the project's agent playtest command. At drive close, before the drive-close scope run, run the rows of every plan serving the goal on the merged candidate. Record each requirement as met, partial, not met or owner-judges, with its run evidence. A requirement no row maps is not met. Only taste is owner-judges (`appetite_invariant.md` *Usability is analytical*). A partial or not-met row is repaired as a slice and re-run before any push. When the owner asked to see the result first, the first report shows the rendered result before any hardening slice runs.
2. **Review.** When the drive changed a shared contract (a floor module, a shared command, tool or skill) or a file in the top 5% by fix commits, or the owner asked, run `/review` diff mode over the drive's commits. Give every finding a disposition: fixed now, logged under a `worklog_reference` §Do-now gate reason, refuted with evidence, or below bar and noted. A finding that needs the owner's taste is an owner fork. An undispositioned finding stops the land.
3. **PR.** When preparing a branch for a PR or merge, run `/pr_ready`.
4. **Land.** Commit each finished slice in categorical commits. Push through `/commit_push` when the state file's standing facts hold push authority for this work. Without that authority, the report names the unpushed commits.
5. **Write-back.** For a Part, `/update_roadmap` flips it `complete`, with step 1's record as its Trigger (`roadmap_triggers_mvp.md` §6.10). When the flip makes an MVP `🧪 Ready for playtest`, surface its Playtest plan (§6.11). If that MVP has no Playtest plan, name the gap; the chain is not clean. For worklog items, run `/worklog complete` with each item's commit. Ad-hoc work has no write-back; name any roadmap topic it touched and route deferrals through `/worklog`.
6. **Report.** Mark the state file's slices done and write the final report to the owner (`reference/deliverable_report.md`). Its screenshots reuse step 1's frames.

## Halts

A halt fires only on information the plan could not have known. State the evidence and stop; never improvise a fix to an out-of-plan problem. A halt leaves the shared checkout green: move the halted slice's uncommitted edits, a RED-first test above all, into `<plan>.<slice>.patch` beside the plan, and name that patch as the slice's resume point in the state file. A red test left in the tree fails every other session's gate. Under `/overnight`'s arming, a halt becomes a Park row in its Close doc, and the drive continues the work that does not depend on it.

- **H1: a load-bearing premise failed.** Write it as "plan says X / reality is Y": a named file or type is missing, a depended-on deliverable behaves wrong, or the plan lacks a decision it needs. A claim that confirms the plan does not halt.
- **H2: scope cannot be bounded.** Execution finds the work materially larger. Growth alone re-plans the milestones. A milestone that cannot be planned until earlier work lands means the work is size parts: halt and propose the split to `/plan`. Dropping or shrinking a planned slice is a halt with an owner question, never a silent cut.
- **H3: an owner fork** (taste, appetite, or a subjective done-condition, per `appetite_invariant.md`). Never resolve it by an agent default. Taste no test can assert: build the mechanism, flag it for the owner, and continue, unless the done-condition is that taste.
- **H4: no commit on any of these:** a red gate that is not a trivial in-scope fix; an unaccounted drop in the test count; an unresolved critical finding; an absent, partial or timed-out gate or review result, which is never green. An owner's quoted waiver of a named check exits H4 for that check only, and the state file keeps the quote. A drop the work's test arithmetic accounts for exactly (N retired, M added) is not unaccounted; the commit message states it.
- **H5: non-convergence.** Revisions stop adding evidence, a repaired slice fails the same way again, or the executor is stuck with no new information. Annotating a disposition and proceeding is never the exit.
- **H6: prior-art conflict.** An existing owner, or another session's in-flight plan over the same files, already owns the concern. Add the work to that owner or halt for the owner; never run a parallel plan.

## Rationalizations to refuse

| Rationalization | Reality |
|---|---|
| "It's a deletion — nothing to test first." | Changed behaviour still gets RED before GREEN; §Slice rules scopes the baseline and the retired-name grep. |
| "The plan is detailed, so RED-before-GREEN is covered." | A detailed plan is starter material, never a licence to skip RED. |
| "It's ad-hoc, so relax the test-first rule or the gate." | Both bind every gated change, whatever its origin. |
| "This taste question is small — pick the sensible default." | H3: batch it or halt on it. |
| "Run every slice in parallel to finish sooner." | Coupled slices wait on their inputs; only independent rows run at once. |
| "It grew, but we're mid-drive, so keep going." | H2: growth re-plans the milestones, and an unplannable milestone halts. |
