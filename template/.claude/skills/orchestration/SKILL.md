---
name: Orchestration
description: >-
  Auto-load when orchestrating — delegating, fanning out, parallelizing, or splitting work across subagents, Workflows or sidecars — and before any long-horizon task that should be delegated: choosing the
  dispatch mechanism (single Agent, Workflow script, sidecar), fanning out reviews/audits/fixes,
  splitting a large task into delegated chunks, pinning model + effort per stage, or authoring a
  Workflow. Sequential stage dependencies are an orchestration shape (pipeline), not a reason to skip.
  SKIP only for inline work — small edits and quick lookups where round-trip overhead exceeds the work.
---

# Orchestration

The mechanism layer for delegating work: which dispatch mechanism fits, how to fan out, how to pin model + effort per stage. This file names **roles, never models**: the role → model ladder is `reference/model_ladder_evidence.md` §Role guidance; aliases, prices and gates are `reference/external_models.json` (`python3 .claude/tools/model_registry.py available`); sidecar launch recipe is `reference/sidecar_dispatch.md`; spawn rules (MANDATORY / PARALLEL / NO POLLING) are `commands/agents/review_agents.md`. CLAUDE.md §Model Delegation keeps the two spec-time decisions: copyable-vs-derived, and which currency a fan-out spends.

## 0. Dispatch Shape — decide this FIRST

| mechanism | use for | never for |
|---|---|---|
| **Single `Agent`** | one bounded chunk (a plan slice, one TDD cycle, one survey); `subagent_type: "fork"` when it needs the session's conversation context; one exploratory dispatch to discover an item set | a fan-out — `Agent` inherits session effort invisibly and records no per-agent usage |
| **Workflow** (`Workflow` tool, `.claude/workflows/*.js`) | every fan-out (≥2 agents) and every judgment/verification stage: enumerable items, pipeline/barrier, scripted merge — per-call `model`+`effort` pins, schemas, resume. Generic engines `dispatch.js` (strict pins, prompts as file paths) and `review_fanout.js` (read-only lenses, FINDINGS schema) make the pinned route zero-authoring | reaching an external model — pins never cross transports |
| **Sidecar script** | any non-Anthropic model (GPT, opencode, deepseek, local): a separate `claude` child on its own endpoint. Bash, one job per call; recipe `reference/sidecar_dispatch.md` | — |

**Dispatch is transport-bound.** Workflow/Agent run on the session's endpoint only: an Anthropic session runs `claude-*` agents, a deepseek session deepseek agents (`hooks/model_pin_translate.py` translates role pins there). A vendor id inside `agent()` returns null, which `.filter(Boolean)` renders as "0 findings" — a clean-looking run that ran nothing. Check each fan-out's journal model column against the currency you intended.

**Litmus:** *can I enumerate the jobs now?* Yes → Workflow. No → ONE exploratory `Agent`, then Workflow over what it found. "No need for a workflow file" is a named rationalization. Workflow as a pinned-dispatch wrapper is standing-authorized; only *scale* beyond the task's natural shape needs the user's words, ultracode, or a command that invokes it. A recurring shape becomes a command invoking `Workflow({scriptPath})`; a command whose prose shouts anti-drift warnings at itself ("spawn exactly N in one message") is a drift fossil — move the determinism into a script.

**Fixed panels are floors, not ceilings.** A command's prescribed lens set always runs; extend it with bespoke lenses when the risk profile warrants, each naming the concrete failure mode it hunts.

**Concurrency under Workflow:** auto-cap `min(16, cores−2)`, overflow queues — pass all items. **Single-flight:** `parallel()`/`pipeline()` agents never each run GdUnit4 tests or fan out csharp-ls calls (`gotcha_workflow_single_flight_concurrency.md`) — pre-compute the symbol map, run the gate once serially outside the barrier; this is why review lenses are read-only. A *single-agent* wave may build and run narrowly-filtered suites; mandate TRX-counter evidence (`total > 0 AND failed >= 1`), never an exit code (`gotcha_zero_match_filter_exit1_mimics_red.md`).

