---
allowed-tools: Bash(git ls-files:*), Bash(git grep:*), Glob, Grep, Read, Edit, Task, SlashCommand, EnterPlanMode, ExitPlanMode
description: Autonomously drive a plan-pending Part to an approval-ready plan — plan_part brief → Plan Mode draft → plan_check convergence → ExitPlanMode. Batches taste forks; halts on 6 valves.
---

# /plan_drive — Autonomous Plan-Production Loop

The planning-phase counterpart to [`/plan_handoff`](plan_handoff.md). Where `/plan_handoff` drives an *approved plan* to shipped code, `/plan_drive` drives a *plan-pending Part* to an **approval-ready plan**, autonomously running the brief → draft → audit → revise cycle and stopping at the one human gate that matters: the native **`ExitPlanMode` approval**.

It composes existing surfaces — [`/plan_part`](plan_part.md), Plan Mode (the Claude Code built-in), and [`/plan_check`](plan_check.md) — and adds the convergence loop + halt valves that make unattended plan production safe.

## What this is — and is NOT

- **SERIAL orchestrator, not a `Workflow`.** It carries state across an ordered brief→draft→audit→revise cycle. The only fan-out is `/plan_check`'s own 3 parallel audit agents (which it owns).
- **It USES Plan Mode natively — it does not bypass or reimplement it.** Per your direction, `/plan_drive` enters Plan Mode immediately after the `/plan_part` brief, so Plan Mode's Phase-1 Explore, Phase-2 tentative-decision validation (override-capable), Phase-3 Review, the bounded-file-list HARD-STOP, the compliant plan-file header, and the `ExitPlanMode` approval all happen **natively** — nothing hand-rolled. (This is a deliberate, user-directed exception to the usual "the user invokes Plan Mode" convention — `feedback_plan_mode_is_claude_code_built_in`.)
- **`ExitPlanMode` is the single approval gate.** The loop's only other human touch is `AskUserQuestion` to resolve a genuine taste fork (valve f) before approval — an input pause, not an approval. Everything else — brief, explore, draft, audit, revise — is autonomous. Convergence is NOT approval (`feedback_honor_execution_directive`): the loop never treats its own "0 critical" as the go-ahead, and never auto-chains into `/plan_handoff`.

## Usage

`/plan_drive <part-name> [roadmap-path]` — invoke in a **fresh planning session at xhigh effort** (planning benefits from high reasoning; the executor runs lower-effort per `process_rule_plan_high_execute_lower`). Resolves the Part on the nearest-ancestor `roadmap.md` (same convention as `/plan_part`).

## The flow

1. **Brief** — run [`/plan_part <part>`](plan_part.md). It loads the design surface verbatim and classifies drift. (Roadmap reads are grep-anchored — Part row + sibling rows + *Currently ready*; the Mermaid block is table-redundant, never load it.) **Macro drift → halt valve (a)** (kick to `arch-rework`). Carry micro-drift + unresolved-scope notes forward. If `/plan_part` HARD-STOPs on a **State precondition** (Part not `plan-pending`, name unresolved, an unresolved redirect) the drive ends here — surface plan_part's redirect and stop. This is upstream of the loop, before Plan Mode entry; it is not one of the six valves.
2. **Enter Plan Mode** (immediately after the brief emits). Run Plan Mode's standard phases:
   - **Phase 1 Explore** — prior-art discovery for any NEW types the design introduces. **If Explore finds an existing 2+ subclass family / interface the plan should extend rather than parallel → halt valve (c)** (surface the canonical abstraction before drafting; `feedback_inspect_existing_abstractions_first`).
   - **Phase 2 Plan** — validate tentative design decisions against the codebase (override-capable; treat any `AskUserQuestion` menu-pick as Phase-0 input, not a settled decision).
   - **Phase 3 Review** — read the critical files; reconcile.
   - **Phase 4 draft** — compose the plan file. It **MUST** begin with the header `**Roadmap:** <path> — Part **<ID>**` (drives `/session_end` drift detection). If the file list cannot be bounded ("…and possibly others") → **halt valve (b)**.
3. **Audit** — run [`/plan_check @<plan-file>`](plan_check.md) (memory + ordering hazards + abstraction-fit + test-readiness + DoD/stub/cross-Part-dep completeness), using its conditional lens composition (pure-retirement plans drop `plc-pattern-fit`) and path-passed CONTEXT (agents Read the plan file; no verbatim paste). **If a `plan_check` agent returns INCONCLUSIVE → halt valve (d)** — never read an empty/timed-out agent as "0 critical."
4. **Revise & converge** — apply findings, re-audit. See *Convergence* + *The appetite invariant* below. **Persistent or divergent criticals after the round cap → halt valve (e).** A taste fork that changes the plan's *shape* (not just a tunable parameter) can't be parameterized around → **valve (f)**: resolve it with the user via `AskUserQuestion` before approval, never by guessing.
5. **`ExitPlanMode`** — the single native approval gate. By now every taste fork is resolved (step 4 / valve f) and written into the plan as fact, so it is fully settled. FIRST confirm it satisfies [*Plan-file format*](../skills/_brainstorm_shared/plan_file_format.md) — resolved design as fact, rationale inline, no process-meta or open-decision sections. THEN present it for approval **with the process narrative in the message, not the plan** (what `plan_part` drifted, what `plan_check` flagged each round and how it resolved, the convergence basis).
6. **Hand off** — on approval, offer [`/plan_handoff <plan-file>`](plan_handoff.md) with **the plan file path only**. The plan already carries each decision's *why* inline (per [*Plan-file format*](../skills/_brainstorm_shared/plan_file_format.md)), so the executor's "plan is self-sufficient" stance holds without a separate critique artifact its stance forbids it to read. Offer, never auto-chain.

