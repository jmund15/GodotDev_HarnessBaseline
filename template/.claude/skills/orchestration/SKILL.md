---
name: Orchestration
description: >-
  Use before delegating or coordinating multi-step work: choose scope, model/effort, transport and evidence contracts. Skip small inline edits and known lookups where handoff costs more than the work.
---

# Orchestration

**Goal:** orchestration exists to cut tokens and cost per accepted result at unchanged quality. Every dispatch choice (inline or delegated, width, tier, effort) is judged by that; §5b's quality floors are never the trade.

`/delegate` owns ordinary jobs; fixed-panel commands own their coverage. The role ladder owns model judgment, the registry owns availability/capabilities/prices, and `reference/sidecar_dispatch.md` owns launcher mechanics. Do not copy their tables into each caller.

## 0. Dispatch Shape — decide this FIRST

| Mechanism | Use |
|---|---|
| Inline | Small, already-understood edits/lookups; parent decisions and cross-system synthesis |
| Workflow | Pinned jobs and independent lenses on this session's transport; existing dispatch/review/chain engines |
| Single Agent | A genuine exploratory/context-fork exception; state inherited settings the API cannot pin. A model-pinned Agent that is not Explore/Plan/fork is denied by `dispatch_mechanism_guard.py` unless the brief carries `AGENT-EXCEPTION: <why the jobs are not enumerable yet>` |
| Sidecar | Another transport, or a documented need for isolated process-level configuration |

Litmus: can you enumerate the jobs now? Yes → `/delegate`. No → one exploratory Agent, then `/delegate` over what it found.

**One suitable arm per independently needed job.** Roster availability does not create work. Keep required coverage and independent review; add a job for a distinct risk, not another provider name. A command's mandates are a floor: every mandate always runs, and a bespoke lens may be added when the risk warrants, each naming the failure mode it hunts. How many mandates share a seat follows §2 *Sizing the width*. Same-task comparisons require an explicit comparison request, named arms, frozen inputs and finite budget. `/pin_ab` owns the common comparison case and carries §5b's currency override into its dispatch when the band requires it.

**Standing authorization:** a pinned Workflow within the session's workflow size guideline needs no per-use opt-in, so dispatch it without asking. Only a larger fan-out asks first. A command that prescribes a fan-out authorizes it at the width §2 sets for that command; do not drop a mandate or run it inline.

Workflow/Agent stay on the current endpoint. Use its actual model IDs; do not send another provider's vocabulary or silently accept a fallback. The caller owns fan-out; delegates do not invent nested jobs. Subagents cannot invoke Workflow or Agent: a nested fan-out is materialized by the orchestrator, which dispatches every lens itself. Use `/delegate` for declared jobs and existing engines for fixed panels. A recurring shape earns reuse, not another bespoke wrapper.

For comparisons, keep source inputs frozen until every arm returns. For ordinary authoring, expected output edits are not a failed comparison. Read-only findings over changed inputs need targeted revalidation, not a blanket reset or rerun.

## Pre-Dispatch Checklist

- Scope, inputs, exclusions and done-condition are concrete.
- Model, effort, profile, transport and currency are explicit; current availability is checked. Read the band from the latest `[budget-posture]` line (`hooks/budget_posture.py`); if absent, `<TEMP>/cc-cachestat-<session_id>.json` `rate_limits`.
- Writes are disjoint or serialized. Shared runtime tests/LSP are single-flight.
- Each required result has a recoverable artifact or transcript route; no missing arm can look clean.

## 1. When to Parallelize

Run independent work together when it reduces elapsed time without hiding integration cost. Independent review buys another perspective, not a guarantee from vote count. Prefer a few coherent jobs over many tiny cold starts. Pass the intended independent jobs together; do not serialize them without a real dependency or resource constraint.

## 2. When NOT to Parallelize

Keep coupled debugging, one shared design decision, overlapping writes and consistent prose authoring in one owner. Width costs reconciliation, context and conflicts even when agents only read. Required coverage stays; speculative width does not.

### Sizing the width

Choose width from independent coverage, resource limits and integration work. Preserve caller-declared exclusions and budgets. A command's coverage contract cannot be silently reduced to save time.