## Pre-Dispatch Checklist

- [ ] **Read the budget band first** — the latest `[budget-posture]` line (`hooks/budget_posture.py`); absent, read `<TEMP>/cc-cachestat-<session_id>.json` `rate_limits.seven_day`. An unknown band silently defaults every lens to plan quota. A posture line reading **band UNREADABLE** is terminal for the session, not a transient miss: some entrypoints send no `rate_limits` at all (`--why` names which). Pin tier and effort on work shape per *Tier-within-quota* below, and expect sidecar band gates to refuse until passed `-A`.
- [ ] **Shared file writes?** Two agents editing one `.cs`/`.tscn`/`.tres` → serialize or partition explicitly.
- [ ] **Worktree isolation needed?** Agents share the working tree; write-parallel work over overlapping files takes `isolation: "worktree"` (§7).
- [ ] **Manual `Agent` dispatch only:** ≤15 agents, nested = `outer × inner ≤ 15` (§6).

## 1. When to Parallelize

3+ independent investigations (each failure its own root cause); independently broken subsystems; batch PR/audit operations (`/pr_pipeline`, `/session_audit`); cross-domain audits with one domain per agent; adversarial prompt batteries (`/test_skill`). **Why another agent, not another pass:** the builder reads the intention, not the result — an independent lens is the return, and it names the rule that produced the fault, not the instance.

## 2. When NOT to Parallelize

Related failures (one investigation, one fix; parallelize only the fix-write); shared file state; exploratory debugging where one finding reframes the next prompt; work needing the orchestrator's full picture; one file with several concerns; aesthetic or tonal consistency (fan out research, converge authoring in one context).

### Sizing the width

**Width is bounded by integration cost.** Read-only lanes integrate for free — width bounded only by spend and the cap. Write-parallel lanes integrate superlinearly — keep to a handful; one strong builder plus running critics beats six builders.

## 3. The Dispatch Procedure (manual `Agent`, legacy fallback)

1. Write each agent's exact scope; verify no dependency on another's output.
2. Self-contained tasks — no mid-flight context requests.
3. Pin model per agent (§5).
4. All `Agent` calls in ONE message; never `run_in_background`; wait for all.
5. Integrate: dedupe by `file:line`, reconcile contradictions, verify with the suite if fixes landed.

## 4. Agent Prompt Structure

Focused (one deliverable) · self-contained (context INLINE — orchestrator pushes, agents never pull) · exact output format (JSON / table / one-line verdict) · no coordination implied · **verification in the FOREGROUND, report in the same turn** (a backgrounded test run stalls the pipeline) · **never widen its own permissions** — a tool denial is a STOP, reported under `couldNotSatisfy`; re-attempting a refused edit through another mechanism (Bash `sed`/`python` after `Edit` denial) is an auto-mode bypass.

**Root-cause investigation/fix lanes embed `debugging` §The Recurrence Law** (subagents never auto-load skills): the spec carries evidence-before-hypothesis and the discriminating-evidence bar, and a lane returning a fix without that evidence is rejected at review, not merged.

```
You are <role>.
CONTEXT: <inline files, schemas, conventions>
TASK: <single deliverable>
OUTPUT: <exact format>
CONSTRAINTS: <hard rules; done-condition>
```

## 5. Model & Effort Selection

**Every dispatched agent pins both `model` and `effort`. An omitted pin inherits the session's — the bug, not a shortcut.** Effort first, then tier: effort buys turns, turns cost; exhaust the effort pin before trading the tier down. Load `reference/model_ladder_evidence.md` §Role guidance whenever you pin.

**Engine floor** (`review_fanout.js`, `doc_architecture_audit.js`) catches a forgotten pin at the default fan-out row only — it cannot tell a reasoning-heavy lens from a survey, and manual dispatches bypass it.