## The appetite invariant (the core safety rule)

**[Shared]** The full rule — lexical tripwire, always-taste set, errs-toward-taste default — lives in [`_brainstorm_shared/appetite_invariant.md`](../skills/_brainstorm_shared/appetite_invariant.md) (the same source [`/architecture_brainstorm_redteam`](architecture_brainstorm_redteam.md) uses). It is the load-bearing safety rule of this loop.

Application to plan production: a question is **TASTE** — **never resolved by guessing to reach convergence** — when its answer depends on an unstated **appetite** (how much coupling, future-proofing, scope, performance margin, risk, or feel is acceptable), *even if phrased analytically*. An **analytical** question has a single correct answer *given a stated appetite*. Convergence counts rigor-holes ONLY; a taste fork is never a convergence blocker — it's resolved WITH the user via `AskUserQuestion` before approval (valve f), not guessed and not left open in the approved plan.

## Convergence

- **The convergence target counts RIGOR-HOLES only** — critical, addressable findings. Loop step 3 → step 4 until **zero critical rigor-holes** remain.
- **A taste question is NOT a rigor-hole and MUST NOT be guessed to reach convergence.** When forks remain after rigor-holes hit zero, batch them into one `AskUserQuestion` (max 4; chain if more) BEFORE `ExitPlanMode` and write the picks in as fact — the approved plan carries zero open user-decisions (an unready plan must not be presented for approval).
- **Auto-apply is intent-preserving ONLY.** Auto-apply a revision only when it cannot change design intent — adding a named failing test the plan already implies, fixing a stale file ref, correcting a namespace/path, formatting. **ANY** revision that changes a type / signature / default / export name-or-shape / scope, or merges/splits a design element, is taste-adjacent → batch for explicit approval, never auto-apply. When unsure whether a revision preserves intent, do not auto-apply (`feedback_default_adoption_lies_about_state`).
- **Round cap = 2.** Distinguish a **persistent** critical (survives a revision) from a **new** critical (a revision introduced it). A *new* critical in round 2 means the loop is **diverging** → halt immediately (valve e); don't spend the "last" round. Persistent criticals after 2 rounds → halt (valve e). **Tie-breaker:** when a round-2 critical's identity is ambiguous (reworded but addressing the same rigor-hole), treat it as **persistent** (halt e) — err toward the halt, mirroring the appetite invariant's errs-toward-taste conservatism.
- **Liveness precondition (closes the false-absence path).** "Zero critical rigor-holes" counts only when each `/plan_check` agent **corroborated** its clean result — echoed *what it examined* (symbol-refs / memory-hits count, abstractions checked). An agent returning "0 critical" with no liveness signal is the `gotcha_workflow_fanout_search_false_absence` class — a silent read-failure reads identical to a genuine clean → route to valve (d), never count it as convergence.
- **Round-2 re-dispatch is conditional.** If every round-1 revision was itself derived from a first-party code read (the mechanism was verified in source before the plan text changed), a round-2 re-audit re-verifies facts that already have primary evidence — skip the dispatch and record the basis in the Decision record. Re-dispatch only when revisions rest on agent testimony or untested inference, as ONE focused fresh verifier (independent dispatch per `red_team_must_be_independent_dispatch`) under `/plan_check`'s evidence-quoting rule.
- **Refuting empirical claims are verified first-party before they count.** A verifier that *refutes* the plan's factual basis ("already implemented / file missing / already refactored") must quote raw tool output, and the orchestrator Greps/Reads at least one quote directly — verification agents fabricate confident file-state reads (`feedback_verify_explore_agent_empirical_claims`; one fabricated an entire "surgery already applied" read on `/part_drive`'s maiden run). A fabricated refutation is discarded and recorded; never re-dispatch the same agent, and never spend a convergence round on it.
- **`/plan_check` SKIP substitution.** On a legitimate SKIP (≤2 files / no new types / no deletions) no audit runs, so "zero criticals" is vacuously true. Convergence then REQUIRES a completed Plan Mode **Phase-2 validation** pass (the validated tentative-decision list is the artifact), and the `ExitPlanMode` presentation MUST disclose *"plan_check SKIPPED (sub-litmus); convergence basis = Phase-2 validation."* A SKIP never satisfies convergence on its own.

## The six halt valves

The only points the loop pauses for the user. Each fires on information the loop can't resolve alone — surface it plainly; (a)–(e) halt the drive, (f) resolves via `AskUserQuestion` and continues.