A fixed review panel has three settings. **Coverage** is the command's mandates, and every mandate runs. **Width** is the seat count: a seat carries one or more mandates, as the command's seat map assigns them per tier. **Depth** is effort per seat; `/plan_check` takes it from its ladder tier row, and every other panel keeps its own effort rule.

The caller picks the tier from facts about the subject and reports the fact that set it. A fact the caller cannot establish counts toward Wide. The first matching row wins.

| Tier | Litmus | Width |
|---|---|---|
| Wide | The subject (a) performs an operation git cannot undo: it deletes or rewrites untracked files, vault notes or user data, or publishes externally; (b) crosses the Jmodot/game boundary or changes 3+ top-level subsystems; (c) replaces a contract whose callers sit in 2+ subsystems; or (d) adds or changes a rule in an always-loaded surface | one seat per mandate |
| Small | Every change edits an existing file; nothing adds a type, file, export, node, command or concept; nothing is deleted; ≤5 files | one seat for all mandates |
| Standard | Everything else | the command's Standard seat map |

Tier facts come from the plan for `/plan_check` (its 1b), from `git diff --stat` plus the added and deleted paths for a diff, and from the planned files and named subsystems for a red-team design. A command without a seat map runs one seat per mandate at every tier. A tier never lowers a command's own trigger threshold.

A merged seat keeps per-mandate coverage. Its `checked.basis` names every mandate it carried with that mandate's outcome: findings, clean with the evidence checked, or not reached. Each finding carries the mandate key that produced it. The caller reports a not-reached mandate as UNCOVERED. A re-dispatch (a second round, a recovery) sends the seat only its changed or uncovered mandates. The caller records one verdict row per seat with its `mandates` block (`/orchestration_metrics` *Incremental rating*); the panel-yield line in that report is the trigger to re-open a seat map.

## 3. The Dispatch Procedure (manual `Agent`, legacy fallback)

Prefer `/delegate` and pinned Workflow engines. The Agent exception does not expose an effort pin: record the inheritance trade rather than pretend it was pinned. Wait for completion notifications; do not poll a result the runtime will deliver.

Message another session or agent only when it changes their next action: ownership transfer, dependency ready, verified conflict or a blocker needing a decision. Send the evidence path and requested action, not routine progress. A message may resume a finished agent; do not reopen a completed audit or review files still being edited.

Preserve original findings and provenance when integrating. Location is not defect identity. A completed process is not a verified deliverable.

## 4. Agent Prompt Structure

A brief names context, purpose, exact task, allowed write set, exclusions, output artifact and done-condition. Every Workflow brief states its own task first. Engines tell agents that the relayed user message is context, but a lens whose mandate looks unrelated can still answer that message instead. Supply relevant evidence/paths, not every skill or the whole transcript. Check inherited claims against source and local inputs; preserve known values even when an inference fails. Keep provenance, uncertainty and runtime overrides explicit. Delegates and `write_doc` inherit nothing from the conversation; an exclusion not written in the brief is lost.

**Scope a brief by the defect class, never by the candidate list you happened to find.** When the reason for delegating is that your own sweep was thin, handing over that sweep's output makes your exploration the delegate's ceiling and reproduces the gap at higher cost. Give the class, the discovery obligation and the searches to run; pass any candidates as "already seen, not the boundary", and require a coverage report naming what was searched. §11's enumerate-before-dispatch rule sizes a CONVERGED job; it never licenses capping an audit at what you already knew.
<!-- retire-when: review-by: 2027-09-17 -->

Keep full results on disk and a short actionable digest in the parent. A profile without Write cannot promise a spill. A subagent is told to return findings as text rather than write report files, so it declines a named report path: brief for the full result as its final message or through the engine's `spillDir`. Long investigations preserve completed evidence before their last message. `couldNotSatisfy` names actual blockers; no outside-scope edits or alternative-tool retry after denial.

Debugging lanes need discriminating evidence before fixes, especially after a prior fix failed. Hand verification results back with the patch, not a claim that tests probably ran. Foreground owned tests may run only where the job's resource scope permits them.

## 5. Model & Effort Selection

Commands name a tier, never a model; resolve it on your own seat through `model_registry.py for-role <tier>` and `reference/model_ladder_evidence.md`. Inspect only the fields needed for the choice; do not repeatedly dump the full roster. Explicit owner overrides remain labeled overrides, not capability promotions.

