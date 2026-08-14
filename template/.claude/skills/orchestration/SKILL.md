---
name: Orchestration
description: >-
  Always auto-load BEFORE any long-horizon work, where work should be delegated to subagents or Workflows: choosing the
  dispatch mechanism (single Agent, parallel dispatch, or a Workflow script), fanning
  out reviews/audits/fixes, splitting a large task into delegated chunks, pinning
  model + effort per stage, or authoring a Workflow. Sequential stage dependencies
  are an orchestration shape (pipeline), not a reason to skip. SKIP only for inline
  work — small edits and quick lookups where round-trip overhead exceeds the work.
---

# Orchestration

This skill is the *mechanism layer* for delegating work: which dispatch mechanism fits the task shape, how to fan out, and how to pin model + effort per stage. The *judgment layer* — tier ladder, delegation grain, spec discipline — lives in CLAUDE.md §Model Delegation and is not duplicated here. The *spawn rules* live in `.claude/commands/agents/review_agents.md` (MANDATORY / PARALLEL / NO POLLING / NO TODOWRITE rules) — this skill tells you **when** to apply them and **which mechanism** to fan out with.

## 0. Dispatch Shape — decide this FIRST

Three mechanisms dispatch subagent work. Pick by task shape before anything else:

- **Single `Agent` call** — one coherent, bounded work-chunk (a plan-slice, one TDD cycle, one survey). No orchestration overhead. Use `subagent_type: "fork"` when the chunk needs the session's conversation context — forks inherit it in full; every other mechanism starts cold and gets only what the prompt carries.
- **Manual parallel `Agent` dispatch** (§3 below) — **legacy fallback only** (Workflow tool unavailable). A fan-out you discovered at runtime is still a Workflow: once the jobs are enumerable, pass them to the generic engines — `.claude/workflows/dispatch.js` (general, strict pins, prompts as file paths) or `review_fanout.js` (read-only review lenses, FINDINGS schema) — which give per-call `model`+`effort` pins, PINS logging for `/orchestration_metrics`, and schema enforcement at zero authoring cost. The `Agent` tool inherits session effort invisibly and records no per-agent usage; its only sanctioned uses are `fork` and the single exploratory dispatch below.
- **Dynamic Workflow** (`Workflow` tool, scripts in `.claude/workflows/*.js`) — fan-out whose shape is **known up front** and **deterministic**: a fixed or derivable item set, a barrier/pipeline, a verify/adjudicate stage, a scripted merge/score/assert. Buys you: schema-enforced structured returns, per-call `model` + `effort` pins, resume/caching, loops and budget guards in code that run identically every run. Authoring guidance: §9.
- **Sidecar script** (`.claude/scripts/deepseek_sidecar.sh`) — the only mechanism that reaches the deepseek transport from an Anthropic session; Workflow/Agent cannot. Bash-invoked, one job per call (a fan-out means N calls, each with its own `-R` record + `-l` label), recipe in §5.

**Workflow opt-in gate (amended by user directive 2026-07-28):** Workflow as a *pinned-dispatch wrapper* — replacing what would otherwise be `Agent` calls at comparable agent count, including one-off fan-outs through the generic engines — is **standing-authorized**; no per-use ask. The gate still governs *scale*: an orchestration meaningfully larger than the task's natural shape (speculative extra stages, agent counts beyond the enumerated jobs) needs the user's words, ultracode, or a command whose instructions invoke it. For a recurring orchestration shape, still encode it as a command that invokes `Workflow({scriptPath})`.

**Litmus (fan-out):** *Can I enumerate the jobs right now?* Yes → Workflow (bespoke script, or the generic engines for ad-hoc shapes). No → ONE exploratory `Agent` to discover the item set, then Workflow over what it found. "No need for a workflow file for this" is a named rationalization — the generic engines exist so the pinned route costs no authoring. And when a command's prose is shouting anti-drift warnings at itself (*"MANDATORY: spawn exactly N in one message"*, *"do NOT run_in_background"*), that prose is a **drift fossil** — the determinism belongs in a Workflow script (exemplar: `/plan_check`, converted 2026-07-28).