### Tier by lens shape

Litmus: *would a wrong answer be caught by re-reading the input, or only by out-reasoning it?*

- **Executor tier — floor for reasoning-heavy lenses:** red-team, architectural analysis, design semantics, refactor-parity gating, fix authorship — anywhere a miss ships a defect. Architecture authoring splits by altitude: scoped → executor; cross-domain → orchestrator tier (un-scopable, so not delegable).
- **Default fan-out tier — floor for read-heavy and mechanical lenses:** surveys, enumeration, text-comparison audits, rubric checklists, schema extraction. Never a design-judgment lens here to save cost.
- **Validation tier:** verify a PASS, re-check a finding, cheap-to-reject lookups — same model as default fan-out; the lever is a lower effort pin.
- **Orchestrator tier — never a delegate target** (a delegate does not delegate). Explicit user request only.
- **Escalation is per-lens**, raised for a specific heavier input, never a blanket panel bump.
- **Scout = `agentType: 'Explore'` + explicit model pin.** Locate/enumerate/extract verifiable without doctrine — Explore/Plan receive no CLAUDE.md and no memory index, so never a lens that must APPLY project rules. It is a third Workflow pin (`dispatch.js` per-job `agentType`), never a reason to drop to the `Agent` tool (no effort param → inherited session effort).

**The one inherit carve-out:** measurement batteries that test the session model's own behavior (`routing_battery.md`, `doc_workflow_battery.md`) omit `model` and `effort` on purpose. Do not "fix" them.

### Effort (Workflow `agent()` only)

`low | medium | high | xhigh` — `max` is banned on Anthropic pins; sidecar effort is a vendor coordinate read from the registry row.

**Work-shape defaults, never role defaults:** open design / architecting / root-cause debugging `high` (`xhigh` for the hardest design and buried-fork verification); executing a converged spec `low`, a loose one `medium`; any *anchored* lens (explicit rubric, supplied inventory, exact schema) `low` even on architectural subject matter; fan-out never above `high` (effort INVERTS past it); scouts `low`. A row whose ladder `effort` cell differs has moved its own boundary — honor the cell.

- **Pin by residual ambiguity, not size or importance.** Effort buys more steps, not deeper ones; a raise pays only where something remains to discover. Every raise names the ambiguity it resolves.
- **Each rung ≈1.4× the one below, within one model** — pull effort before tier; trading tier crosses a price ratio and buys verification work.
- **A sharp mandate substitutes for effort on sub-architectural inputs** — a lens's named failure mode does the work; on architecturally-loaded plans it does not, and executor-tier `high` finds what default-tier `medium` misses.
- **Bounded-ambiguity stages hard-set effort in the script** (judges, verifiers, extraction); per-invocation stages (arms, executors) take a script default plus an `args` override that carries a named justification.
- **Unsure between `low` and `medium` → `medium`.** Never characterize a tier from one observation; tier claims need the `/eval_dashboard` floor.
- **Before pinning, check `.claude/orchestration_candidates.json`** — a listed shape runs one rung below default on its next dispatch (`/orchestration_metrics` *Over-pin candidates*).

## 5b. Budget, Availability & Transport

Order: (1) is the model selectable — `model_registry.py available`; an excluded model is out, re-select under the ladder, never substitute by rule; (2) what the band allows; (3) how much quota the dispatch spends; (4) if it leaves Anthropic, the sidecar recipe.

