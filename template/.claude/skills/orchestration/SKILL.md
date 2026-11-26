---
name: Orchestration
description: >-
  Use before delegating or coordinating multi-step work, including 3+ unrelated test failures, multiple subsystems broken independently, batch PR reviews, cross-domain audits, or independent work units to parallelize. SKIP small inline edits and known lookups where handoff costs more than the work.
---

# Orchestration

**Goal:** the harness goal in `CLAUDE.core.md` §Core Principles, applied to every dispatch choice (inline or delegated, width, tier, effort); §5b's quality floors are never the trade.

**Settle each question at the first stage whose inputs can.** A stage (brief, plan, check, fold, execution, review) hands forward only what needs a later stage's input: an execution result, an owner answer or an independent reading. What its own reads, searches or checks settle, it settles; a question passed on costs a later, wider stage the same read. Each handed-forward item names the input it waits on.

## 0. Dispatch Shape — decide this FIRST

| Mechanism | Use |
|---|---|
| Inline | What `CLAUDE.interactive.md` §When work becomes a job keeps with you; anything else is a job |
| Fan-out | Every pinned job list: `tools/sidecar_fanout.py <jobs file> --detach` applies the ladder's selection line across each job's listed rows (§5b). A job it places on the session's own plan runs as a Workflow agent; the rest run as sidecars |
| Workflow | The call the fan-out prints for the jobs it placed on the session's own plan; an engine the fan-out lacks; or a measurement of the session model itself |
| Sidecar | A fan-out job on another plan, or one whose fields need its own process (`SIDECAR_ONLY_FIELDS`: another checkout as `workdir`, `window`, `extraArgs`), or a measurement of process-level configuration; never a hand-written launch |

Litmus, for work the Inline row does not keep: a chain of reads follows `CLAUDE.interactive.md` §When work becomes a job. Can you enumerate the jobs now? Yes → `/delegate`. No → the unknown is itself one job: a search across files you cannot name yet is a single pinned survey through `/delegate` (`kind: scout`, a fanout-tier row) that returns the map, then `/delegate` over it. The Agent tool is denied: it cannot pin model or effort.

### Governing skills

