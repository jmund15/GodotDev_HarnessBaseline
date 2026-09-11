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

The mechanism layer for delegating work: `/delegate` is the canonical route for ordinary ad hoc jobs; fixed-panel commands keep their own entry points. This file names **roles, never models**: the role → model ladder is `reference/model_ladder_evidence.md` §Role guidance; aliases, prices and gates are `reference/external_models.json` (`python3 .claude/tools/model_registry.py available`); sidecar launch recipe is `reference/sidecar_dispatch.md`; spawn rules (MANDATORY / PARALLEL / NO POLLING) are `commands/agents/review_agents.md`. CLAUDE.md §Model Delegation keeps the two spec-time decisions: copyable-vs-derived, and which currency a fan-out spends.

## 0. Dispatch Shape — decide this FIRST

| mechanism | use for | never for |
|---|---|---|
| **Single `Agent`** | one exploratory dispatch when the item set is unknown; `subagent_type: "fork"` when it needs the session conversation | an ordinary pinned job — use `/delegate`; a fan-out — `Agent` inherits session effort and records no per-agent usage |
| **Workflow** (`Workflow` tool, `.claude/workflows/*.js`) | every fan-out and judgment stage: enumerable items, pipeline/barrier, pins, schemas, resume. `/delegate` routes native work to `dispatch.js`, `dispatch_chains.js`, or `review_fanout.js` | reaching another transport; **a nested fan-out** — subagents cannot invoke Workflow. The orchestrator materializes every lens (`tools/lens_briefs.py`) and dispatches it (`gotcha_subagents_have_no_workflow_tool`) |
| **Backgrounded `Agent` lane** | independent lanes, the orchestrator has work for the interval, each return consumed from a spill file | never when the next step needs the result; the pin is stated at dispatch as for any job, but the lane logs no PINS row — record the pin in the plan file, and state the trade at dispatch |
| **Sidecar script** | ANY model on a transport this session is not running on — GPT, opencode, deepseek, local, and Anthropic itself from a provider session: a separate `claude` child on that transport's endpoint. Bash, one job per call; recipe `reference/sidecar_dispatch.md` | — |

**Dispatch is transport-bound.** Workflow/Agent run on the session's endpoint only: an Anthropic session runs `claude-*` agents, a codex session GPT agents, a deepseek session deepseek agents. Sibling models on the session's OWN transport are reachable in-harness by Workflow pin — no sidecar. Pin that transport's ids; `hooks/workflow_provider_guard.py` denies the wrong vocabulary and names the roster. A pin the endpoint cannot serve returns null, which `.filter(Boolean)` renders as "0 findings" — a clean-looking run that ran nothing, which is why the guard denies rather than warns. Check each fan-out's journal model column against the currency you intended.

**Litmus:** *can I enumerate the jobs now?* Yes → `/delegate`. No → one exploratory `Agent`, then `/delegate` over what it found. Direct Workflow remains for fixed-panel commands and workflow authoring. A recurring shape becomes a command invoking `Workflow({scriptPath})`. A nested command that fans out is denied by `dispatch_mechanism_guard.py`; the main session owns the fan-out.

**Fixed panels are floors, not ceilings.** A command's prescribed lens set always runs; extend it with bespoke lenses when the risk profile warrants, each naming the concrete failure mode it hunts.

**Non-destructive fan-outs run as multi-model arms.** Exploration, plan drafts, plan-check and review lenses dispatch one Anthropic arm plus each available sidecar model (`python3 .claude/tools/model_registry.py available`; sidecar lenses take `-S <schema>` + `-P` so a compaction auto-resumes), and each arm's outcome lands in the dispatch ledger so `/orchestration_metrics` can contrast them; execution under a converged spec stays single-arm.

**Arms that will be COMPARED read a frozen input.** Ladder evidence, `/pin_ab`, multi-model plan-check or review: dispatch against a detached worktree (`git worktree add --detach`, passed as `-d`/cwd). `sidecar_fanout.py --compare` enforces it.

**Land nothing until the last arm returns.** An arm that reads a fix you already applied reports it ABSENT — indistinguishable from a miss, and undetectable later (`gotcha_comparison_arms_need_a_frozen_input`).

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
4. Dispatch all lanes in ONE message. **Block** (`run_in_background: false`) when your next action needs a lane's result. **Background** when the lanes are independent and you have work for the interval: the runtime backgrounds by default and notifies on completion, and `SendMessage` continues a lane with its context intact. Never poll for a result you will be notified about. Consume a backgrounded lane by its artifact (`args.spillDir`), never by liveness — the `[killed]` reaping measured for **Bash** `run_in_background` is unattested here, and an artifact check is correct either way. Backgrounded `Agent` lanes carry no effort pin and no PINS row: say what you are trading before you dispatch.
5. Integrate: dedupe by `file:line`, reconcile contradictions, verify with the suite if fixes landed.