| Valve | Trigger | Action |
|---|---|---|
| **(a) Macro drift** | `/plan_part` finds a load-bearing design premise stale (base type gone, boundary redrawn, anchor unresolved). | HALT → propose `/update_roadmap transition <part> to arch-rework` with a Trigger naming the drift. |
| **(b) Unbounded scope** | Plan Mode can't bound the file list ("…and possibly others"). | HALT → split the Part or kick to `arch-rework`. |
| **(c) Prior-art conflict** | Explore finds an existing 2+ subclass family / interface the plan should extend, not parallel. | HALT *before* drafting → surface the canonical abstraction; the design may need rework. |
| **(d) Audit inconclusive / false-absent / fabricated** | A `/plan_check` agent returns no verdict (timeout / empty), OR returns "0 critical" with no corroborating liveness signal (`gotcha_workflow_fanout_search_false_absence`), OR its refuting empirical claims fail first-party verification (fabricated read). | Require each agent to echo *what it examined* before accepting its clean. Re-run `/plan_check` once (fresh agents — never the fabricator); if still uncorroborated, surface — never auto-advance on an unverified "0 critical." |
| **(e) Critical not converging** | Criticals persist after 2 revise rounds, OR a revision introduces a new critical (divergence). | HALT with the plan at its last-revised state + the full critique trail + a recommendation: address the criticals manually, or run `/architecture_brainstorm_redteam` if they indicate an architectural dead-end. |
| **(f) Load-bearing taste fork** | A taste question (per the appetite invariant) whose answer changes the plan's *shape*. | Can't self-resolve — resolve with the user via `AskUserQuestion` (batch all forks into one call) BEFORE `ExitPlanMode`; write the pick in as fact. Never guess; never leave it open in the approved plan. |

## Autonomy discipline

- **Don't re-ask mid-stream.** Once invoked, run to a halt valve or to `ExitPlanMode`; don't ask "continue?" (`feedback_honor_execution_directive`). The valves are the only legitimate pauses.
- **Resolve analytical plan questions at plan-time** (`feedback_resolve_questions_in_plan_not_execution`) — but resolve taste questions with the user via `AskUserQuestion` before approval; never guess one to hit convergence.
- **Don't compress Plan Mode's phases because the brief is rich** (`feedback_dont_compress_socratic_on_rich_prompt`). The `/plan_part` brief is starter material; Phase-1 Explore and Phase-2 validation are not redundant with it and are not optional.
- **Don't skip `/plan_check` SKIP-litmus awareness.** If the Part is genuinely ≤2 files / no new types / no deletions, `/plan_check` legitimately SKIPs — then "0 critical" is trivially true. In that case the convergence signal comes from Plan Mode's own Phase-2 validation, not from a SKIPped audit; say so rather than claiming a clean audit ran.

## Constraints

- **Write surface = the plan file only.** The loop's only writes are intent-preserving auto-applies to the plan file (per *Convergence*) plus the step-5 inline-*why* pass (per [*Plan-file format*](../skills/_brainstorm_shared/plan_file_format.md)). **No production code, no tests, no roadmap edits before `ExitPlanMode` approval** — those belong to `/plan_handoff`.
- **No worklog adds during the loop.** Deferrals surfaced mid-drive go into the plan's Decision record or the taste-batch, not the worklog (a post-approval action).
- **Cloud-compatible.** Inherits `/plan_part` + `/plan_check`'s LSP→Grep fallback; no Godot / Obsidian MCP dependency.
- **Context-exhaustion is a split signal, not a valve.** A `plan-pending` Part large enough to exhaust context mid-convergence should have been split at arch Step 5 or caught by valve (b). If the loop can't fit, halt and split the Part — never truncate a half-converged plan.

## Anti-patterns

| Rationalization | Reality |
|---|---|
| "The brief is detailed — skip Explore/Phase-2 and draft straight from it." | The brief verifies the design's *existing* claims; it doesn't discover alternative shapes or validate tentative picks (which get overridden in practice). Run both. |
| "This question looks analytical, I'll resolve it to converge." | If its answer depends on coupling/scope/risk/feel appetite, it's taste — never guess it to converge. It doesn't block convergence (not a rigor-hole); resolve it with the user via `AskUserQuestion` before approval, never leave it open in the plan. |
| "This non-critical fix is clearly beneficial, I'll just apply it." | Only if it can't change intent. Narrowing a type, merging two deliberately-split test cases, renaming an export — all change intent → batch, never auto-apply. |
| "Criticals remain but I'm out of rounds — present the plan anyway." | Approval-ready and known-critical are mutually exclusive. Halt (valve e); never present a known-critical plan for approval. |
| "Convergence reached — hand straight to `/plan_handoff`." | Convergence ≠ approval. `ExitPlanMode` is the gate; only after the user approves do you offer the executor. |
| "Run the brief→draft→audit cycle as a `Workflow` to parallelize." | It's serial-stateful (each step depends on the last). Only `/plan_check`'s internal audit fans out. |