Before building a brief, read [Governing skills](reference/brief_checks.md#governing-skills).

**Size every dispatch from its work.** This holds for every dispatch: author, survey, review, a later round or a rerun. Measure the work first: its units, their size, their coupling and their distinct risks. Then set four things from that measure, each at the least that delivers the accepted result (Goal):
1. **Route:** inline only for what `CLAUDE.interactive.md` §When work becomes a job keeps with you, or when handoff costs more than the work; a converged spec dispatches (§11). Price it by what it leaves in the parent's context, not by how many files it touches: a loop (edit, run or render, inspect, fix) over a large file, images or long output is a job even on one known file, and returns its verdict and evidence paths.
2. **Jobs:** one per piece of work that needs its own owner: a disjoint write set (§2's map), units past one context (§11 *Context bound*), or a distinct risk that needs its own reader (§2 *Sizing the width*). Coupled work keeps one owner. A copied roster, a provider name or a vote count never adds a job. Gathering and judging are separate jobs (`brief_checks.md` §Gather, then judge).
3. **Depth:** the lowest ladder cell that clears the job's residual ambiguity, at the effort its depth needs (§5).
4. **Reading:** only what the work needs, as paths, hunks and excerpts, never a whole artifact for a few rows, within the window (Checklist).

Keep required coverage and independent review. A command's mandates are a coverage floor, not a seat count: every admitted mandate runs (the drive's mandate-admission rule), on the seats §2 sets, and a bespoke lens may be added when it names the failure mode it hunts.

**Standing authorization:** multi-agent orchestration is opted in for every session in this project: dispatch pinned jobs without asking, within the size guideline, on the row and plan the ladder's selection line picks. It holds within the session's live runtime and context capacity; only a larger fan-out asks first. A command that prescribes a fan-out authorizes it at the width §2 sets.

Workflow stays on the current endpoint with its actual model IDs; never accept a silent fallback. A dispatch is never granted Workflow or Agent (`reference/sidecar_dispatch.md`), so the orchestrator dispatches every lens of a nested fan-out itself.

## Pre-Dispatch Checklist

- Each job lists every row that meets its bar, each with its effort (the jobs file's `rows`), by the ladder's selection line; the fan-out applies that line across them (§5b). Availability is checked.
- The job declares one `kind` (`reference/sidecar_dispatch.md` §Job kind), the lowest one whose doctrine the work needs.
- The brief's premises and paths pass [`reference/brief_checks.md`](reference/brief_checks.md).
- Read the preflight (`tools/dispatch_size.py`): its fit line gives each job's reading against its compaction point with room for work, and it lists every unmet launch precondition (a launcher-read file that does not resolve, a kind the fan-out would refuse, a pinned tier with no ladder slice in this context, the orchestration skill not loaded); a clean preflight is the launch's go. Check required reading outside its scan, including standing doctrine. If the set does not fit, hand paths and search targets for on-demand reads. A larger window (`-M`, `window: "full"`) takes [`reference/dispatch_rows.md`](reference/dispatch_rows.md) §A larger window.
- A multi-job run is marked concurrent, and `guards/single_flight.json` owns what its rail bars. A job needing a machine-wide single-flight tool gets its own dispatch; runtime-test jobs use a solo dispatch or a chain lane within the runtime-slot rule (§2).
- Read the preflight facts (`reference/sidecar_dispatch.md` step 3 for sidecars; a Workflow-routed job's preflight is the guard's plan-room check at the call), then tell the owner one line before dispatch. It names the work measure; the jobs with model, effort, paying plan and reading; the cheapest shape that would deliver the same accepted result (fewer or merged jobs, a lower pin, a search before reading, inline); and the fact that made this shape beat it; and each open item that waits on this job, with the shared file or output that makes it wait. §8 compares the line with what ran.
- Before listing a job's rows, read [`reference/dispatch_rows.md`](reference/dispatch_rows.md) §Which rows a job lists: where rows come from, the named reason that bars a cheaper row that clears the ladder's floor, and how effort is set.

## 2. Width

Run independent work together when it cuts elapsed time without hiding integration cost; independent review buys another perspective, not a guarantee from vote count. Keep coupled debugging, one shared design decision, overlapping writes and consistent prose authoring with one owner: width costs reconciliation and context even when agents only read.

At every dispatch decision and after each measured run, map everything open in the session's work (plan slices, review findings, failing proofs, files to change, the session's own gates and renders, the drive's remaining items and owner rulings) to jobs by write set and dependency, and state the map. A priority order orders launches; it never by itself holds an independent item back. Items with disjoint write sets and no shared seam may run as concurrent jobs, up to the transport's concurrency limit (§5b's one-native-job rule; the fan-out's sidecar limits are `reference/sidecar_dispatch.md`'s), in worktrees (§7) when they share a checkout. Items sharing a file or a seam serialize, and coupled items merge into one job only within §11's context bound. Set the job count by §11's complete-task cost.

Before running runtime-test lanes concurrently, read the runtime-slot rule in [`reference/worktrees.md`](reference/worktrees.md).

### Sizing the width

Before seating a fixed review panel, read [`reference/review_width.md`](reference/review_width.md): it owns coverage, width, depth and the size, room and ledger-plan rules.

**Later rounds.** Before seating a review of a subject an earlier round reviewed, read [`reference/later_rounds.md`](reference/later_rounds.md).

## 4. Agent Prompt Structure

A brief states its own task first, then context, purpose, the expected write set, exclusions, the operations it requires, the return artifact and the done-condition. The write set is the map's scheduling forecast, never a fence narrower than the done-condition needs. In its own worktree, a delegate edits any further file the work needs and lists it in its return: a clash costs one merge, a halt costs a relaunch. In a shared checkout, it reports a file another running job writes instead of editing it. Delegates inherit nothing from the conversation: an exclusion not written in the brief is lost, and a lens whose mandate looks unrelated may answer the relayed user message instead. Rich context in the briefing session does not discharge the brief's gates: the brief supplies what its job needs and cannot load by itself, as evidence and paths, never the transcript. It never restates what the job's profile loads (`CLAUDE.md`, the standing rules); a fact every brief would need belongs in the harness. Grounding the profile does not load goes in as the passage the work rests on, quoted with its path; a whole skill or document is named by path for the job to load, never copied. Preserve known values even when an inference fails. A delegate quotes source verbatim and labels its own inference separately.

**A brief leaves the thinking to the job.** It hands over what the job cannot know, each input at the weight it holds; how much it settles follows the work. Settled work (an owner's pick, a golden file, a hand-labeled sample) gets a full spec, pinned per §5 as tight-spec work, never to an architect row. Open-ended work (designing, architecting, planning, debugging, judging another job's work) gets the context its analysis needs. What the owners decided (words, a pick, an artifact or build they approved, a lock) is fixed and named so; a question, an example or an option they passed over is open, and so is an earlier agent's decision (in code, a comment, a report, a brief or a plan): it reaches the job as a claim with its source and its reason, and a finding against it counts; a claim about current state goes with what checking it against source found. The briefer adds nothing that moves a weight: no hedge on a decision, no menu closing an open question, no cause, fix or design the owners did not pick. Owner words go verbatim, as evidence of what they want and why. **The brief's question is the owners' principle, whole, and the deliverable is indexed by it, never by what the briefer already holds:** candidates, field shapes, the owners' examples, a split into sub-questions or method steps, its own framing of the problem. Those enter as "already seen, not the boundary", for the job to reconcile against the principle at the end, because a plan or audit follows the index its brief gives it, whatever the brief says about the boundary. Every seat on one question gets the whole question; a lens names where a seat starts, never its boundary. [`reference/brief_checks.md`](reference/brief_checks.md) holds the launch check for a judged job.

**Scope a brief by the defect class, never by the candidates you already found.** Give the class, the discovery obligation and the searches to run (a function's callers, a value's overrides), and require a coverage report. A review or audit brief never caps how many findings to return. An audit of harness changes runs the registered harness lens over them (`/session_audit <range> --lens sa-harness-quality`, or a plan's meta mandates), which holds them to the whole doctrine; a bespoke brief may add a class to that lens, never stand in for it.

- A long writing job (an `author`) writes each unit as it settles, never in one final write; no brief forbids that, and a read-only job returns its report as its final message instead. A Workflow result is a bounded preview (a tally from it undercounts); get the full set per [`reference/result_retrieval.md`](reference/result_retrieval.md).
- The return contract: the deliverable at the path the brief names plus a digest, and without a named path the final message is the deliverable. Native writable jobs can use the engine's `spillDir` (repo-relative, under `.claude/scratch/`); jobs without Write return inline. A result the parent parses by script returns as fenced JSON Lines blocks, checked per that reference.
- A job over enumerable items returns one checklist line per item, done with evidence or not done with its blocker: [`reference/brief_checks.md`](reference/brief_checks.md) §Return checklist.
- A delegate that writes returns a `choices` block, `changedPaths` instead of a commit in the shared checkout, an `offers` block, and a `reach` block when it changes gated code: read [`reference/writer_returns.md`](reference/writer_returns.md) before briefing or consuming one.

Debugging lanes gather discriminating evidence before fixes, especially after a failed fix. An executor's done-condition and the runs its brief may order: [`reference/brief_checks.md`](reference/brief_checks.md). A findings or claims job's post-check follows §8.

A running job's course changes by amendment, never by a relaunch or a stop: read [`reference/integration.md`](reference/integration.md) §While a job runs before messaging or editing beside one.

## 5. Model & Effort Selection

Before the first pinned dispatch after a start or compaction, `Read` `reference/model_ladder_evidence.md` in full by its absolute path, or run `python3 .claude/tools/model_registry.py for-role <tier>...` bare for every tier you pin: a pipe, `head`, `tail`, filter or redirect earns no credit. A command names a tier; `for-role` resolves it. Declare it only when the call's own data implies none (`tier` on the object carrying `model`, else `args.tier`). A comparison or battery that pins rows on purpose states `args.measurement` instead. `hooks/workflow_provider_guard.py` denies a pin outside the job's tier: its written `tier`, else the tier its data implies (a `review_fanout.js` seat's lenses, a `dispatch.js` job's `shape`). `for-role` drops exhausted plans before any row prints, then lists each fit row with its ladder cells; `[budget-posture]` gives each other plan's band word. Weigh them by the ladder's selection line at the job's bar (rule 1); lean moves that bar (reference `spend_lean.md`), and rigor never does, because high stakes buy verification (reference `rigor.md`). The parent keeps orchestration, unscoped cross-system decisions and the final verdict on a design a job wrote (`orchestration` reference `brief_checks.md` §Gather, then judge). Owner overrides stay labeled overrides.

Pin the row per slice by whether its test intent scores what the executor must discover and then judge, never by size, file count or the cost of a mistake. A shape's tier admits both the ambiguous-execution and tight-spec rows; the test intent picks one. Read [`reference/slice_pins.md`](reference/slice_pins.md) for the clearly-defined and ambiguous tests and the `pick`/`spec` fields that record the reading. High stakes buy verification (a RED-first case, an independent review), not a stronger executor.

Before a same-task comparison, or pinning a model the owner marked for its tier or one `for-role` does not list for it, read [`reference/model_evidence.md`](reference/model_evidence.md).

### Per-dispatch harness cost

Read-only built-ins omit project context and cannot write artifacts; supply the rule a lean child needs explicitly, or choose a fuller profile.

### Effort

Every dispatched agent pins model and effort, and only a measurement of the session model inherits; a check-scored job's effort is a lookup every route fills from its `spec`. Before setting an effort by hand (an owner pin, an engine's `args.checker`, a review seat, a later round), or when a job under-resolves, read [`reference/effort.md`](reference/effort.md). §5b owns cost-driven descent.

## 5b. Budget, Availability & Transport

The fan-out picks each job's paying plan. Before a job's `plan` override, a pay-per-token route or a credit-plan row, read [`reference/budget_posture.md`](reference/budget_posture.md) §Paying plans.

Read the complete `for-role` listing before claiming no row serves. State a deliberate budget override before use, and never swap a model merely to get a green exit.

When the posture line reports `5h used faster than its time` or a Surplus band, read [`reference/budget_posture.md`](reference/budget_posture.md): it owns the one-native-job rule, its owner override and the close-of-task backlog offer.

Keep quality floors while cutting cost: remove redundant work first, then adjust effort or tier on evidence. A tier follows its work shape in the ladder's role definitions, in every quota band.

## 7. Worktree Caveat

Before a job runs in another checkout (a worktree or `workdir`), or a worktree retires, read [`reference/worktrees.md`](reference/worktrees.md).

## 8. Verification After Integration

When a dispatch returns, read [`reference/integration.md`](reference/integration.md): it owns the engine's `verification` block, the manifest join and recovery before a rerun.

Reviewers report every finding that passes `commands/agents/orchestrator_action_protocol.md` §Reporting Filter, in its §Finding Schema and tagged with its `minRigor` ([`reference/rigor.md`](reference/rigor.md)); [`reference/later_rounds.md`](reference/later_rounds.md) sets what a fix's re-check covers and when the rounds stop. A cosmetic issue (whitespace, comment style) passes only when it shows a real problem. Check a symbol's call sites, not only its public surface. Failed, null or malformed output is uncovered, never "zero findings". For any other deliverable, read it, verify its decisive claims and original-source coverage, then run the applicable checks. Resolve every cited symbol and path in an architecting or planning deliverable before consuming it. Reconcile conflicting evidence directly.

## 9. Authoring a Workflow on the Fly

Before writing a new Workflow script, read [`reference/workflow_authoring.md`](reference/workflow_authoring.md).

## 10. Goal Tracking and Context Checkpointing (long-horizon runs)

A long run (a drive, an `/overnight` or `/goal` run, any task with a record) is steered by its goal's requirements, never its plan's step list. At each checkpoint (a job's return, a compaction, a finished slice, a wait on jobs), and before a drive keeps task state across phases or compactions, read [`reference/checkpointing.md`](reference/checkpointing.md): §Goal tracking judges each requirement against current evidence.

## 11. Dispatch Doctrine (efficiency-to-quality)

Dispatch a converged spec, work enumerated with a check (a plan slice, a close-out recipe, a known follow-on step), at the ladder's tight-spec cell; the parent keeps final judgment: a red's ruling, what lands, the commit. A delegatable unit is one coherent job whose file set you can enumerate now and which finishes in one session. A verified finding ledger with exact old/new text is a converged spec. Author the verification and first exemplar before batching clones.

Before a review-gated slice, a plan draft or a split over disjoint units, read [`reference/drive_dispatch.md`](reference/drive_dispatch.md): *Continue file*, *Plan drafts* and *Context bound*.

Count complete-task cost: preparation, cold starts, model work, repair, verification and recovery. Bound parent prose, not evidence.