- Keep orchestration, unscoped cross-system decisions and the final design verdict with the qualified parent/owner.
- Use a qualified executor for scoped design, difficult debugging and consequential review.
- Use the fan-out/validation tier for anchored checks and tight execution; a scout only for verifiable locate/enumerate/extract work.
- Copyable bulk I/O can use the local worker. Derived judgment needs a model capable of the inference.

Pin effort by residual ambiguity, not importance or file count; respect the model's measured behavior and legal transport values. Fixed review panels follow their ladder tier row where one exists (`/plan_check`). Unknown effective effort stays unknown. Requested pins, runtime model identity and capability evidence are different facts. A single result cannot promote or demote a model globally.

### Per-dispatch harness cost

Choose the smallest profile that carries the required tools and doctrine. Read-only built-ins omit project context and cannot write artifacts; a full profile costs more but may be necessary to apply project rules. Supply the relevant rule explicitly or choose the appropriate profile—never assume omitted context was inherited. Do not treat historical startup-token figures as constants.

### Effort

Every dispatched agent pins both model and effort. The deliberate inherit case is a measurement of the session model itself. Raise a lens above its default cell only per-lens, with `args.justification` naming the ambiguity the raise resolves; never a blanket panel bump. If an API cannot express a required setting, use a supported route and state the trade; do not invent an option. Check `.claude/orchestration_candidates.json` only as advisory evidence, not automatic routing authority. Unsure between `low` and `medium` → `medium`. When a lens under-resolves, raise its effort before raising its tier: effort buys turns. §5b owns cost-driven descent. A sharp mandate substitutes for effort only on sub-architectural inputs; on an architecturally-loaded plan, executor-tier `high` finds what `medium` misses.

## 5b. Budget, Availability & Transport

Read the current band's actual currency before dispatch. The native route spends its provider's allowance; a sidecar spends the target provider's. Quota is not an Anthropic-dollar estimate. A zero-priced route still has eligibility, privacy and service limits.

Availability comes from the registry; budget bands from `quota_bands.py`. Preserve their guards. An unreadable band is unknown, not Surplus or Hot. Under a pressured band `hooks/workflow_provider_guard.py` denies a pinned dispatch until the call states its currency: `args.currency` plus `args.currencyReason`, or an Agent prompt line `CURRENCY: anthropic — <why>`. State it once per band, then hold it on the session record. A deliberate budget override is stated before use, never chosen silently after a failed job. Do not substitute a model/provider merely to get a green exit.

Keep quality floors while controlling cost: first remove redundant work, then adjust eligible effort/tier on evidence. Pressure moves the tier, ambiguity moves the effort: under pressure, enumerable checks and converged execution drop to the fan-out tier, while planning, architecting, architectural plan review, red-team and open-judgment review hold the executor tier as the reserved floor. Record the accepted result and repair work. Do not infer a cheaper counterfactual from one clean run or compare mismatched cohorts.

Native engines accept this transport's declared IDs/efforts. Off-transport jobs use its registered launcher through the sidecar owner. Literal pins and short args avoid hidden provider translation; large briefs stay in files.

## 6. The 15-Agent Cap (manual `Agent` dispatch only)

Manual batches stay within 15 total agents, including nested work. Workflow concurrency follows the live runtime; do not encode a stale platform count as policy. Nested delegation is not a substitute for parent-owned coverage.

## 7. Worktree Caveat

Shared-checkout writers partition or serialize ownership. Genuine isolation is the per-agent `isolation: "worktree"` option, for write-parallel work only; it costs setup time and disk per agent, and its submodule must be initialized before relevant builds. Read-only comparisons use frozen inputs. Neither idle state nor a repo-path substring proves a process/file belongs to this job.

GdUnit4 and shared csharp-ls operations are single-flight. Main owns broad verification; isolated non-runtime proofs may run within a scoped job. A RED needs an executed assertion failure, not a zero-match exit code.

## 8. Verification After Integration

Read the delivered artifact, verify decisive claims and original-source coverage, then run the applicable checks. A delegate's architecting or planning deliverable clears the citation floor before you consume it, whatever model wrote it (`skills/_brainstorm_shared/design_contract.md` *Citations resolve BEFORE dispatch*). Reconcile conflicting evidence directly. Failed/null/malformed output is uncovered; never filter it into “zero findings.” Check each fan-out's journal model column against the currency you intended.