**Fixed panels are floors, not ceilings.** When a command prescribes a fixed agent set (redteam lenses, audit axes, review checklists), that set is the guaranteed-minimum coverage — never dropped, because a free-form dispatcher reliably skips the dimension it's blind to. But extend it with bespoke agents/lenses when the task's risk profile warrants (and the command's contract allows): each bespoke mandate must name the concrete failure mode it hunts — an unfalsifiable "review holistically" agent is noise generation, not coverage. Which agents to add is judgment (`feedback_prescribe_verification_not_cognition`); that the floor runs, independently and with liveness checked, is contract. Exemplar: `/architecture_brainstorm_redteam` Step 0.5 (conditional + bespoke lenses, ≤8–9 total, optional completeness critic).

Everything below (independence, the cap, worktree races, model selection) governs **both** mechanisms unless noted. Two rules are mechanism-specific:
- **Concurrency:** a Workflow auto-caps at `min(16, cores−2)` and **queues** overflow — pass all items, no manual batch math. The §6 15-cap + nested arithmetic applies to **manual `Agent` dispatch only**.
- **Single-flight under concurrency:** Workflow `parallel()`/`pipeline()` agents must NOT each run GdUnit4 tests (named-pipe wedge) or fan out csharp-ls LSP calls (single-flight wrapper). Pre-compute the symbol map orchestrator-side and pass it in; run the test gate once, serially, outside the barrier. Contention is also why fan-out review lenses are read-only — read that as forced, never as debt to fix, and relocate a verdict's execution to the serial stage instead of arming the lanes. See `gotcha_workflow_single_flight_concurrency.md`.
  **This is a concurrency rule, not a blanket one.** A *single-agent* wave has nothing to contend with: grant it `dotnet build` and narrowly-filtered suite runs so its TDD RED claims are observed rather than reasoned. Keep the full gate in the orchestrator regardless — it exceeds the 5-minute subagent cache TTL, where a filtered run does not. Price the grant: build+test access roughly tripled turn count (188 vs a 56 authoring-only average) — worth it for a multi-slice semantic change, waste on a mechanical refactor. Mandate TRX-counter evidence (`total > 0 AND failed >= 1`), never an exit code — a zero-match filter also exits 1 (`gotcha_zero_match_filter_exit1_mimics_red.md`).

---

## Pre-Dispatch Checklist (All Parallelizations)

Before fanning out:

- [ ] **Read the budget posture BEFORE picking models — this gates provider choice, so it comes first.** Find the most recent `[budget-posture]` line in the conversation (emitted by `budget_posture.py` on `UserPromptSubmit`). If none is present, read the gauge directly: `<TEMP>/cc-cachestat-<session_id>.json` carries `rate_limits.seven_day.used_percentage` and `resets_at`. **Never dispatch a fan-out without knowing the band** — an unknown band silently defaults every lens to Anthropic plan quota, which is the expensive failure and the one nothing downstream detects. Bands in §5.
- [ ] **Check for shared file writes:** Will any two agents edit the same file (`.cs`, `.tscn`, `.tres`)? If yes → either serialize those agents OR partition the file's edits explicitly.
- [ ] **Estimate concurrency:** Flat dispatch ≤15 agents (cap from `pr_pipeline.md`, *Concurrency limit* rule). Nested dispatch (each agent spawns subagents) requires `floor(15 / subagents-per-agent)` outer agents.
- [ ] **Confirm worktree isolation isn't needed:** Parallel agents share the SAME working tree by default. Write-parallel work over overlapping files needs `isolation: "worktree"` per agent (§7).

---

## 1. When to Use Parallel Dispatch

**Trigger phrases:** "fan out", "in parallel", "batch X", "audit across N files", "fix all failing tests", "review all open PRs"

Use parallel dispatch when:

- [ ] **3+ independent investigations:** Multiple unrelated test failures, each with its own root cause. Each agent gets one failure.
- [ ] **Multiple subsystems broken independently:** A merge brought in changes to AI + VFX + Spell Architecture; each subsystem can be audited in isolation.
- [ ] **Batch PR/audit operations:** `/pr_pipeline` reviewing N PRs; `/session_audit` running 3 orthogonal axes.
- [ ] **Cross-domain audits:** A check that walks the entire codebase but each agent owns a different domain (AI, Combat, UI, Inventory).
- [ ] **Per-test-category fix-ups:** N independent Logic-domain unit-test failures with no shared root cause.
- [ ] **`/test_skill` adversarial prompts:** each scenario tests one rationalization in isolation — flat fan-out, dispatched through the `test-skill-pressure` Workflow (§0 Workflow path).

**Why a separate agent rather than another pass yourself:** whoever built a thing is the worst judge of it — knowing what it was meant to be, you read the intention instead of the result. That gap is the entire return on an independent lens, and it holds even when the builder could run the same checklist perfectly. It also sets what a finding should name: the rule that produced the fault, not the one instance of it.

**{{PROJECT_NAME}} exemplars** (read these to see the pattern in action):
- `/pr_pipeline` (*Concurrency limit* + batch protocol sections) — N review agents launched in parallel batch, 15-cap exemplar.
- `/session_audit` Phase 2 — 3 orthogonal axes (design-semantics / robustness-performance / intuitiveness-testability) dispatched through the `review_fanout.js` Workflow engine; the canonical §0 "known-up-front fan-out → Workflow" exemplar.

---

## 2. When NOT to Parallelize

**Skip phrases:** "related failures", "investigation", "I'm not sure what's wrong yet"

Do NOT parallelize when:

- [ ] **Related failures** → "if all 5 test failures cascade from one `Wizard.cs` change, parallelize the fix-write step but NOT the root-cause investigation — one investigation, one fix."
- [ ] **Shared file state** → "two agents editing the same `.tscn` file race the file write — Godot's editor scene format is not merge-friendly."
- [ ] **Exploratory debugging** → "early-stage investigation where one agent's finding reframes the next agent's prompt — sequential is faster overall; you don't want to launch 5 agents and discard 4 results."
- [ ] **Need full context** → "the work requires the orchestrator to hold the full picture. Parallel agents cannot share intermediate findings without a slow round-trip back to the orchestrator."
- [ ] **One file, multiple concerns** → "a single bug touching one file. Don't split 'fix syntax + fix logic + add test' into three agents — context fragmentation costs more than time saved."
- [ ] **Aesthetic or tonal consistency** → "N agents each authoring one asset, doc section, or UI panel yield N individually-defensible pieces that don't read as one voice. Fan out the research; converge the authoring in one context."

### Sizing the width

**Width is bounded by integration cost, not by the concurrency cap.** Ask what it costs to merge the lanes' outputs, and size to that:

- **Read-only lanes integrate for free** — the output is a list, and concatenating a list costs nothing. Width is bounded only by spend and the engine cap, so a wide batch is correct here, not an exception (`/pr_pipeline`'s 15 lanes, §1).
- **Write-parallel lanes integrate superlinearly** — every pair can conflict and the orchestrator reconciles all of them. Keep these to a handful, and add critics rather than builders: one strong builder plus running critics beats six builders. Widening a *write* fan-out trades a linear time saving for a quadratic reconciliation cost; widening a *read* fan-out does not.

---

## 3. The Dispatch Procedure

*(Manual `Agent` dispatch. For a deterministic, known-up-front fan-out, encode it as a Workflow instead — §0. The single-message / no-`run_in_background` rules below are what a Workflow's `parallel()` gives you for free.)*

For each parallelizable task:

1. **Identify independent domains.** Write down each agent's exact scope and verify it doesn't depend on another agent's output.
2. **Create focused tasks.** Each task should be self-contained — the agent shouldn't need to ask for context mid-flight.
3. **Choose model per agent** (see Model Selection below).
4. **Dispatch in single message.** Multiple `Task` tool uses in ONE message → parallel execution. **Never split across messages** — that serializes them.
5. **No `run_in_background`.** Per `review_agents.md` *Agent Spawn Rules*, all results return together when the slowest agent finishes; polling is wrong.
6. **Wait for ALL results.** Do not partially proceed — incomplete agents may surface findings that change the integration step.
7. **Integrate results.** Merge findings (deduplicate by `file:line`), reconcile contradictions, present the unified view to the user.
8. **Verify with full test suite** if any fixes were applied. Per-domain agent fixes can interact at integration boundaries.

---

## 4. Agent Prompt Structure

Each parallel-dispatched agent prompt must be:

- [ ] **Focused** — one clear deliverable, not a checklist of unrelated tasks.
- [ ] **Self-contained** — all context the agent needs is INLINE. Do not point at "see the code in the repo" — the agent has tools to read it but minimizing surprises improves accuracy. (`archive_agent_task_gotchas.md`: orchestrator pushes context; agents don't pull.)
- [ ] **Specific output format** — JSON findings array OR a structured table OR a one-line verdict. Free-form prose is hard to integrate.
- [ ] **No coordination implied** — the prompt should not reference other agents (*"the AI agent will handle this"*) since order of completion is non-deterministic.
- [ ] **Foreground verification, one-turn report** — executor briefs that include test/build verification must pin: run verification in the FOREGROUND and deliver the final report in the same turn. An executor that backgrounds its test run and ends its turn "waiting" stalls the pipeline (notification fires, no report; recurred 2× in one session) — the orchestrator ends up nudging or taking verification over.

**Template:**

```
You are <role-description>.

CONTEXT:
<inline file contents, schemas, conventions — everything the agent needs>

TASK:
<single specific deliverable>

OUTPUT:
<exact format expected, e.g., "JSON array per orchestrator_action_protocol.md schema">

CONSTRAINTS:
<any hard rules, e.g., "do not modify files", "limit to <scope>">
```

**Cross-reference:** `feedback_inspect_existing_abstractions_first.md` — when scoping per-agent task boundaries, extending an existing 2+ subclass family beats inventing parallel work. The same logic applies to agent task scoping.

---

## 5. Model & Effort Selection

Model *attributes* (which model fills which role, intel/speed/cost/taste) live in ONE place: the CLAUDE.md §Model Delegation table. This section owns the *dispatch rules*, stated in that table's `role` names — look up the role → model mapping at dispatch time. Evidence base: `archive_agent_task_gotchas.md`.

### The load-bearing rule: a fan-out NEVER inherits the session model

Omitting `model` does NOT pick a cheap model — it inherits the **session model**, which is whatever the orchestrator is running under. Under the orchestrator tier that is the most expensive option, and a fan-out *multiplies* it: a 6-lens red-team panel with no pins becomes 6 orchestrator-tier critics — overkill the work rarely justifies. **Every dispatched agent pins a model explicitly. No exceptions outside the carve-out below.** "I'll just let it inherit" is the bug, not a shortcut.

**Engine floor (defense-in-depth).** `review_fanout.js` and `doc_architecture_audit.js` default an omitted/mis-spelled model to a hardcoded floor (set in those scripts — keep it aligned with the table's default-fan-out row). The floor is a safety net for a forgotten pin — it is NOT a license to stop pinning: a pin at the call site documents *why* a lens runs where it does, and manual `Agent`/`Task` dispatches (e.g. `pr_ready`'s `parity` lens, the red-team `Task` fallback) bypass the floor entirely and MUST pin. The engine floor encodes the *default-fan-out* row only — it cannot tell that a lens is reasoning-heavy, so an unpinned red-team or design-judgment lens silently lands a tier below its actual floor.

### Decision tree (roles per the CLAUDE.md table)

**Two floors — pick by lens shape.** Litmus: *would a wrong answer be caught by re-reading the input, or only by out-reasoning it?* Re-reading → default fan-out. Out-reasoning → executor.

- [ ] **Executor tier** — **THE FLOOR FOR REASONING-HEAVY LENSES.** Adversarial-design critique (red-team), architectural analysis, design-semantics (`session_audit`), domain-coherence/reference-integrity (`structure_audit`), refactor-parity gating (`pr_ready`), fix authorship — anywhere a miss ships a defect rather than a re-prompt. Per the table this tier matches the orchestrator on intelligence at better cost and speed, so there is no fidelity argument for going higher. **Architecture authoring splits by altitude:** scoped/lower-level → this tier; cross-domain/large-scope → orchestrator tier (un-scopable, so it can't be delegated). Capability detail lives in the CLAUDE.md table `±` column.
- [ ] **Default fan-out tier** — **THE FLOOR FOR READ-HEAVY AND MECHANICAL LENSES.** Surveys, call-site/reference enumeration, text-comparison audits, checklist passes against an explicit rubric, schema extraction, state reconciliation. The table marks this tier down on architecture and complex agentic coding — never staff a design-judgment lens here to save cost.
- [ ] **Validation tier** — Verify a PASS verdict, re-check a finding, mechanical lookups/data-extraction where a wrong answer is cheap to reject and re-prompt. Per the table this currently maps to the same model as the default fan-out tier — the savings lever is a reduced effort pin (see Effort selection below), not a cheaper model.
- [ ] **Orchestrator tier** — **Never a delegate target.** Its differentiating column is *delegation*, and a delegate does not delegate: dispatching one pays orchestrator cost for executor-shaped work at zero intelligence gain. Reserve for explicit user request; never a default, never inherited, never a unilateral pick for a routine fan-out.
- [ ] **Escalation above a lens's floor is per-lens and deliberate** — raise a *specific* lens when its input is genuinely heavier than the panel's (a multi-subsystem boundary redesign for an architecture lens), never a blanket bump of the whole panel.
- [ ] **`Explore`** — **WARNING:** the built-in `Explore` agent runs on Haiku (harness fact, not a table choice) and has hallucinated paths (`mooyum_milk.tres`, Phase 1e.2). Use only for scoped lookups where a wrong answer is cheap to reject. Most {{PROJECT_NAME}} commands use `general-purpose` + an explicit model pin instead.

### The one legitimate inherit-the-session-model carve-out

**Measurement batteries that test the session model's own behavior** — `routing_battery.md` (tool-routing decisions) and `doc_workflow_battery.md` (skill-trigger behavior) — *intentionally* omit `model` so the subagents run under the session model being measured. Pinning these would defeat their purpose. This is the ONLY sanctioned inheritance; both commands say so inline. Do not "fix" them in a model-selection sweep. The same carve-out covers effort: a battery agent's effort stays inherited too.

### Effort selection (Workflow `agent()` only)

Effort (`'low' | 'medium' | 'high' | 'xhigh' | 'max'`) is a second, orthogonal knob: `model` picks the brain, `effort` picks how long it deliberates. Surface availability:

- **Workflow `agent()`** — per-call `opts.effort`. The only per-call surface.
- **`Agent` tool** — NO effort parameter. Effort is only pinnable via a custom agent definition (`.claude/agents/*.md` frontmatter); none exist in this harness, so manual dispatches always inherit session effort — which is why fan-outs never run on the `Agent` tool (§0): inherited `high`/`xhigh` multiplies the costliest pin across a panel for measured-zero gain.

**Two-class effort rule for workflow scripts (user directive 2026-07-28).** Stages whose ambiguity is bounded by construction (judges, verifiers, extraction, checklist lenses) HARD-SET effort in the script — the thinking happened once, at instrument-authoring time, with evidence (measured, J-CAL 2026-07-28: judge panels at `opus` `low`/`medium`/`high` differ by ≤3 of 56 items and degradation is misses-only, never false credit — `medium` stands as default, `opus·low` is the sanctioned economy panel, `sonnet·medium` is disqualified as a judge). Stages whose residual ambiguity varies per invocation (arms, executors, authors) take a script default plus an `args`-level override that MUST carry a named justification — the engines log it beside the PINS line, and `/orchestration_metrics` audits whether it paid.

Rules:

- [ ] **Pin by residual ambiguity at dispatch — not task size, difficulty, or importance.** Effort buys more *steps*, not deeper ones: measured across 21 agents, output-per-turn is flat from `medium` to `high` (445 → 465) while turn count rises ~1.8×. So a raise pays only where something remains to discover. If you have already resolved every open decision in the spec, drop to `medium` however important the chunk is. Corollary: an API-shape or semantics decision you *could* have settled yourself costs ~2.3× when pushed into an agent instead — and is the most common source of a `wasted` verdict.
- [ ] **Know which rung is expensive.** Normalized cost per agent: `low` 1.0×, `medium` 1.2×, `high` 2.8×. Output tokens are only 5–9% of that — the bill is cache-read, i.e. turns × context. So `low` vs `medium` is not worth deliberating (`low` takes the same turns and merely thinks less per step); **`medium` vs `high` is the only effort decision that materially costs anything.** Default `medium`; earn `high` by naming the ambiguity it will resolve. Measure with `/orchestration_metrics`.
- [ ] **A sharp mandate substitutes for effort — on sub-architectural inputs.** Both `medium` plan-check lenses in that run each found a blocker, matching the `high` lenses at 43% of the cost — the lens's named failure mode did the work. Tightening a prompt reduces turns as effectively as lowering the pin, and costs nothing in quality. **Scope caveat (P-D pin comparison, benchmark 2026-07-29):** the parity does NOT hold on architecturally-loaded plans — there `opus·high` lenses found 2.5× the unique valid defects of `sonnet·medium` (15 vs 6, incl. the two highest-value findings). The mandate substitutes for effort only while the input's ambiguity is below the architecturally-loaded line.
- [ ] **Judgment stages pin the dispatched model's `effort` column value** (CLAUDE.md table — its peak quality-per-token level). Omitting inherits session effort — acceptable when the two coincide, but the pin is what keeps a stage's cost/quality independent of session settings.
- [ ] **Drop below the column value by stage shape, not by "it's mechanical":** `'low'` fits stages with an airtight spec and no open judgment — pure transcription, extraction against an exact schema, format conversion. Mechanical work that can hit slightly unexpected input needing a judgment call (validators, dispatch against real-world content) runs `'medium'`. Unsure between the two → `'medium'`. **Measured counterexample worth knowing:** `opus`+`low` scored top marks on an *open discovery* task — matching `opus`+`medium` and `sonnet`+`high` at roughly half the tokens — so this bullet's airtight-spec restriction is untested rather than established for that row.
- [ ] **Never characterize a tier from one observation.** In the same measured run, `sonnet`+`low` scored 7/7 on one task and produced one citation on another, and `sonnet`+`low` beat `sonnet`+`medium` — effort is non-monotonic at n=1. A single agent's output tells you about that agent, not its tier. Tier claims need the `/eval_dashboard` floor (≥8 agents, ≥3 sessions).
- [ ] **Effort tiers are row-relative, not absolute.** The same enum value buys more deliberation on a higher-intel row, so the boundaries above are defaults, not physics: a row whose effort cell explicitly claims `'low'` for defined workhorse stages has already moved that boundary — honor the cell over the default. Where a cell does, routing a fully-specified workhorse stage *up a row and down an effort tier* can beat keeping it on the lower row at raised effort; the CLAUDE.md `±` column records which rows this holds for.
- [ ] **Above the column value is per-stage and on-demand** — reserve for the hardest verify/judge stage when a miss is costly, mirroring the per-lens escalation rule above. Never a blanket bump. **Check for a pre-encoded split first:** a role's effort cell in the CLAUDE.md table may already carry a multi-value stage-shape split (a higher value for its hardest authorship shape, a lower one for routine execution, lower still for fully-specified workhorse stages) — read the cell before assuming a single flat value applies. **`'max'` is banned on Anthropic pins** — token cost far exceeds the marginal improvement; `'xhigh'` is the ceiling. (Off-Anthropic this does not apply: the sidecar's requested-`max` is a vendor coordinate, not this scale — see the dispatch bullet below.)

**Budget-pressure bands — reading the band is MANDATORY (Pre-Dispatch Checklist); what it recommends is advisory.** A `[budget-posture]` hook line, when present, may widen what routes to the sidecar as weekly budget pressure rises: `pressure = used% / pace%` per window, computed from `resets_at` (never hardcoded). `seven_day` governs **provider choice** (plan-quota tier vs the paid sidecar); `five_hour` governs **fan-out width**. Bands (thresholds' SSOT: `.claude/hooks/budget_posture.py`): <0.85 Surplus — zero sidecar spend, the plan-quota budget is already paid for (CLAUDE.md §9's synthesis-bundling rule still binds; the executor becomes a subagent, task-sized per §9 *Offline fallback*); 0.85–1.15 On pace — sidecar takes Explore/read-synthesis/doc write-ups; 1.15–1.5 Ahead — + converged-spec execution and low-stakes adversarial-review lenses; >1.5 Hot — + deeper exploration and architectural review passes. **Pressure never delegates the reserved floor:** orchestration, gate decisions, cross-system seams — and the ideal-design VERDICT. Bands widen the delegatable set; they never shrink the reserved one, with the single enforced exception below.

**The authoring/verdict split (user directive 2026-08-12).** Pressure may delegate large-scope architecture *authoring* to a strong external model; it never delegates the *verdict on that architecture*. From an Anthropic session an external model is a delegate whose output comes home for judgment — **it authors; the verdict comes home.** In an external-model-led session that model IS the orchestrator (the existing session carve-out), and gate decisions plus the ideal-design verdict still warrant an Anthropic session OR explicit user sign-off.

**Bands are advisory for tier and width, ENFORCED in exactly one place: agent-initiated SIDECAR dispatch of a `gated` model.** This is the registry's authorization policy, and this paragraph is its prose home — `.claude/reference/external_models.json` holds only the values (`authTier`, `gate.minBand`, `gate.minBalanceUSD`), because a normative rule whose only home is a data file is invisible to a doctrine reader (`instruction_quality` §3).
- **Every** model's `gate.minBand` is evaluated, both tiers — the band read is local and free, so gating it behind `authTier` would leave the cheap tier's floor authored and never read. `deepseek-v4-flash` carries `minBand: On pace` precisely because sidecar spend is forbidden in Surplus.
- `authTier: gated` adds a pre-dispatch **balance floor** (one HTTP probe). `-A` overrides the band gate only; it never overrides the balance floor, and an unreachable/unparsable balance endpoint is itself a refusal (exit 6) rather than a pass.
- Exit codes: **5** band refusal, **6** balance floor or probe failure, **2** unresolvable model or unusable registry (the sidecar fails CLOSED — it never falls back to a hardcoded id, because dispatching at a tier nobody chose costs money).
- **A Workflow fan-out inside an external-model-led session is NOT gated.** A PreToolUse hook cannot deny one agent without killing the whole dispatch, and launching that session was itself the authorization. What replaces enforcement there is *informed choice at SessionStart*: `session_model_rails.py` states the session's own tier, its role map, its prices, and the routing rule — pin the expensive tier ONLY for large-scope architecting and complex cross-domain work; everything else pins the cheap role. Reading the band still precedes any fan-out (Pre-Dispatch Checklist).
- **Band names rank by BURN RATE, not headroom.** `Surplus` is the LOW-pressure band and means plan quota is going unused — spend it first. `Ahead`/`Hot` are HIGH-pressure and are when paid transport becomes the cheaper currency. A refusal message quoting only the band name reads backwards, so the sidecar prints the pressure number beside it (`budget_posture.py --band --pressure`).

**Dispatching to the sidecar:** `.claude/scripts/deepseek_sidecar.sh` (Bash, backgroundable; full flag reference in its header). Minimum well-formed dispatch: `bash .claude/scripts/deepseek_sidecar.sh -m flash -e max -f <prompt-file> -S <schema.json> -R <record.json> -l "<label>" -d <repo-root>` — `-R` writes the provenance record + spend-ledger line, and `-l` is what makes the run visible to `/orchestration_metrics` (unlabeled runs are skipped by default). Effort doctrine per the CLAUDE.md deepseek row: requested-`low` converged, requested-`max` open; requested-`high` is high-variance and unverified — prefer the two measured rungs.

**Context disclosure per dispatch:** `-D bare|pointer|full` (default `full`; unknown value exits 2) sets how much harness context the child loads — `bare`: scratch run-cwd, vendor+CLI+user config only (repo Read kept via the grant; measured 36.4K fresh input tokens ≈ $0.0051/flash run vs 56.8K ≈ $0.0080 for `full`); `pointer`: bare + MEMORY.md appended (42.8K ≈ $0.0060). `-C <file>` (repeatable) appends extra context on any tier. Select by self-containedness, not importance: default `full`; `bare` when the prompt file already carries everything the job needs; `pointer` when the gotcha index pays for itself. Measured counts carry ±9% run-to-run variance.

**`-m` names the tier, and naming it is the point.** `-m flash|pro` (aliases resolved from `.claude/reference/external_models.json`; full ids accepted). Default is `flash`. **Pass `-m` explicitly on every real dispatch** even when you want the default — an inherited default is a tier nobody stated, and the ambiguity costs money in exactly one direction. `pro` is `gated`: expect exit 5 below its band floor (`-A` overrides) and exit 6 below its balance floor (`-A` does not). Reach for `pro` only where the expensive tier earns it — large-scope architecting, complex cross-domain work — and prefer **few long agents to many short ones**: each dispatch pays a ~56K-token cold-start toll at ~3.1× the cheap tier's fresh rate, so a wide-and-short pro fan-out is the worst shape available. Its quality is UNMEASURED (`reference/model_ladder_evidence.md`); only its price is known.

From a deepseek *session*, Workflow pins are auto-translated instead by `hooks/model_pin_translate.py`, per-role — don't hand-rewrite model names in scripts. (Agent-tool pins are stripped, not translated: bare role names hard-error on that endpoint.)

**Two dispatch args beyond the pins.** `j.shape` (`any` | `survey` | `review` | `author`) selects which rail family from `.claude/guards/` the engine injects — omit it and the job gets `any`; the receiving model's tier decides how verbosely the rails are spelled out. `args.spillDir` bounds the RETURN path: each agent writes its full deliverable to `<spillDir>/<label>.md` and returns a ≤200-word digest plus `FULL: <path>` (`args.spillDigestWords` overrides the cap). Default it ON whenever per-agent output is report- or prose-shaped — unbounded prose returns are the dominant driver of orchestrator context growth, and growth is what forces lossy compaction. Omit it only when you need every word in-context. `explore_fanout.js` honors the same arg as spill-BEFORE-validate: the lens writes its complete deliverable to `<spillDir>/<label>.md` before structured-output validation, so a schema rejection is recoverable via `/salvage_fanout`.

**Every NEW workflow script carries the `PIN()`/`EFF()` preamble.** Three lines, above the first `agent()` call, with every dispatch reading `model: PIN(j.model), effort: EFF(j.effort)`:

```js
const A = (typeof args === 'string') ? JSON.parse(args) : (args ?? {})
const PIN = (m) => (A.__pin && A.__pin.roles && A.__pin.roles[m]) || (A.__pin && A.__pin.model) || m
const EFF = (e) => (A.__pin && A.__pin.effort && A.__pin.effort[e]) || e
```

**`PIN` must be ROLE-AWARE — read `__pin.roles[m]` first.** The older scalar form `(A.__pin && A.__pin.model) || m` discarded its own argument and resolved every role to one id, so whichever id it held, one tier was always wrong: the cheap id silently downgraded every `opus` lens, the expensive one tripled the bill on every `sonnet` lens. `__pin.model` survives only as a fallback for a resolver not yet updated. Copy the preamble from `dispatch.js`, which is authoritative.

`hooks/model_pin_translate.py` injects `args.__pin` at dispatch; the resolver is what applies it. The hook cannot reach a `name:`-dispatched script's source, so a script omitting the preamble keeps its Anthropic literals on a DeepSeek endpoint — where a **bare role name HARD-ERRORS** (`agent()` returns null, `.filter(Boolean)` renders it as "0 findings": a fan-out that looks clean and ran nothing), and a full `claude-*` id aliases by tier, unpriced and unstated. On an Anthropic session the preamble is a no-op — pins dispatch claude-* agents there, and wanting the sidecar is a mechanism change (the script), never a pin.

---

## 6. The 15-Agent Concurrency Cap

*(Applies to **manual `Agent` dispatch**. A Workflow auto-caps at `min(16, cores−2)` and queues overflow — you pass all items and skip this arithmetic; see §0.)*

**Source:** `pr_pipeline.md`, *Concurrency limit* rule — *"Concurrency limit: 15 agents max per batch."*

Two calculation modes:

### Flat dispatch (simple)

Each agent is one direct subagent with no nested sub-dispatches.

- **Cap applies directly:** ≤15 prompts per batch.
- **Examples:** per-test-category Logic-domain fix-ups, per-PR review status checks, `pr_ready`'s lens dispatch. (`/test_skill` and `/session_audit` route through Workflow engines — the auto-cap applies instead.)

### Nested dispatch (compound)

Each "outer" agent spawns its own subagents internally.

- **Cap applies to the multiplied total:** `outer × subagents-per-outer ≤ 15`.
- **Example:** `/pr_pipeline` spawns N review agents, each of which spawns 4-7 subagents. With 6 subagents per review on average, the cap allows `floor(15 / 6) = 2` outer reviews per batch. Larger batches must serialize.

**Rule:** Calculate the multiplied total before dispatching. If the total exceeds 15, split into sequential batches. Don't double-count (treating a flat 12-agent dispatch as if it were nested) and don't under-count (treating a 3-outer × 6-inner = 18 dispatch as if 3 ≤ 15).

---

## 7. Worktree Caveat

{{PROJECT_NAME}} frequently runs in `.claude/worktrees/<name>/`. **Parallel agents do NOT get isolated worktrees by default** — they all write to the same working tree.

Implications:

- [ ] If two agents edit the same file, the second write wins (or fails on lock).
- [ ] Agents that modify `.tscn` files MUST be partitioned to non-overlapping scenes.
- [ ] If you need genuine isolation (parallel agents mutating overlapping files), pass `isolation: "worktree"` per agent — each gets its own fresh worktree (auto-removed if unchanged) and they CAN run in parallel. Setup costs ~200–500ms + disk per agent; reserve it for write-parallel work, never read-only fan-outs. Each fresh worktree needs the Jmodot submodule re-init.

**Cross-references:**
- `archive_worktree_session_setup.md` (auto-memory) — worktree-init recipe and submodule gotchas.
- `archive_worktree_submodule_gotcha.md` (auto-memory) — Jmodot submodule needs `git submodule update --init --recursive` after every checkout in a worktree.

---

## 8. Verification After Integration

After all agents return:

1. **Deduplicate findings.** Same `file:line` from different agents → keep the more specific one (or the `critical: true` one). Per `orchestrator_action_protocol.md` Step 1 (Merge & Deduplicate).
2. **Reconcile contradictions.** Two agents may disagree on a finding's category or fix. Surface this as a `## Notes` synthesis (orchestrator-only, per the protocol's Step 3) for the user.
3. **Run the broader test suite** if fixes were applied. `/regression_gate` is the canonical full-suite verification (3-tier: silent-skip sentinel + baseline drift + explicit failures).
4. **Do not claim completion before verification.** Per the protocol's `Claims to Refuse` section — *"should work now"*, *"probably passes"*, *"seems to be fixed"* are all unverified. Cite test output or use future-tense honestly.

---

## 9. Authoring a Workflow on the Fly

For a fan-out that passes the §0 litmus but has no existing script. The tool's own description documents the API (`meta`, `agent()`, `pipeline()`, `parallel()`, `budget`, resume); this section is the project discipline layered on top.

**Structure:**

- [ ] **`Write` the script to a file, then invoke `Workflow({scriptPath})` — put every agent prompt inside the script.** Both memorialized spawn failures (`gotcha_workflow_args_permission_control_chars.md`, `gotcha_workflow_args_generation_fidelity.md`) occur because prompt text transits a JSON tool-call payload; script text loaded from disk never does. Prompt size then stops mattering. Reserve `args` for short scalars, keep the tolerant-parse guard (`args` still arrives as a string), and push bulk CONTEXT to a scratch `.md` the agents `Read` by absolute path.
- [ ] **Emit the effort pins so the run is measurable.** The harness records label and model per agent but not `effort`, and data-driven dispatch (`effort: job.effort`) defeats static parsing. One line makes `/orchestration_metrics` self-resolving: `log('PINS ' + JSON.stringify(Object.fromEntries(JOBS.map(j => [j.label, j.effort]))))`.
- [ ] **Record each dispatch's falsification outcome when you consume its result** (`clean`/`defects`/`rework`/`discarded`) — cost survives compaction, the outcome doesn't. Overshoot is never rated here; it is derived at the aggregate. Rule + ledger format: `/orchestration_metrics` §Incremental rating.
- [ ] **Before pinning, check `.claude/orchestration_candidates.json`** — a listed shape family runs one rung below the table default on its next natural dispatch (substituted downgrade, never an extra dispatch) and the verdicts-file entry carries the `probe` marker. Rule: `/orchestration_metrics` *Over-pin candidates*.
- [ ] **Give every write-shaped schema a `couldNotSatisfy` field.** It is what makes file-set-partitioned parallel authoring safe: agents that hit a boundary report the gap instead of overstepping it or dropping it silently, and the collected gaps become the orchestrator's integration list. Pair it with `redVerification` on TDD stages.
- [ ] **`pipeline()` is the default; a barrier (`parallel()` between stages) needs justification.** A barrier is correct ONLY when stage N needs cross-item context from ALL of stage N−1 (dedup across the full set, early-exit on zero total, "compare against the other findings"). "The stages are conceptually separate" is not a barrier reason.
- [ ] **`schema` on every stage whose output feeds another stage or the final merge.** Schema-enforced returns are half the reason to use a Workflow over manual dispatch; free-form prose between stages forfeits it.
- [ ] **Pin `model` and `effort` per stage** per §5 — the §5 never-inherit rule applies to every `agent()` call, and the engine-floor defense exists only in scripts that implement it (e.g. `review_fanout.js`).
- [ ] **`log()` every silent cap** — top-N, sampling, no-retry. Silent truncation reads as "covered everything."
- [ ] **Loops guard on `budget.total`** — with no token target set, `budget.remaining()` is `Infinity` and an until-dry loop runs to the agent cap.

**Cold-context prompt discipline:** workflow agents never see the session. Push everything in (per §4 self-containment); agents compare, never discover — fanned `Grep`/`Glob` returns intermittent false-empties (`gotcha_workflow_fanout_search_false_absence.md`). Route shared bulk through one `contextPrefix`-style string rather than duplicating it into N prompts: large × nested × escape-dense `args` kills the run at generation time — a 4ms/0-agent death is malformed `args`, not a broken tool (`gotcha_workflow_args_generation_fidelity.md`). Scripts parse-guard `args` (arrives as JSON string on some harness versions, parsed value on others — `reference_workflow_integration_mechanics.md`).

**Mechanics:** `meta` must be a pure literal; `Date.now()`/`Math.random()`/argless `new Date()` throw (pass timestamps via `args`); pass `args` as real JSON values, never a stringified blob. Every invocation persists its script and returns the path — iterate by editing that file and re-invoking with `{scriptPath}`, and resume a paused/edited run with `resumeFromRunId` instead of re-running completed agents.

**Promotion path:** a script worth running twice is worth saving to `.claude/workflows/` behind a command (the command IS the opt-in — §0 gate). Audit any command-invoking-workflow pair against `instruction_quality` §12 (orchestration-contract integrity: invocation shape, output schema, gotcha screening).

---

## 10. Context Checkpointing (long-horizon drives)

Deep context degrades output quality well before hard limits (~500K tokens by user observation, 2026-08-02), and the model CANNOT self-measure its context usage — the user's statusline can. Three rules for any drive/long-horizon session:

- **Checkpoint state to the plan file at every slice/phase boundary** — mandatory regardless of context depth; the plan file (plus Obsidian design docs) is the SSOT that makes any stop cheap.
- **Offer a stopping point — never unilaterally stop.** Ideal compaction band: **35–50% context**. Read the persisted gauge (statusline state file) at slice boundaries; once it enters the band, steer the drive toward a durable stopping point *within* it. Going somewhat over 50% is acceptable when reaching one requires it — a mid-slice compact costs more than the overshoot. "Stopping point" means the ORCHESTRATOR's inline work is at a boundary, not that the session is idle: dispatching the next executor/Workflow and compacting while it runs is the preferred, time-efficient shape (in-flight dispatches survive compaction — choreography below). What must not straddle a compact is unfinished inline reasoning, not delegated work. Also offer when a compaction event fires or the user announces pressure, regardless of the gauge.
- **The boundary mechanism is IN-SESSION COMPACT, not fresh-session handoff** (user directive 2026-08-03): at a stopping point the USER runs `/compact` in the same session — the summary preserves the drive's goal and process while shedding context. Never propose a `/plan_handoff`-to-fresh-session as the default boundary; reserve it for genuine session death. Stopping-point choreography: (1) make state durable (converged plan file, roadmap, decision records, fork answers), (2) **dispatch the next long-running Workflow FIRST** — in-flight workflows survive compaction — launch first, the run proceeds while the session compacts — (3) announce the stopping point EXPLICITLY — say "stopping point reached — safe to compact" and **report the session's context %** so the user can judge: Read `<TEMP>/cc-cachestat-<session_id>.json` (written by `~/.claude/statusline.py`; the `context_pct` field exists precisely because the model cannot self-measure) and quote the number; if the file is absent, say so and ask the user to read their statusline — never guess. Provenance: `feedback_compact_in_session_at_stopping_points.md`.
- **Never claim context pressure the user's meter contradicts.** No self-estimated "context is deep" assertions; cite a signal or say nothing.

## 11. Dispatch Doctrine (efficiency-to-quality)

Moved from CLAUDE.md §Model Delegation (2026-08-07) — the always-loaded surface keeps only the gists plus this pointer. Model specs resolve ONLY from the CLAUDE.md ladder; role names in this section are qualifications.

- **Session-model-relative dispatch — delegate whole work-chunks, then review.** Review depth tracks the intel gap: a *strictly-lower-intel* tier lands a well-scoped unit at ~80–90% (rule of thumb, not a metric) and the orchestrator closes the last 10–20%; an *equal-intel executor* returns correctness-complete work, so the orchestrator reviews for taste, cross-system fit, and harness-principle adherence rather than as a correctness backstop. Either way the orchestrator scopes and reviews — it does not execute. **A converged plan/landing spec IS the dispatch signal:** the moment a spec exists that a lower ladder row could execute, executing it inline from a higher row is the failure — on every execution surface (drive commands, plan handoffs, inline feature work), not any one command. **The signal fires on EFFORT as well as tier:** a converged spec gains nothing from a high-effort session executing it inline — dispatch it at a low-effort pin even when the session model IS the executor tier; the 'authors inline' carve-out is conditional on matched session/dispatch effort (an unknown-or-elevated session-effort rail is exactly the case to dispatch). **Default to dispatching a scoped work-chunk to the tier whose ladder row fits its shape**, rather than doing it inline. Keep at the orchestrator: decisions and direction, cross-system seams, final review/integration. *Small surgical edits and quick lookups stay inline* (round-trip overhead exceeds the work). Payoff: a leaner orchestrator context runs longer before compaction and spends fewer of the costliest tier's tokens. Recurring failure: executing a delegatable chunk inline ("already in context", "faster to just do it") — the delegate at ~80% + your review IS the design; name it and dispatch.
- **Delegation grain — by shape, not difficulty.** Ideal chunk = one coherent work unit with a bounded, enumerable file/output set a delegate finishes in one session: a plan-slice, a single TDD cycle (RED→GREEN→REFACTOR for a component), one subsystem survey, one checklist-guided review pass. Too small (a couple edits, a lookup) → inline; too big (files won't bound, spans subsystems) → decompose and delegate the pieces. Litmus = the plan's bounded-file-list gate: can't enumerate the files → not yet a delegatable unit. Delegate bounded I/O + mechanically-verifiable output, or a scoped chunk with a clear done-condition; keep open-ended/*unscoped* judgment, silent-failure risk, and cross-system seams at the session model.
- **Cache-TTL asymmetry decides where blocking work runs.** The prompt cache is a prefix match; the main conversation holds a 1h TTL (automatic on subscription, else `ENABLE_PROMPT_CACHING_1H`), while **every subagent is pinned to 5m** and builds its own prefix from scratch. Consequences at dispatch time: a call that blocks >5min (test suite, full build) expires the subagent's cache mid-flight and costs a full re-read of its context on return — keep the *blocking run* in the main session and delegate the authoring around it. That is a carve-out for the blocking call only, NOT licence to execute the chunk inline. Never interleave long-blocking calls with edits inside one subagent — batch them; each gap re-reads a context that grew since the last. Prefer `fork` when the chunk needs session context: it inherits the parent's prefix and starts warm, where a fresh subagent starts cold. `isolation: "worktree"` builds its own prefix (cache is scoped per working directory) — reserve it for genuine parallel write conflicts.
- **Dispatch via Workflow; bound the return path.** Every fan-out (≥2 agents) and every judgment/verification stage dispatches via `Workflow` with explicit per-call `model`+`effort` pins — the generic engines (`workflows/dispatch.js`, `review_fanout.js`) make the pinned route zero-authoring, so *"no need for a workflow file here"* is a named rationalization. **Standing authorization:** Workflow as a pinned-dispatch wrapper needs no per-use opt-in — opt-in governs agent *scale*, not mechanism. Bare `Agent` is reserved for `fork` (needs session context) and a single exploratory dispatch whose item set is unknown until it looks; both knowingly forfeit pinned effort (they inherit session effort invisibly and record no per-agent usage) — when effort matters, dispatch a single-job Workflow instead. Every agent's final text lands in orchestrator context *permanently* and that growth is what forces lossy compaction: pass `args.spillDir` whenever per-agent output is report/prose-shaped, omit it when you need every word. Engine choice, delegate rails (`.claude/guards/`), and per-stage effort shaping: §0/§5.
- **The spec is the price.** Spec-writing time at the orchestrator tier converts a cheaper model's output up a tier; a loose spec converts it into rework. A landing spec carries a hand-verified exemplar, explicit per-item deltas, hard invariants, and self-verification ("report what you couldn't satisfy"). Scoped+principled is what makes the executor tier effective too.
- **Author the verification before dispatch, and the first instance yourself.** Data-pin tests written before the agent runs make delegated output cheap to trust and cheap to reject. New .tres/.tscn schema or authoring pattern → one exemplar by hand, then delegate the clones. **Survey specs** carry a question list + word cap + "quote key signatures"; **never burn agent-tier tokens on digest/synthesis work** (worker tier); research is legwork you delegate, not thinking you outsource — `/research` (worker tier) gathers and cites, the orchestrator interprets. **Living table:** `/self_evaluate` flags delegation mismatches; `/autolearn` proposes ladder/grain edits when they recur.

## Cross-references

**Spawn rules (do not duplicate — point to):**
- `.claude/commands/agents/review_agents.md` — Agent Spawn Rules (MANDATORY / PARALLEL / NO POLLING / NO TODOWRITE).

**Canonical {{PROJECT_NAME}} exemplars:**
- `/pr_pipeline` (*Concurrency limit* + batch protocol) — N review agents in parallel; 15-cap exemplar with nested-concurrency math.
- `/session_audit` Phase 2 — 3 axes via the `review_fanout.js` Workflow engine (§0 Workflow-path exemplar).
- `/test_skill` (`.claude/commands/test_skill.md`) — adversarial fan-out via the `test-skill-pressure` Workflow engine.

**auto-memory (cold tier `archive/`):**
- `archive_agent_task_gotchas.md` — orphan Godot processes after parallel dispatch, no-polling pattern, subagent file-read direction (orchestrator pushes, agents don't pull), Haiku Explore hallucinations.
- `archive_worktree_session_setup.md` — worktree initialization recipe.
- `archive_worktree_submodule_gotcha.md` — Jmodot submodule init after worktree checkout.

**File-based memory:**
- `feedback_inspect_existing_abstractions_first.md` — when scoping per-agent task boundaries; extending an existing 2+ subclass family beats inventing parallel work.

**Orchestrator integration:**
- `.claude/commands/agents/orchestrator_action_protocol.md` — Step 1 Merge & Deduplicate, Step 2 Present Unified Report, Step 3 NOTE Synthesis, Step 4 Execute Actions, Claims to Refuse section.

**Skills:**
- [`debugging`](../debugging/SKILL.md) — Phase 2 (Pattern Analysis) cross-codebase work often parallelizes well; decides whether to parallelize investigation.