**Bands** (`.claude/tools/quota_bands.py` `BANDS` is the only home; the `[budget-posture]` hook emits the current band's set): `pressure = used% / pace%` per window. `seven_day` governs provider choice and tier-within-quota; `five_hour` governs fan-out width. **Surplus** <0.85 (low pressure — spend plan quota first, it expires), **On pace**, **Ahead**, **Hot** >1.5 (paid transport becomes the cheaper currency). A band authorizes a CLASS of work; the roster supplies who does it — a band never names models. Bands govern PAID currencies only: the free local tier (`ai-worker`) takes copyable digest reads, extraction and doc prose in every band. Pressure widens the delegatable set; it never shrinks the reserved floor — orchestration, gate decisions, cross-system seams, the ideal-design VERDICT. Reading the band is mandatory; what it recommends is advisory, enforced in exactly one place: the sidecar's band gate (exit 5, `-A` overrides; balance floor exit 6 is not overridable; exit 7 unavailable outranks both).

**Tier-within-quota.** Plan quota is model-weighted: the executor tier at `low` matches the default fan-out tier on quality at ~2.6× the quota — buy it for judgment or wall-clock, never to save budget. First ask whether the work can leave quota at all (local tier, or an external model that *claims the role* in the registry's `roles`). What stays on quota:

| dispatch shape | Surplus / On pace | Ahead / Hot |
|---|---|---|
| planning, architecting, design judgment | executor `high` (`xhigh` hardest) | unchanged — the reserved floor |
| plan-check / review lenses | executor `low` | open lenses executor `low`; enumerable lenses default fan-out `medium` |
| execution under a converged spec | executor `low` | default fan-out `medium` |

Pressure moves the *tier*; ambiguity moves the *effort*. An architecting dispatch never drops to `low` for budget — off-quota transport or smaller scope instead. Record `clean`/`defects`/`rework` on every traded-down dispatch; an unrecorded trade-down is a saving you cannot defend.

**Authoring/verdict split.** Pressure may delegate large-scope architecture *authoring* to a strong external model; the *verdict* comes home. In an external-model-led session, gate decisions and the ideal-design verdict still warrant an Anthropic session or explicit user sign-off — and a Workflow fan-out there is not band-gated (`session_model_rails.py` states the session's tier and role map at SessionStart instead).

**Sidecar:** `reference/sidecar_dispatch.md` — one launcher per transport from the registry's `launcher` field, one flag surface, the `-D`×`-G` agent-type table, exit codes. Prefer few long agents to many short ones on a paid tier — each dispatch pays a cold-start toll.

**Workflow script preamble:** every new script copies the `PIN()`/`EFF()` resolver from `dispatch.js` (role-aware — `__pin.roles[m]` first) and reads `model: PIN(j.model), effort: EFF(j.effort)`. `hooks/model_pin_translate.py` injects `args.__pin` on deepseek sessions; without the preamble an Anthropic role literal hard-errors there. **Two more dispatch args:** `j.shape` (`any|survey|review|author`) selects the `.claude/guards/` rail family; `args.spillDir` makes each agent write its full deliverable to `<spillDir>/<label>.md` and return a ≤200-word digest — default ON for report/prose-shaped output, since unbounded returns are what force lossy compaction.

## 6. The 15-Agent Cap (manual `Agent` dispatch only)

Flat: ≤15 per batch. Nested: `outer × subagents-per-outer ≤ 15` — compute before dispatching, split into sequential batches above it.

## 7. Worktree Caveat

Parallel agents share one working tree: second write wins or fails on lock; `.tscn` edits must partition by scene. `isolation: "worktree"` per agent buys genuine isolation (~200–500ms + disk each; write-parallel work only); each fresh worktree needs the Jmodot submodule re-init (`archive_worktree_submodule_gotcha.md`).

## 8. Verification After Integration

Dedupe by `file:line` (keep the more specific / `critical` one); reconcile contradictions as orchestrator-only `## Notes`; run `/regression_gate` if fixes landed; never claim completion unverified — cite output or use future tense.

## 9. Authoring a Workflow on the Fly

The tool description documents the API; this is the project layer on top.

- **`Write` the script, invoke `Workflow({scriptPath})`, prompts INSIDE the script** — prompt text through the JSON `args` payload is what dies (`gotcha_workflow_args_generation_fidelity.md`, `gotcha_workflow_args_permission_control_chars.md`). `args` carries short scalars only, parse-guarded (arrives as a string on some versions); bulk context goes to a scratch `.md` agents `Read` by absolute path.
- **`log('PINS ' + JSON.stringify({label: effort, …}))` for EVERY dispatched label**, verify/adjudicate stages included — `/orchestration_metrics` refuses an unresolved `?`.
- **Record each dispatch's outcome when you consume it** (`clean`/`defects`/`rework`/`discarded`) — cost survives compaction, the verdict doesn't.
- **Write-shaped schemas carry `couldNotSatisfy`** (+ `redVerification` on TDD stages).
- **`pipeline()` by default; a barrier needs cross-item context from ALL of the prior stage.**
- **`schema` on every stage whose output feeds another.** Pin `model` and `effort` per stage. `log()` every silent cap. Loops guard on `budget.total`.
- **Cold context:** agents compare, never discover — fanned `Grep`/`Glob` false-empties (`gotcha_workflow_fanout_search_false_absence.md`). `meta` is a pure literal; `Date.now()`/`Math.random()` throw; resume with `resumeFromRunId`.
- **Promotion:** a script worth running twice goes to `.claude/workflows/` behind a command; audit the pair against `instruction_quality` §12.

## 10. Context Checkpointing (long-horizon drives)

Checkpoint state to the plan file at every slice boundary. Offer a stopping point in the **35–50% context** band — read `context_pct` from `<TEMP>/cc-cachestat-<session_id>.json` and quote it; never self-estimate. The boundary is in-session `/compact`, never a fresh-session handoff (`feedback_compact_in_session_at_stopping_points.md`). Choreography: make state durable → dispatch the next long-running Workflow FIRST (in-flight dispatches survive compaction) → announce "stopping point reached — safe to compact" with the percentage.

## 11. Dispatch Doctrine (efficiency-to-quality)

- **A converged spec IS the dispatch signal** — on every execution surface, and on EFFORT as well as tier: a converged spec gains nothing from a high-effort session executing it inline; dispatch at a low-effort pin even when the session model is the executor tier. The orchestrator keeps decisions, cross-system seams, final review; small surgical edits and lookups stay inline. Review depth tracks the intel gap: a lower tier lands ~80–90% and you close the rest; an equal-intel executor returns correctness-complete work you review for taste and fit.
- **Delegation grain by shape:** one coherent unit with a bounded, enumerable file set finished in one session — a plan slice, one TDD cycle, one subsystem survey, one checklist review. Can't enumerate the files → not yet a delegatable unit. Keep unscoped judgment, silent-failure risk and cross-system seams at the session model.
- **Cache-TTL asymmetry:** the main conversation holds a 1h prompt cache; every subagent is pinned to 5m. Keep blocking runs >5min (full suite, full build) in the main session and delegate the authoring around them; never interleave long-blocking calls with edits inside one subagent. `fork` starts warm; `isolation: "worktree"` builds its own prefix.
- **The spec is the price.** Spec-writing at the orchestrator tier converts a cheaper model's output up a tier; a loose spec converts it into rework. A landing spec carries a hand-verified exemplar, per-item deltas, hard invariants, a done-condition and "report what you couldn't satisfy".
- **Author the verification before dispatch, and the first instance yourself.** Data-pin tests first; one hand-made exemplar for a new `.tres`/`.tscn` pattern, then delegate the clones. Survey specs: question list + word cap + "quote key signatures". Digest/synthesis never burns agent-tier tokens — worker tier (`/research`).
- **Rate each dispatch's effort fit when you consume its result**; `/self_evaluate` flags mismatches, `/autolearn` proposes ladder edits when they recur.

## Cross-references

`commands/agents/review_agents.md` (spawn rules) · `commands/agents/orchestrator_action_protocol.md` (merge, report, claims to refuse) · exemplars `/session_audit` Phase 2, `/plan_check`, `/test_skill` · cold memory `archive_agent_task_gotchas.md`, `archive_worktree_session_setup.md`.
