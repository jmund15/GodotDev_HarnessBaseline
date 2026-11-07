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
| Inline | Small, already-understood edits and lookups; parent decisions and cross-system synthesis |
| Fan-out | Every pinned job list: `tools/sidecar_fanout.py <jobs file> --detach` applies the ladder's selection line across each job's listed rows (§5b). A job it places on the session's own plan runs as a Workflow agent; the rest run as sidecars |
| Workflow | The call the fan-out prints for the jobs it placed on the session's own plan; an engine the fan-out lacks (`review_fanout.js`); or a measurement of the session model itself |
| Sidecar | A fan-out job on another plan, or one whose fields need its own process (`SIDECAR_ONLY_FIELDS`: another checkout as `workdir`, `window`, launcher flags), or a measurement of process-level configuration; never a hand-written launch of a same-plan job |

Litmus, for work the Inline row does not keep: lookup shape follows `CLAUDE.core.md` §9. Can you enumerate the jobs now? Yes → `/delegate`. No → the unknown is itself one job: a search across files you cannot name yet is a single pinned survey through `/delegate` (`kind: scout`, a fanout-tier row) that returns the map, then `/delegate` over it. The Agent tool is denied: it cannot pin model or effort.

### Governing skills

Before building a brief, read [Governing skills](reference/brief_checks.md#governing-skills).

**Size every dispatch from its work.** This holds for every dispatch: author, survey, review, a later round or a rerun. Measure the work first: its units, their size, their coupling and their distinct risks. Then set four things from that measure, each at the least that delivers the accepted result (Goal):
1. **Route:** inline when handoff costs more than the work, each inline call priced at the parent's whole context (`CLAUDE.core.md` §9), or the work is the parent's judgment; a converged spec dispatches (§11). Price it by what it leaves in the parent's context, not by how many files it touches: a loop (edit, run or render, inspect, fix) over a large file, images or long output is a job even on one known file, and returns its verdict and evidence paths.
2. **Jobs:** one per piece of work that needs its own owner: a disjoint write set (§2's map), units past one context (§11 *Context bound*), or a distinct risk that needs its own reader (§2 *Sizing the width*). Coupled work keeps one owner. A copied roster, a provider name or a vote count never adds a job.
3. **Depth:** the lowest ladder cell that clears the job's residual ambiguity, at the effort its depth needs (§5).
4. **Reading:** only what the work needs, as paths, hunks and excerpts, never a whole artifact for a few rows, within the window (Checklist).

Keep required coverage and independent review. A command's mandates are a coverage floor, not a seat count: every admitted mandate runs (the drive's mandate-admission rule), on the seats §2 sets, and a bespoke lens may be added when it names the failure mode it hunts.

**Standing authorization:** multi-agent orchestration is opted in for every session in this project: dispatch pinned jobs without asking, within the size guideline, on the row and plan the ladder's selection line picks. It holds within the session's live runtime and context capacity; only a larger fan-out asks first. A command that prescribes a fan-out authorizes it at the width §2 sets.

Workflow stays on the current endpoint with its actual model IDs; never accept a silent fallback. Measured capability: a Workflow-dispatched agent has neither Workflow nor Agent; a sidecar child gets neither unless its shape row lists one (`tool_profiles.py` `RESERVED_BUILTINS`). The orchestrator therefore dispatches every lens of a nested fan-out itself.

## Pre-Dispatch Checklist

- Each job lists every row that meets its bar, each with its effort (the jobs file's `rows`), by the ladder's selection line; the fan-out applies that line across them (§5b; a lone sidecar launch names its own launcher and row). Profile is explicit, and availability is checked.
- The brief lists the operations it requires (git, a test runner, a file write, each slash command it tells the child to run).
- The brief's premises and paths pass [`reference/brief_checks.md`](reference/brief_checks.md).
- Read the preflight (`tools/dispatch_size.py`): its fit line gives each job's reading against its compaction point with room for work, and it lists every unmet launch precondition (a kind the fan-out would refuse, a pinned tier with no ladder slice in this context, a governing skill not loaded); a clean preflight is the launch's go. Check required reading outside its scan, including standing doctrine (`-D`). Hand hunks, paths and search targets for on-demand reads that do not fit. Pin a larger window (`-M`) only when all the work must stay resident, and state its price: it can bill at a long-context rate. Set `window: "full"` only where `reference/model_ladder_evidence.md` *Compaction* allows it.
- A multi-job run is marked concurrent, and `guards/single_flight.json` owns what its rail bars. A job needing a machine-wide single-flight tool gets its own dispatch; runtime-test jobs use a solo dispatch or a chain lane within the runtime-slot rule (§2).
- Read the preflight facts (`reference/sidecar_dispatch.md` step 3 for sidecars; a Workflow-routed job's preflight is the guard's plan-room check at the call), then tell the owner one line before dispatch. It names the work measure; the jobs with model, effort, paying plan and reading; the cheapest shape that would deliver the same accepted result (fewer or merged jobs, a lower pin, a search before reading, inline); and the fact that made this shape beat it. §8 compares the line with what ran.
- A job's rows leave out a cheaper row that clears the ladder's floor only for a named reason: quality. Under lean the reason is the `±` clause that bars it, never quality alone. A shape that cannot say why it beats the cheaper one takes the cheaper one. Rows come from `for-role` for this job's work, never from a model a plan or an earlier jobs file named.

## 2. Width

Run independent work together when it cuts elapsed time without hiding integration cost; independent review buys another perspective, not a guarantee from vote count. Keep coupled debugging, one shared design decision, overlapping writes and consistent prose authoring with one owner: width costs reconciliation and context even when agents only read.

Before dispatching enumerable work (plan slices, review findings, failing proofs, files to change), map its items to jobs by write set and dependency, and state the map. Items with disjoint write sets and no shared seam may run as concurrent jobs, up to the transport's concurrency limit (§5b's one-native-job rule; the fan-out's registry `maxChildren` per transport and a caller-set `--max-parallel` total, neither set by default), in worktrees (§7) when they share a checkout. Items sharing a file or a seam serialize, and coupled items merge into one job only within §11's context bound. Set the job count by §11's complete-task cost. Re-map after each measured run.

Before running runtime-test lanes concurrently, read the runtime-slot rule in [`reference/worktrees.md`](reference/worktrees.md).

### Sizing the width

Before seating a fixed review panel, read [`reference/review_width.md`](reference/review_width.md): it owns coverage, width, depth and the size, room and ledger-plan rules.

**Later rounds.** Before seating a review of a subject an earlier round reviewed, read [`reference/later_rounds.md`](reference/later_rounds.md).

## 4. Agent Prompt Structure

A brief states its own task first, then context, purpose, the expected write set, exclusions, the operations it requires, the return artifact and the done-condition. The write set is the map's scheduling forecast, never a fence narrower than the done-condition needs. In its own worktree, a delegate edits any further file the work needs and lists it in its return: a clash costs one merge, a halt costs a relaunch. In a shared checkout, it reports a file another running job writes instead of editing it. Delegates inherit nothing from the conversation: an exclusion not written in the brief is lost, and a lens whose mandate looks unrelated may answer the relayed user message instead. Rich context in the briefing session does not discharge the brief's gates: ground a sub-Part's brief in the whole system's purpose. Supply relevant evidence and paths, not every skill or the transcript. Check inherited claims against source and local inputs, and preserve known values even when an inference fails. A delegate quotes source verbatim and labels its own inference separately.

**A judged job's brief is facts only.** A job whose result is a design or a judgment, not the execution of one (architecting, planning, design, debugging, judging another job's work), gets the goal, the owners' words verbatim, the evidence paths and measurements, and what is fixed: their picks, locked parts, hard limits. It states no cause, fix or design of the briefer's: the owners' words show what is wrong and what they want, and the job finds the cause and the best answer, which can differ from what the words name. Only a settled design (an owner's pick, a golden file, a hand-labeled sample) justifies a brief that settles every behavior, and that brief is tight-spec work pinned per §5, never to an architect row.