Recover before another paid call: artifact, then transcript/`/salvage_fanout`. Confirm a job is terminal before considering a replacement; no-result and still-running are different states. Record outcome on consumption and build the existing manifest with one exact evidence join per label. Keep process status separate from acceptance.

## 9. Authoring a Workflow on the Fly

Use the `workflow-authoring` reference for the live API. Prefer the existing dispatch/review/chain engines. New scripts have literal metadata, explicit pins, PINS logging and schemas for machine-consumed results. Bounded-ambiguity stages (judges, verifiers, extraction) hard-set effort in the script; per-invocation stages (arms, executors) take a script default plus an `args` override with a named justification. Write-shaped schemas carry `couldNotSatisfy`, plus `redVerification` on TDD stages. Preserve full evidence without hard caps on archival finding arrays.

Use `pipeline()` unless the next stage needs all prior results together. State any coverage cap and budget. Pass paths/short scalars, not giant nested args. Agents compare, never discover: push content through args, because fanned `Grep`/`Glob` return false empties (`gotcha_workflow_fanout_search_false_absence`). Keep completed results on resume; inspect a missing result before re-dispatch. Outcome and effort-fit ratings are recorded when consumed, not reconstructed from memory at session end.

## 10. Context Checkpointing (long-horizon drives)

Keep a small task state: requirements/exclusions, decisions, active jobs, accepted artifacts, verification and next action, in `tools/task_record.py` (one record per task; decisions carry evidence, jobs come from the dispatch journal). Write it at each phase boundary and on each consumed result; the compaction hook re-injects it. Load that state plus the active procedure after interruption, not every completed phase body. After resume, recheck required inputs and decisions before trusting receipts.

Leave automatic compaction enabled for ordinary long work. Verify the recipient's usable window and inherited overrides; a client declaration does not grant backend capacity. Do not blanket-switch models to 1M or disable compaction to avoid recovery work. Every turn re-sends the live context, so session cost grows with it on every transport, and a provider may bill a larger prompt at a higher per-token rate (registry `longContextTier`). Take a large window for the turn that needs it, then compact (`gotcha_long_context_gpt_sessions_cost_by_context_size`). Use supported compaction at a useful boundary; do not stop a drive merely because context is high. Dispatch the next long-running job before a deliberate compaction; in-flight dispatches survive it. Compact in session at a boundary, never by handing off to a fresh session. Read `context_pct` from `<TEMP>/cc-cachestat-<session_id>.json`, never self-estimate, and report it only when it changes the next action.

## 11. Dispatch Doctrine (efficiency-to-quality)

A converged spec is the dispatch signal on every execution surface: executing it inline from a higher-tier or higher-effort session is the defect. Dispatch it at a low-effort executor pin even when the session model is that row; the parent keeps integration and final judgment. A delegatable unit is one coherent job whose file set you can enumerate now and whose work finishes in one session; if you cannot enumerate the files, scope it further before dispatching. Small surgical work can stay inline when handoff is larger than the work. A verified finding ledger with exact old/new text is a converged spec: dispatch the edits and keep verification in the parent. Author the verification and first exemplar before batching clones. Subagent prompt caches expire far sooner than the main session's: keep long blocking runs (full suite, full build) in the parent and delegate the authoring around them. Never interleave long-blocking calls with edits inside one subagent.

**Slices are commit units, not dispatch units.** Before the first execution dispatch, map the plan's slices to jobs from their write sets and interface dependencies, and state the map. Slices with disjoint write sets and no dependency run concurrently, each in its own worktree when they share a checkout. Merge consecutive coupled slices into one job only while its measured peak context stays well under the executor's window: every later turn re-reads the earlier slice's context, so a merge saves startup, not tokens. A slice smaller than its handoff runs inline. Re-map after each measured run.

Count complete-task cost: preparation, cold starts, model work, repair, verification and recovery. Bound parent prose, not evidence. A paid task is not retried from scratch merely because its transport stopped. Use the live background/monitor lifetime contract so a shell timeout does not discard a healthy long job.

## Cross-references

`commands/delegate.md` · `reference/sidecar_dispatch.md` · `reference/model_ladder_evidence.md` · `commands/agents/orchestrator_action_protocol.md` · `commands/agents/review_agents.md` (spawn rules) · `commands/salvage_fanout.md`