## 4. Agent Prompt Structure

Focused (one deliverable) · self-contained (context INLINE — orchestrator pushes, agents never pull) · exact output format (JSON / table / one-line verdict) · no coordination implied · **verification in the FOREGROUND, report in the same turn** (a delegate's own **Bash** test run — the `[killed]` reaping is measured for Bash `run_in_background`; whether it reaches Agent lanes is unattested (assessment R3, probe in C2); artifact consumption is correct either way) · **never widen its own permissions** — a tool denial is a STOP, reported under `couldNotSatisfy`; re-attempting a refused edit through another mechanism (Bash `sed`/`python` after `Edit` denial) is an auto-mode bypass · **every user-stated exclusion travels in CONSTRAINTS** (docs not to read, sources not to use, folders not to write) — delegates and `write_doc` (`include_session_chat=false`, `reference_files=[]`) inherit nothing from chat.

**Root-cause investigation/fix lanes embed `debugging` §The Recurrence Law** (subagents never auto-load skills): the spec carries evidence-before-hypothesis and the discriminating-evidence bar, and a lane returning a fix without that evidence is rejected at review, not merged.

```
You are <role>.
CONTEXT: <inline files, schemas, conventions>
WHY: <the larger task this serves; what the result enables next>
TASK: <single deliverable>
OUTPUT: <exact format>
CONSTRAINTS: <hard rules; done-condition>
```

## 5. Model & Effort Selection

**Every dispatched agent pins both `model` and `effort`. An omitted pin inherits the session's — the bug, not a shortcut.** Effort first, then tier: effort buys turns, turns cost; exhaust the effort pin before trading the tier down. Load `reference/model_ladder_evidence.md` §Role guidance whenever you pin.

**Resolve a role to a model with `model_registry.py for-role <orchestrator|executor|fanout|scout>`, never from memory.** It reports rows — in-transport, across the hop with its launcher line, and the nearest tier when neither — and resolves no pin: vendor ids stay the only legal pin on a provider session.

**Effort is a lever only where the transport lets it be one.** Where a transport fixes effort server-side a per-agent pin is inert, leaving transport-plus-tier — codex reads `CCP_CODEX_EFFORT` once at proxy startup.

**Engine floor** (`review_fanout.js`, `doc_architecture_audit.js`) catches a forgotten pin at the default fan-out row only — it cannot tell a reasoning-heavy lens from a survey, and manual dispatches bypass it.

### Tier by lens shape

Litmus: *would a wrong answer be caught by re-reading the input, or only by out-reasoning it?* Which work shapes each tier covers is the ladder's `§Role definitions` table; the rules below are the routing on top of it.

- **Executor tier — floor for reasoning-heavy lenses:** anywhere a miss ships a defect. Architecture authoring splits by altitude: scoped → executor; cross-domain → orchestrator tier (un-scopable, so not delegable).
- **Default fan-out tier — floor for read-heavy and mechanical lenses.** Never a design-judgment lens here to save cost.
- **Validation tier:** verify a PASS, re-check a finding, cheap-to-reject lookups — same model as default fan-out; the lever is a lower effort pin.
- **Orchestrator tier as a delegate is a cost default, not a capability rule** — off by default; open it per lens via `/pin_ab`, never by blanket pin.
- **Escalation is per-lens**, raised for a specific heavier input, never a blanket panel bump.
- **Scout = `agentType: 'Explore'` + explicit model pin.** Locate/enumerate/extract verifiable without doctrine — Explore/Plan receive no CLAUDE.md and no memory index, so never a lens that must APPLY project rules. It is a third Workflow pin (`dispatch.js` per-job `agentType`), never a reason to drop to the `Agent` tool (no effort param → inherited session effort).

**The one inherit carve-out:** measurement batteries that test the session model's own behavior (`routing_battery.md`, `doc_workflow_battery.md`) omit `model` and `effort` on purpose. Do not "fix" them.

### Per-dispatch harness cost

A third pin beside model and effort: how much harness the child loads, charged PER AGENT — a 10-lens fan-out pays it ten times.

| mechanism | knob | where |
|---|---|---|
| Workflow `agent()` | `opts.agentType` (`dispatch.js` requires it per job) | table below |
| `Agent` tool | `subagent_type` (same agent types; no effort pin — fan-outs don't go here) | table below |
| Sidecar | `-D bare\|pointer\|full` × `-G` | `reference/sidecar_dispatch.md` agent-type table |

First-turn input tokens, identical trivial prompt (Anthropic transports):

| agentType | Sonnet | Haiku |
|---|---|---|
| `Explore` / `Plan` | 27.8K | 15.1K |
| `general-purpose` / default workflow subagent | 53.4K | 33.6K |

`Explore`/`Plan` receive no project CLAUDE.md and no memory index — read-only locate/enumerate/extract only, never a lens that must APPLY project rules (`bare` is the sidecar analog; `full` ≈ `general-purpose`). An unpinned agent inherits the SESSION model whatever its agentType.

### Effort (Workflow `agent()` only)

`low | medium | high | xhigh` — `max` is banned on Anthropic pins; sidecar effort is a vendor coordinate read from the registry row. The `PINS` line records the request, not the effect — where the transport fixes effort (*Effort is a lever only where the transport lets it be one*), confirm effective effort from the run record before citing a pin as evidence.

**Work-shape defaults, never role defaults:** open design / architecting / root-cause debugging `high` (`xhigh` for the hardest design and buried-fork verification); deep review / red-team / adversarial plan review on opus `xhigh` — `high` finds a fraction of the planted defects (ladder §Pick by work shape); executing a converged spec `low`, a loose one `medium`; any *anchored* lens (explicit rubric, supplied inventory, exact schema) `low` even on architectural subject matter; fan-out never above the receiving row's ceiling — the ladder row owns the cell (sonnet: never above `high`, effort inverts); scouts `low`. A row whose ladder `effort` cell differs has moved its own boundary — honor the cell.

- **Pin by residual ambiguity, not size or importance.** Effort buys more steps, not deeper ones; a raise pays only where something remains to discover. Every raise names the ambiguity it resolves.
- **Each rung ≈1.4× the one below, within one model** (measured on opus; re-sweep per model — level names do not map across models) — pull effort before tier; trading tier crosses a price ratio and buys verification work.
- **On a SIDECAR model, effort can gate engagement, not depth.** An investigative lens (review, red-team, exploration, root-cause) pins the vendor's top rung: below it, a lens answers from its inlined context and never opens a file — measured on Luna, 3 turns and 0 tool calls at `medium` against 79 tool calls and 5 findings at `max`, same input. Anthropic rungs still buy steps within an already-engaged process; do not port one model's calibration to another (ladder row owns each cell).
- **A sharp mandate substitutes for effort on sub-architectural inputs** — a lens's named failure mode does the work; on architecturally-loaded plans it does not, and executor-tier `high` finds what default-tier `medium` misses.
- **Compare the read set against the ARM's context window, not against the input's size.** A row with a quarter-million-token window compacts partway through a sweep a million-token row would finish in one pass, so the same mandate is cheap on one and mid-run on another; `model_registry.py context-window <id>` gives the number. Compaction is survivable, not degrading — `sidecar_fanout.py` sends `-P` always and `-S` on every review shape, and with both the arm resumes into its structured deliverable. Without `-S` it resumes into prose, which is a dispatch error and never the row's ceiling.
- **Bounded-ambiguity stages hard-set effort in the script** (judges, verifiers, extraction); per-invocation stages (arms, executors) take a script default plus an `args` override that carries a named justification.
- **Unsure between `low` and `medium` → `medium`.** Never characterize a tier from one observation; tier claims need the `/eval_dashboard` floor.
- **Before pinning, check `.claude/orchestration_candidates.json`** — a listed shape runs one rung below default on its next dispatch (`/orchestration_metrics` *Over-pin candidates*).

## 5b. Budget, Availability & Transport

Order: (1) is the model selectable — `model_registry.py available`; an excluded model is out, re-select under the ladder, never substitute by rule; (2) what the band allows; (3) how much quota the dispatch spends; (4) if it leaves Anthropic, the sidecar recipe.

**Bands are per CURRENCY, and a seat has two.** A Workflow/Agent dispatch is governed by its own transport's band; a hop spends the TARGET's — read it with `python3 .claude/hooks/budget_posture.py --band --transport <name>`. On a provider seat `[budget-posture]` prints both, labelled.

**Bands.** `pressure = used% / pace%` per window; `.claude/tools/quota_bands.py` `BANDS` is the only home, and the `[budget-posture]` hook emits the current band's set.

- **Surplus** <0.85 (spend plan quota first — it expires), **On pace**, **Ahead**, **Hot** >1.5 (paid transport becomes the cheaper currency). `seven_day` governs provider choice and tier-within-quota; `five_hour` governs fan-out width.
- **A band authorizes a CLASS of work; the roster supplies who does it** — a band never names models. Bands govern PAID currencies only: the free local tier (`ai-worker`) takes copyable digest reads, extraction and doc prose in every band.
- **Pressure widens the delegatable set and never shrinks the reserved floor** — orchestration, gate decisions, cross-system seams, the ideal-design VERDICT.
- **The floor reserves those DECISIONS, not the work shape.** A *scoped* judgment, review or architecting LENS is delegable in every band, to the ladder's row for that work shape, by sidecar hop when that row is off-transport. What comes home is the verdict on its findings.
- **Two hooks enforce it.** The sidecar's band gate (exit codes in `reference/sidecar_dispatch.md`), and `hooks/workflow_provider_guard.py`: under **Ahead/Hot** it DENIES every Workflow/Agent dispatch carrying a model pin — claimed by a roster model or not — unless the call states the currency. `args.currency: "anthropic"` + `args.currencyReason`; Agent takes a `CURRENCY: anthropic — <why>` prompt line. Stated once per band, then held on the session record.
- **A pin is not its own justification.** A pin the engine defaulted to, or one a command's own table supplied, is not a constraint, so the reason names one independent of the pin — engine lock, MCP tools the sidecar child lacks, a capability the roster genuinely lacks. "The lens is pinned opus" is circular, and a command's pin table is subordinate to its own band rule.

**Tier-within-quota.** Plan quota is model-weighted: the executor tier at `low` matches the default fan-out tier on quality at ~2.6× the quota — buy it for judgment or wall-clock, never to save budget. First ask whether the work can leave quota at all (local tier, or an external model that *claims the role* in the registry's `roles`). What stays on quota:

| dispatch shape | Surplus / On pace | Ahead / Hot |
|---|---|---|
| planning, architecting, design judgment | executor `high` (`xhigh` hardest) | unchanged — the reserved floor |
| plan-check / review lenses | executor `low` | open lenses executor `low`; enumerable lenses default fan-out `medium` |
| execution under a converged spec | executor `low` | default fan-out `medium` |

Pressure moves the *tier*; ambiguity moves the *effort*. An architecting dispatch never drops to `low` for budget — off-quota transport or smaller scope instead. Record `clean`/`defects`/`rework` on every traded-down dispatch; an unrecorded trade-down is a saving you cannot defend.

**On an external-model-led session** the floor above still binds: gate decisions and the ideal-design verdict warrant an Anthropic session or explicit user sign-off, even though large-scope architecture *authoring* is delegable there. A Workflow fan-out from that seat is not band-gated — `session_model_rails.py` states the session's tier and role map at SessionStart instead.

**Sidecar:** `reference/sidecar_dispatch.md` — one launcher per transport from the registry's `launcher` field, one flag surface, the `-D`×`-G` agent-type table, exit codes. Prefer few long agents to many short ones on a paid tier — each dispatch pays a cold-start toll.

**Generic engines are provider-aware, not cross-transport.** The provider guard injects `args.__transport = {name, ids, default}` off-Anthropic; `dispatch.js`, `dispatch_chains.js`, and `review_fanout.js` then accept that transport's ids. Another transport still needs its registry launcher. `/delegate` keeps executor `route` separate from delegate-rail `shape`: only `shape` (`any|survey|review|author`) reaches sidecar `-G`. `args.spillDir` defaults on for prose output and stays under `.claude/scratch/` or `$TEMP/claude`.

## 6. The 15-Agent Cap (manual `Agent` dispatch only)

Flat: ≤15 per batch. Nested: `outer × subagents-per-outer ≤ 15` — compute before dispatching, split into sequential batches above it.

## 7. Worktree Caveat

Parallel agents share one working tree: second write wins or fails on lock; `.tscn` edits must partition by scene. `isolation: "worktree"` per agent buys genuine isolation (~200–500ms + disk each; write-parallel work only); each fresh worktree needs the Jmodot submodule re-init (`archive_worktree_submodule_gotcha.md`).

## 8. Verification After Integration

Dedupe by `file:line` (keep the more specific / `critical` one); reconcile contradictions as orchestrator-only `## Notes`; run `/regression_gate` if fixes landed; never claim completion unverified — cite output or use future tense.

**A lens that stalls or returns null is recovered before any re-dispatch:** its spill file, else `/salvage_fanout`, which owns recovery. **Check deliverable rules against the delivered artifact, not the research spill.** `/delegate` records outcomes on consumption, then joins each expanded label to exactly one Workflow or sidecar row through `orchestration_metrics.py --manifest-seed`; missing or duplicate evidence fails.

## 9. Authoring a Workflow on the Fly

The tool description documents the API; this is the project layer on top.

- **Pass new scripts inline via `Workflow({script})`; iterate with the returned `scriptPath`** — keep prompts inside the script: prompt text through JSON `args` can die (`gotcha_workflow_args_generation_fidelity.md`, `gotcha_workflow_args_permission_control_chars.md`). `args` carries short scalars only; bulk context goes to a scratch `.md` agents `Read` by absolute path.
- **`log('PINS ' + JSON.stringify({label: effort, …}))` for EVERY dispatched label**, verify/adjudicate stages included — `/orchestration_metrics` refuses an unresolved `?`.
- **Record each dispatch's outcome when you consume it** (`clean`/`defects`/`rework`/`discarded`) — cost survives compaction, the verdict doesn't.
- **Write-shaped schemas carry `couldNotSatisfy`** (+ `redVerification` on TDD stages).
- **`pipeline()` by default; a barrier needs cross-item context from ALL of the prior stage.**
- **`schema` on every stage whose output feeds another.** Pin `model` and `effort` per stage. `log()` every silent cap. Loops guard on `budget.total`.
- **Cold context:** agents compare, never discover — fanned `Grep`/`Glob` false-empties (`gotcha_workflow_fanout_search_false_absence.md`). `meta` is a pure literal; `Date.now()`/`Math.random()` throw; resume with `resumeFromRunId`.
- **Promotion:** a script worth running twice goes to `.claude/workflows/` behind a command; audit the pair against `instruction_quality` §12.

## 10. Context Checkpointing (long-horizon drives)

Checkpoint state to the plan file at every slice boundary. Before any deliberate compaction, dispatch the next long-running Workflow FIRST — in-flight dispatches survive compaction. The boundary is in-session `/compact`, never a fresh-session handoff (`feedback_compact_in_session_at_stopping_points.md`).

At each slice checkpoint report the context percentage to the user as one line: `Checkpoint: slice N done, context NN% — compact when you choose.` Read `context_pct` from `<TEMP>/cc-cachestat-<session_id>.json`; never self-estimate. The number is information for the user; it is never a reason to stop, summarize, trim work, or propose a new session — continue the drive.

## 11. Dispatch Doctrine (efficiency-to-quality)

- **A converged spec IS the dispatch signal** — on every execution surface, and on EFFORT as well as tier: a converged spec gains nothing from a high-effort session executing it inline; dispatch at a low-effort pin even when the session model is the executor tier. The orchestrator keeps decisions, cross-system seams, final review; small surgical edits and lookups stay inline. Review depth tracks the intel gap: a lower tier lands ~80–90% and you close the rest; an equal-intel executor returns correctness-complete work you review for taste and fit.
- **Delegation grain by shape:** one coherent unit with a bounded, enumerable file set finished in one session — a plan slice, one TDD cycle, one subsystem survey, one checklist review. Can't enumerate the files → not yet a delegatable unit. Keep unscoped judgment, silent-failure risk and cross-system seams at the session model.
- **Cache-TTL asymmetry:** the main conversation holds a 1h prompt cache; every subagent is pinned to 5m. Keep blocking runs >5min (full suite, full build) in the main session and delegate the authoring around them; never interleave long-blocking calls with edits inside one subagent. `fork` starts warm; `isolation: "worktree"` builds its own prefix.
- **The spec is the price.** Spec-writing at the orchestrator tier converts a cheaper model's output up a tier; a loose spec converts it into rework. A landing spec carries a hand-verified exemplar, per-item deltas, hard invariants, a done-condition and "report what you couldn't satisfy".
- **Author the verification before dispatch, and the first instance yourself.** Data-pin tests first; one hand-made exemplar for a new `.tres`/`.tscn` pattern, then delegate the clones. Survey specs: question list + word cap + "quote key signatures". Digest/synthesis never burns agent-tier tokens — worker tier (`/research`).
- **Rate each dispatch's effort fit when you consume its result**; `/self_evaluate` flags mismatches, `/autolearn` proposes ladder edits when they recur.

## Cross-references

`commands/agents/review_agents.md` (spawn rules) · `commands/agents/orchestrator_action_protocol.md` (merge, report, claims to refuse) · exemplars `/session_audit` Phase 2, `/plan_check`, `/test_skill` · cold memory `archive_agent_task_gotchas.md`, `archive_worktree_session_setup.md`.