**Scope a brief by the defect class, never by the candidates you already found.** Give the class, the discovery obligation and the searches to run (a function's callers, a value's overrides); pass known candidates as "already seen, not the boundary", and require a coverage report. A review or audit brief never caps how many findings to return. An audit of harness changes runs the registered harness lens over them (`/session_audit <range> --lens sa-harness-quality`, or a plan's meta mandates), which holds them to the whole doctrine; a bespoke brief may add a class to that lens, never stand in for it.

- A long investigation or author job writes each unit as it settles, never in one final write; no brief forbids that. A Workflow result is a bounded preview (a tally from it undercounts); get the full set per [`reference/result_retrieval.md`](reference/result_retrieval.md).
- The return contract: the deliverable at the path the brief names plus a digest, and without a named path the final message is the deliverable. Native writable jobs can use the engine's `spillDir` (repo-relative, under `.claude/scratch/`); jobs without Write return inline. A result the parent parses by script returns as fenced JSON Lines blocks, checked per that reference.
- A job over enumerable items returns one checklist line per item, done with evidence or not done with its blocker; an unchecked item is not done. Read the checklist first, then spot-check its evidence; it never replaces a diff review or verification gate. `couldNotSatisfy` names real blockers, and a delegate reports a rail/brief conflict instead of resolving it silently.
- A delegate that writes returns a `choices` block, `changedPaths` instead of a commit in the shared checkout, an `offers` block, and a `reach` block when it changes gated code: read [`reference/writer_returns.md`](reference/writer_returns.md) before briefing or consuming one.

Debugging lanes gather discriminating evidence before fixes, especially after a failed fix. An executor's done-condition and the runs its brief may order: [`reference/brief_checks.md`](reference/brief_checks.md). A findings or claims job's post-check follows §8.

Text a delegate acts on orders a Read of a scratch file, never an existence check: Glob and Grep hide gitignored paths.

Before messaging, amending or editing beside a running job, read [`reference/integration.md`](reference/integration.md) §While a job runs.

## 5. Model & Effort Selection

Before the first pinned dispatch after a start or compaction, `Read` `reference/model_ladder_evidence.md` in full by its absolute path, or run `python3 .claude/tools/model_registry.py for-role <tier>...` bare for every tier you pin: a pipe, `head`, `tail`, filter or redirect earns no credit. A command names a tier; `for-role` resolves it. Declare it only when the call's own data implies none (`tier` on the object carrying `model`, else `args.tier`). A comparison or battery that pins rows on purpose states `args.measurement` instead. `hooks/workflow_provider_guard.py` denies a pin outside the job's tier: its written `tier`, else the tier its data implies (a `review_fanout.js` seat's lenses, a `dispatch.js` job's `shape`). `for-role` drops exhausted plans before any row prints, then lists each fit row with its ladder cells; `[budget-posture]` gives each other plan's band word. Weigh them by the ladder's selection line, at the bar the owner's preference, the lean condition (reference `spend_lean.md`) or the work's rigor sets (rule 1). The parent keeps orchestration, unscoped cross-system decisions and the final design verdict. Owner overrides stay labeled overrides.

Pin the row and the effort per slice by whether its test intent scores what the executor must discover and then judge; never by size, file count or the cost of a mistake. A shape's tier admits both the ambiguous-execution and tight-spec rows; the test intent picks one. Read [`reference/slice_pins.md`](reference/slice_pins.md) for the clearly-defined and ambiguous tests and the `pick`/`spec` fields that record the reading. High stakes buy verification (a RED-first case, an independent review), not a stronger executor.

Before a same-task comparison, or pinning a model the owner marked for its tier or one `for-role` does not list for it, read [`reference/model_evidence.md`](reference/model_evidence.md).

A fixed panel lists eligible models with `for-role`; its effort follows the Effort rule below.

### Per-dispatch harness cost

Read-only built-ins omit project context and cannot write artifacts; supply the rule a lean child needs explicitly, or choose a fuller profile.

### Effort

Every dispatched agent pins model and effort; engines fill no omitted pin, and only a measurement of the session model inherits. An engine's own checked verify and merge steps run on `args.checker`; when the call names none, the guard fills the cheapest verify-tier row by ladder `cost` on the session's transport, at its lowest effort level. Pin `args.checker` yourself when the step needs more. Inspect a committed Workflow script's live pins before reuse: it is not automatically Anthropic-only. Before raising a lens's effort, name the defect class it would resolve (`args.justification` may record it); never bump a whole panel. When a lens under-resolves, find why first: a brief or evidence gap is fixed in the brief, effort starvation raises effort, and a capability limit changes the tier. A later round's seat sets effort from what its changed units can hide; a first round's raise never carries over. `.claude/orchestration_candidates.json` is advisory evidence, no automatic routing. When no measured default separates the legal effort levels, choose the effort level that meets the quality floor and rate effort fit after the result. §5b owns cost-driven descent.

## 5b. Budget, Availability & Transport

Every route spends an allowance: native its provider's, a sidecar the target provider's. The fan-out picks each job's paying plan. Before a job's `plan` override, a pay-per-token route or a credit-plan row, read [`reference/budget_posture.md`](reference/budget_posture.md) §Paying plans.

Capability scanning reads a brief's prefix, the shared context and the files the brief names, and skips a brief it cannot resolve; the parent checks any required operation outside that reading. Read the complete `for-role` listing before claiming no row serves. State a deliberate budget override before use, and never swap a model merely to get a green exit.

When the posture line reports `5h used faster than its time` or a Surplus band, read [`reference/budget_posture.md`](reference/budget_posture.md): it owns the one-native-job rule, its owner override and the close-of-task backlog offer.

Keep quality floors while cutting cost: remove redundant work first, then adjust effort or tier on evidence. A tier follows its work shape in the ladder's role definitions, in every quota band.

## 7. Worktree Caveat

Before a job runs in another checkout (a worktree or `workdir`), or a worktree retires, read [`reference/worktrees.md`](reference/worktrees.md).

## 8. Verification After Integration

When a dispatch returns, read [`reference/integration.md`](reference/integration.md): it owns the engine's `verification` block, the manifest join and recovery before a rerun.

Reviewers report every finding that passes `commands/agents/orchestrator_action_protocol.md` §Reporting Filter, in its §Finding Schema and tagged with its `minRigor` ([`reference/rigor.md`](reference/rigor.md)); [`reference/later_rounds.md`](reference/later_rounds.md) sets what a fix's re-check covers and when the rounds stop. A cosmetic issue (whitespace, comment style) passes only when it shows a real problem. Check a symbol's call sites, not only its public surface. Failed, null or malformed output is uncovered, never "zero findings". For any other deliverable, read it, verify its decisive claims and original-source coverage, then run the applicable checks. Resolve every cited symbol and path in an architecting or planning deliverable before consuming it. Reconcile conflicting evidence directly.

## 9. Authoring a Workflow on the Fly

Before writing a new Workflow script, read [`reference/workflow_authoring.md`](reference/workflow_authoring.md).

## 10. Context Checkpointing (long-horizon drives)

Before a drive keeps task state across phases or compactions, read [`reference/checkpointing.md`](reference/checkpointing.md).

## 11. Dispatch Doctrine (efficiency-to-quality)

Dispatch a converged spec, work enumerated with a check (a plan slice, a close-out recipe, a known follow-on step), at the ladder's tight-spec cell; the parent keeps final judgment: a red's ruling, what lands, the commit. A delegatable unit is one coherent job whose file set you can enumerate now and which finishes in one session. A verified finding ledger with exact old/new text is a converged spec. Author the verification and first exemplar before batching clones. A delegate waits on its own runs, and hands a lone wait past about 30 minutes back to the parent, by `guards/any.md`; a brief never orders a delegate to wait until such a job ends (the cache-lifetime memory note holds the cost model).

Before a review-gated slice, a plan draft or a split over disjoint units, read [`reference/drive_dispatch.md`](reference/drive_dispatch.md): *Continue file*, *Plan drafts* and *Context bound*.

Count complete-task cost: preparation, cold starts, model work, repair, verification and recovery. Bound parent prose, not evidence.
