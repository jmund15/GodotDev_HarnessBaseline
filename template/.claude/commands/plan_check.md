---
allowed-tools: Bash(git ls-files:*), Bash(grep:*), Glob, Grep, Read, Write, Workflow, Task
description: Audit a proposed plan before execution — "plan check", "audit the plan". SKIP for ≤2-file plans.
---

Audit a plan **before** implementation, through a lens set chosen by `PLAN_SHAPE` (Phase 1a). Parallel subagents, then consolidation.

- **code** plan: memorialized failure modes; abstractions the plan parallels rather than extends; ideal architecture (axis-nesting, authored-surface coherence, designer ergonomics, ownership seams); test-first executability under Hybrid TDD.
- **meta** (harness/doctrine) plan: memorialized failure modes; the `instruction_quality` rubric plus how each edit is verified; contradiction against live doctrine and inbound-reference rot.
- **Both**: *evidence-grounding* — are load-bearing factual claims measured or asserted. Universal: files get written on a confidently-stated fact before anyone checks it came from one observation of a variable condition.

Phase 1b adds *Definition-of-Done completeness*, *stub/TODO scan*, *cross-Part dependency soundness* Claude-side.

**Advisory — does NOT block execution.** Findings are scope/approach corrections.

## When to invoke

User-requested after a plan proposal (auto-invokable on the frontmatter trigger phrases). Run when **any** hold:
- **3+ files** touched
- a **new type, folder, or top-level concept**
- a **new `[Export]`/authored field or behavior-selecting bool/enum** on an existing class — especially where the folder/base carries a `*Strategy`/`*Config` sibling
- a **subclass added to an existing 2+ family**, or a refactor of a domain that has one (StatusEffect, SpellEffect, StateBase, IBlackboardProvider, ISpell, SpellBehavior)
- **deleting or replacing existing files** — gives `/session_audit` Phase 1.5 a pre-enumerated surface
- an edit to an **always-loaded harness surface** (`CLAUDE.md`, `MEMORY.md`, a `paths:`-globbed rule, a skill `description:`), or doctrine changes across **3+ `.claude/` files** — the `meta` shape. No `/regression_gate` (meta commits are exempt), no compiler, no test: plan-time is their only gate.

Below that, trust the planner — `plan_memory_reminder.py` (PostToolUse on `Write`/`Edit` to `.claude/plans/*.md`) covers routine plans passively. **Coverage hole:** that hook emits nothing below a 50-word plan floor, and SKIP-eligible plans are the terse ones most likely to sit under it.

## Composition with other audits

| Lifecycle stage | Tool | What it covers |
|---|---|---|
| Plan-entry (before draft) | `/plan_part` | Verbatim design surface + codebase drift + macro→`arch-rework` kick; HARD-STOPS on macro drift |
| Plan-time (after draft) | `/plan_check` | Memory gotchas + ordering hazards + existing-abstraction discovery + structure rules + test-first executability + DoD/stub/cross-Part-dep completeness |
| Pre-commit | `/session_audit` Phase 1.5 (MERGE-BLOCKER) | Stub markers + retired-code parity diff — verifies retired surface was *reproduced*, where `/plan_check` verifies proposed surface *covers requirements* |
| Post-implementation | `/session_audit` Phases 2–3 | Code-quality / robustness / testability rubric |

---

## Phase 1: Scope & Load

### 1a. Read the plan

- `/plan_check <inline plan text>` — plan text passed directly
- `/plan_check @<filepath>` — read from file (e.g. `@.claude/plans/foo.md`)
- `/plan_check` (no argument) — read the most-recently-modified `.md` under `.claude/plans/` (where `part_drive` writes)

Any form accepts `--meta` / `--code` to override shape detection. Strip the flag, then store the plan text as `PLAN_TEXT`.

**Prototype-grade detection.** Work targeting `prototypes/<slug>/` files or a `prototype/<slug>` branch is a prototype brief: abort and route to the `prototype` skill (`QUESTION.md` + `ANSWER.md` are its artifacts). A plan that merely *cites* `prototypes/<slug>/ANSWER.md` under `Constraints` is a real Part plan — run the FULL gate.

**Shape classification.** `PLAN_SHAPE` selects the Phase 2 lens set; the default lenses are calibrated for C# gameplay code and would score a harness plan on axes it has none of.

- `meta` — EVERY written path is agent-runtime or docs infrastructure (`.claude/**`, `docs/**`, `Docs/**`, Obsidian vault), AND the plan writes no `.cs`, `.tscn`, `.tres`, `.csproj`.
- `code` — anything else. **Default: one production file makes the plan `code`**, however much harness work rides along, so the production surface is never audited by the lighter set.

State any override in the report header with the detected shape it replaced. A plan editing harness files *in service of* a production change (a domain row because a new subsystem landed) is `code`. Harness work that is an **independent deliverable** is partitioned away by the drafter ([*Plan-file format*](../skills/_brainstorm_shared/plan_file_format.md) → *One shape per plan file*); when such a plan arrives anyway, run the `code` set and name in the header which harness surfaces went un-lensed.

### 1b. Inline scope-coverage check (orchestrator, no agent)

String/structure matching over the plan + roadmap state — no subagent. Report under a **Scope Coverage** subsection.

- Find sections labelled "Requirements", "Goals", "Must do", "Acceptance Criteria", "Scope".
- Find steps in numbered/bulleted lists, "Implementation", "Steps", "Phases".
- Map each requirement to its addressing step(s).
- Flag orphan requirements (no addressing step) and orphan steps (trace to no requirement).
- Flag **step↔step contradictions** — two steps prescribing opposite actions on one target. Requirement↔step tracing misses these; scan explicitly.
- **Definition-of-Done completeness** — does the plan name its closing steps? Incomplete if a `.cs` plan omits `/regression_gate` before commit, omits `<summary>` doc-coverage for a new `[Export]`, or (roadmap Part) omits the closing `/update_roadmap` state flip. Closing verification follows chain position (`change_control` §Gate cadence): a **mid-chain Part** names `verify.ps1 -Scope <domains>` over its accumulated blast zone and gates nothing; the **chain-final Part** names the FULL `/regression_gate`. **A multi-Part plan scheduling a gate + commits at EVERY Part close has inverted the cadence** — one full gate per drive; a mid-drive commit must state why it cannot wait. Flag any narrowed gate tier (`-Smoke`, `-SmokeImport`, `-Targeted`, `-SkipStatic`): those flags no longer exist.
- **Stub / deferral marker scan** — grep the plan for `TODO|FIXME|deferred|defer to|follow-up|stub|placeholder|later pass|out of scope (then used anyway)`. A marker on in-scope work is the parity merge-blocker class caught early (`feedback_refactor_parity_audit`).
- **Invention scan** — every element the plan introduces (type, `[Export]`, file, behavior, step) must trace to the design doc/brief or cited codebase evidence. One grounded in neither is scope invented during planning: require justification where it appears, or drop it. Orphan-step tracing asks whether a step serves a requirement; this asks where the element came from at all.
- **Cross-Part dependency soundness** — for a roadmap Part, check each artifact it assumes already exists (symbol, scene node, autoload, `.tres` "provided by Part N-1") against a prior Part's Definition-of-Done or shipped state. A phantom prior-Part output passes every other lens and still stalls the executor.

### 1c. Domain inference

Keyword-match `PLAN_TEXT` case-insensitively against the same domain table as `plan_memory_reminder.py`, documented at `.claude/reference/memory_domains.md`. Build `INFERRED_DOMAINS`.

**Empty-domain abort applies to `code` plans only.** `PLAN_SHAPE == code` and `INFERRED_DOMAINS` empty → abort: "Plan inference matched no {{PROJECT_NAME}} domains — `/plan_check` has no useful contribution. Proceed with the plan as authored."

A `meta` plan is EXPECTED to match no domain (the table maps gameplay subsystems); aborting there withholds the audit from the plans whose blast radius is every future session. For `meta`, skip the abort and set `INFERRED_DOMAINS = ["Harness/Meta"]`.

### 1d. Load auto-memory gotchas

Per domain, run the auto-memory single-keyword search; concatenate into `MEMORY_HITS`, preserving entity/file names for citation. `Harness/Meta` has no table row — seed it from auto-memory searches for instruction-quality, tool-routing, harness-file and process-discipline rules (`MEMORY.md`'s *Communication & process discipline*, *Tool routing & workflow*, *Harness files* clusters). This search is a **seed/floor**: Phase 2's *Dispatch doctrine* mandates the memory lens to search itself and exceed it.

**Ordering-hazard subset (feeds `plc-memory-alignment`).** Always add the step-*ordering* gotchas so the lens checks step sequence, not just individual steps: autoload subscription order (`gotcha_autoload_to_autoload_subscription_order`), `OnExit` clobbering a consumer's `OnEnter` read (`arch_rule_onexit_must_not_clobber_consumer_onenter`), init-timing (spell spawn pipeline), spawn-marker-inside-trigger-volume.

### 1e. Load known-failure-mode catalog

Run `python3 .claude/tools/kfm.py index` and inject its output as `KNOWN_FAILURE_MODES` (~7KB). Never read [`commands/checklists/known_failure_modes.md`](checklists/known_failure_modes.md) whole — the full catalog is ~49KB and would be paid once per lens. Lenses fetch the bodies they need themselves (catalog §Access).

When `PLAN_SHAPE` names a domain a catalog section covers wholesale — `.tscn`/`.tres` authoring, status/VFX, test/workflow — additionally inject `kfm.py get -s <section>` for that section, so the lens starts from bodies rather than triggers in its own domain.

### 1f. Symbol references for proposed types AND authored surfaces

**`PLAN_SHAPE == meta` replaces this step with 1f-meta** — a harness plan has no types, exports or `.tres` corpus, and the lenses consuming `SYMBOL_REFS`/`NEIGHBOUR_FAMILIES` are not running.

Enumerate from the plan BOTH proposed types/classes/interfaces AND proposed `[Export]`s / authored fields / parameters / behavior flags (the plan's *Authored surfaces* section is the primary source). Then:
- **Local sessions**: LSP `findReferences` per named symbol via the csharp-ls plugin.
- **Cloud sessions** (csharp-ls disabled): `Grep("class\\s+<TypeName>|interface\\s+<TypeName>|: <TypeName>", glob="**/*.cs")`.

Capture as `SYMBOL_REFS`; per proposed *new* type, the count of existing siblings in the same namespace/folder is the load-bearing signal for `plc-pattern-fit`.

**Existence sweep (mandatory per NEW-file/type row):** semantic-search the proposed type by NAME and by CONCEPT (what it computes, not what it is called) across the repo incl. Jmodot. A live equivalent under a different path/shape is a REUSE finding; two `[GlobalClass]` Resources sharing a simple name collide regardless of namespace.

**Export-surface sweep (mandatory per NEW-export row):** per proposed export/field/flag, semantic-search the described BEHAVIOR (what the knob selects, not its name) restricted to the target file's directory, its parent, and the target class's base; and Grep the `.tres` corpus for an existing field already carrying that value. Capture as `NEIGHBOUR_FAMILIES`, labelled unverified per *Verify, don't trust*. Catches a bool shadowing an existing `*Strategy` family, and a value about to be authored in a second home.

### 1f-meta. Inbound-reference sweep (`PLAN_SHAPE == meta` only)

A harness edit's blast radius is measured in *citations*, not call sites. For each surface the plan renames, moves, deletes, renumbers or rewords, enumerate who points at it. Capture as `INBOUND_REFS`, labelled unverified per *Verify, don't trust*.

- **File path** — `Grep` the cited path across `.claude/` (commands, skills, rules, hooks, memory, tests) and the Obsidian vault.
- **Rule / lens / agent key / hook rule-name string** — highest-rot class: nothing errors, the old name stops matching and the check never fires again. Grep the literal old string.
- **Section anchor / `§N`** — inserting or removing a numbered section renumbers every one below; grep `§` citations of the target file.
- **Test fixtures and batteries** — `.claude/tests/`, routing batteries, hook fixtures. A fixture encoding pre-revision behavior rewards the obsolete action (`instruction_quality` §4 *inbound-reference rot*).

Use the `Grep` tool, never raw `grep -r`: `.claude/worktrees/` holds whole extra checkouts whose hits are indistinguishable from real ones.

### 1g. Load support skills

Pre-load into agent CONTEXT; agents do not re-read them.

`PLAN_SHAPE == code`:
- `architecture_philosophy/SKILL.md` and `architecture_philosophy/structure_rules.md` (plc-pattern-fit)
- `.claude/generated/abstraction_families.md` — rows only, for plc-pattern-fit and plc-architecture-quality: the family rows whose name or owning folder matches `INFERRED_DOMAINS` or the plan's touched paths (never the whole 300-family file), labelled as the inventory the plan must reconcile against.

`PLAN_SHAPE == meta` (the above have no referent):
- `skills/instruction_quality/SKILL.md` — WHOLE, for plc-instruction-quality.
- Live text of each doctrine surface the plan edits, for plc-doctrine-consistency (CONTEXT item 11).

---

## Phase 2: Launch Plan-Check Sub-Agents

**Sub-agent delegation is MANDATORY, dispatched via Workflow.** Write the assembled CONTEXT block (Phase 1 outputs, full plan text inline) to a scratchpad file, then:
`Workflow({scriptPath: ".claude/workflows/review_fanout.js", args: {agents: [{key, prompt: <lens template text>, model, effort}], contextPrefixPath: <context file path>}})` — this command's instruction IS the Workflow authorization.

- **Provider is chosen by BAND before any pin is read; lens count never selects it.** Read the `[budget-posture]` line first (bands: `orchestration` §5b). **Surplus → the engine, every lens. On-pace or above → the sidecar** — a plan audit is the adversarial/architectural-review class the Ahead and Hot bands widen. `Workflow` dispatches on the session's own endpoint, so **the engine cannot reach the sidecar**: routing there means one `deepseek_sidecar.sh` call per lens (`-m flash -e max -G review`, plus `-R` and `-l "plancheck:<key>"` for attributable spend), consolidated by the orchestrator instead of the engine. Bound lens output in the MANDATE, never with `-S` — a schema cap discards the whole deliverable (`feedback_schema_caps_must_not_invalidate_delegate_work`). The verdict stays the orchestrator's either way.
- **Per-lens pins.** Design-judgment lenses are opus-floored per `orchestration` §5. Sub-architectural plans: `plc-pattern-fit` + `plc-architecture-quality` at `opus·low`. **Architecturally-loaded plans** (new abstractions, framework-boundary changes, 2+ subsystem reach) raise `plc-architecture-quality` to `opus·high` — the one lens doing open-ended structural judgment — and `plc-pattern-fit` + `plc-memory-alignment` to `opus·medium`, since the symbol inventory and memory seed anchor them. `plc-test-readiness` is `sonnet·medium` everywhere; `plc-memory-alignment` defaults `sonnet·medium` sub-architecturally. Raises pass `args.justification` naming the ambiguity. Pin evidence: `reference/model_ladder_evidence.md`.
- **`plc-evidence-grounding` pins `opus·low` on every plan, both shapes, never escalated.** Its mandate is closed — enumerate load-bearing assertions, check each against its backing — so effort buys steps it does not need, and it runs universally.
- **No inline audit, no collapsed lenses, no unpinned fallback.** Fall back to parallel `Task` dispatch ONLY if Workflow is unavailable (bare subagents inherit session effort unpinned).

**Lens composition (conditional on `PLAN_SHAPE`).** The report header MUST state the shape, the lens set, and why any default lens was omitted.

**`PLAN_SHAPE == code` — default set: the four original lenses plus `plc-evidence-grounding`.** **Omit `plc-pattern-fit` AND `plc-architecture-quality`** only when the plan proposes ZERO new types, ZERO new files (renames excluded), ZERO new exports/authored fields/scene nodes, and no new Jmodot code — pure retirement/rename plans. A plan reusing a family or restructuring an export surface is not a mechanical-edit plan. `plc-memory-alignment` and `plc-test-readiness` are never omitted from a `code` plan.

**`PLAN_SHAPE == meta` — swap the three C#-calibrated lenses for the harness set.** Run `plc-memory-alignment` unchanged — process and tool-routing gotchas are the densest memory cluster and bind harness work hardest — plus:

| Lens | Replaces | Pin |
|---|---|---|
| `plc-instruction-quality` | `plc-architecture-quality` | `opus·low`; `opus·medium` when the plan edits an always-loaded surface |
| `plc-doctrine-consistency` | `plc-pattern-fit` | `opus·low`; `opus·medium` when the plan edits an always-loaded surface |

`plc-test-readiness` is **omitted** for `meta` — Hybrid TDD's domain split has no jurisdiction over markdown. Verification is still audited: `plc-instruction-quality` owns *"does the plan state how each edit is verified"* (hook compiles and fires, script runs both paths, cited path resolves, battery still passes).

*Always-loaded surface* = `CLAUDE.md`, `.claude/auto-memory/MEMORY.md`, any `rules/*.md` whose `paths:` glob the plan's own edits would trigger, or a skill `description:`. These escalate because their blast radius is every future session and a contradiction there cannot be caught downstream.

**`plc-evidence-grounding` runs on BOTH shapes** — the one universal addition.

**Parallel dispatch:** the engine runs selected lenses concurrently and appends its read-only/single-flight guard to each. **Single-flight covers only expensive/stateful ops** — csharp-ls and test runs — which the orchestrator resolves once (1f) and injects; agents never re-run those. Cheap independent investigation is required, not forbidden: `plc-memory-alignment` MUST run its OWN `semantic-search` over `.claude/auto-memory` and Read the real files, and every lens may Read/Grep freely.

### Dispatch doctrine — seed, don't scope

Pre-loaded CONTEXT *orients* agents, NEVER *caps* them — an agent handed only the orchestrator's conclusions inherits its blind spots.

- **Seed, don't cap.** `MEMORY_HITS` (1d) and any gotcha list are a FLOOR for `plc-memory-alignment`. Mandate it to search auto-memory itself across the plan's domains and surface anything beyond the seed; a closed checklist caps discovery at the orchestrator's recall.
- **Orient, don't conclude.** Inject *facts* (surfaces, sibling counts, structure), never *conclusions* ("this is redundant", a pre-decided verdict). Conclusions invite confirmation bias.
- **Verify, don't trust.** Label every injected codebase fact as a claim to confirm first-party. Orchestrator facts can be stale or extrapolated, and an agent that trusts them propagates the error; the best refuting findings come from a lens Reading the real file (reinforces *Evidence-quoting for refuting claims*, Constraints).

### Agent Templates

Resolve the lens set from the gating below, then fetch **only those** bodies:
`python3 .claude/tools/lens.py get --shared <KEY> [<KEY> ...]`.

**Never `Read` [`plan_check_agents.md`](agents/plan_check_agents.md) whole** (~40KB; a guard blocks it). The two `meta`-only lenses are 11.4KB that a `code` plan never dispatches, and `plc-pattern-fit` + `plc-architecture-quality` are 9KB a `meta` plan never dispatches — every whole-file read pays for one of those pairs. `lens.py index plan_check` lists key, model, gate and size if you need to confirm the roster.

Both shapes:
- `plc-memory-alignment` (sonnet·medium; **opus·medium** when architecturally loaded — new abstractions, framework-boundary changes, 2+ subsystem reach; the raise requires `args.justification`) — Memory + known-failure-mode cross-check + step-ordering-hazard scan
- `plc-evidence-grounding` (opus·low, never escalated) — are load-bearing factual claims measured, or asserted from one observation

`PLAN_SHAPE == code` only:
- `plc-pattern-fit` (opus·low; **opus·medium** when architecturally loaded) — existing-abstraction discovery + framework boundary + structure rules
- `plc-architecture-quality` (opus·low; **opus·high** when architecturally loaded) — axis-nesting, authored-surface coherence, designer ergonomics, ownership seams; conditionally omitted per lens composition
- `plc-test-readiness` (sonnet·medium) — test-first executability under Hybrid TDD (detect-and-report only)

`PLAN_SHAPE == meta` only:
- `plc-instruction-quality` (opus·low; **opus·medium** on an always-loaded surface) — the `instruction_quality` rubric + verification-readiness
- `plc-doctrine-consistency` (opus·low; **opus·medium** on an always-loaded surface) — contradiction against live doctrine + inbound-reference rot

### Shared CONTEXT Block

One `CONTEXT` string injected into every agent prompt. It MUST contain:

1. **Plan text** (`PLAN_TEXT`, 1a) — inline the FULL text in the CONTEXT file. Fanned agents hit intermittent empties on *searches* (`gotcha_workflow_fanout_search_false_absence`); one Read of the context file with the engine's retry-once instruction is reliable, and the file keeps `args` small (`gotcha_workflow_args_generation_fidelity`).
2. **Inferred domains** (`INFERRED_DOMAINS`, 1c)
3. **Memory Hits** (`MEMORY_HITS`, 1d) — entity/file names + brief content, labelled a **search seed, not the checklist**.
4. **Known Failure Modes** (`KNOWN_FAILURE_MODES`, 1e) — full catalog text
5. **Symbol References** (`SYMBOL_REFS`, 1f) — per-symbol existing-sibling counts
6. **Support skills** (`architecture_philosophy/SKILL.md` + `structure_rules.md`, 1g) — for plc-pattern-fit and plc-architecture-quality; omittable from plc-memory-alignment's CONTEXT to save tokens. For plc-architecture-quality also inline `rules/scene_authoring.md` §Scene anatomy + `rules/design_litmus.md` whole.
6b. **Neighbour families** (`NEIGHBOUR_FAMILIES`, 1f export sweep) — for plc-pattern-fit and plc-architecture-quality; labelled unverified.
7. **Hybrid TDD domain split** (Logic = strict TDD with concrete `[TestCase]` names; Gameplay = integration + inspection) — plc-test-readiness only; a one-paragraph summary of CLAUDE.md *Development Philosophy* suffices.
8. **Finding Schema reference:** `/.claude/commands/agents/orchestrator_action_protocol.md`

`PLAN_SHAPE == meta` skips 5, 6, 6b, 7 (no referent) and adds:

9. **`PLAN_SHAPE` and the touched-path list**, plus whether any path is an always-loaded surface — both meta lenses branch on it.
10. **`skills/instruction_quality/SKILL.md`** — inline WHOLE for `plc-instruction-quality`; it is that lens's rubric.
11. **Live text of every doctrine surface the plan edits** — for `plc-doctrine-consistency`, so contradiction is judged against what is really there, not the plan's description of it. Same *Verify, don't trust* label as `SYMBOL_REFS`.

---

## Phase 3: Consolidate & Report

Follow the **Orchestrator Action Protocol** in [`orchestrator_action_protocol.md`](agents/orchestrator_action_protocol.md):

1. **Merge & deduplicate** findings across lenses.
2. **Sort:** critical first, then FIX → ASK → PLAN, then bug → rule → improvement.
3. **Present unified report:**

```
╔══════════════════════════════════════════════════════╗
║          PLAN CHECK — [DATE]                          ║
╠══════════════════════════════════════════════════════╣
║ Plan source:        [inline | file path]              ║
║ Shape:              [code | meta]  [detected|--flag]  ║
║ Lenses run:         [keys]  (omitted: [keys] — why)   ║
║ Inferred domains:   [comma-separated list]            ║
║ Symbol refs:        [N types]  |  Inbound refs: [N]   ║
║ Findings:           FIX:N  ASK:M  PLAN:K              ║
║ Critical:           [Y/N — count of critical:true]    ║
╚══════════════════════════════════════════════════════╝
```

`Shape` names the detected value AND whether `--meta`/`--code` overrode it. `Symbol refs` on a `code` plan, `Inbound refs` on a `meta` one. State the lens set: a reader who cannot see which lenses ran reads a short finding list as a clean bill of health rather than narrow coverage.

Then:
- **Scope Coverage** subsection — orphan requirements / orphan steps from 1b (if any).
- **Findings** grouped by tier per the Action Protocol's Step 2 format.
- **Cross-reference to /session_audit Phase 1.5** (only if the plan deletes/replaces files):
  > Heads-up: this plan deletes/replaces `<file list>`. Pre-enumerate the public surface (Exports, lifecycle hooks, signal subscriptions, BB writes, side effects) of the deleted files in your plan now. Phase 1.5 of `/session_audit` will verify your replacement reproduces every item — surfaces missed here become MERGE-BLOCKER findings later.

### Verdict

| Verdict | Criteria |
|---------|----------|
| **APPROVE** | 0 critical findings, ≤2 total findings, no orphan requirements |
| **APPROVE WITH NOTES** | 0 critical findings, 3+ findings (all addressable in revision) |
| **REVISE PLAN** | 1+ critical findings, OR orphan requirements present, OR a Logic-domain change with no tests-first (critical per `plc-test-readiness`), OR ASK findings the user can't resolve without changing the plan |

> **Test-first is a hard criterion.** A Logic-domain change not gated with a named failing test FIRST is critical → **REVISE PLAN**, no carve-out. Gameplay-domain work without an ISceneRunner plan is critical *unless* the plan explicitly flags it subjective ("feel/juice — manual playtest").

> **Critical architecture findings revise-and-reconverge — capped at 2 dispatched rounds.** A `critical` from `plc-architecture-quality` or `plc-pattern-fit` is answered by REVISING THE PLAN and re-running that lens, never by annotating a disposition and proceeding.
> - **Round 2 re-runs are delta-scoped**: only the lenses that raised criticals, prompted with the revision plus round-1's verified evidence quotes passed as trusted — re-deriving them is the main token sink.
> - **Convergence confirmation is orchestrator-inline** whenever the revision is a verbatim fold of lens-authored fix text plus user-ruled forks: read the revised sections against the finding list yourself. Dispatch a verifier agent ONLY when the revision introduces new design the lenses have not seen.
> - **Hard cap: 2 dispatched rounds.** Still divergent after 2 → escalate to the user (disputed on evidence, or a genuine taste fork). Residual risk is covered downstream by TDD RED verification, executor report-don't-adapt, and `/regression_gate`.

---

## Phase 4: Execute Actions

Follow the Action Protocol's Step 4. FIX findings rewrite the plan text (not code), so verification is "plan now reads as updated." ASK findings produce the user's design choices, applied to the plan. PLAN findings mean the plan needs a re-design pass before implementation.

**Confirmation prompt** (after the report):
> "Ready to revise the plan? I'll apply the N FIX text-edits, then walk through M ASK items for your input, then we'll discuss K PLAN items if any. After revision, the plan goes back to you for approval before implementation begins."

---

## Constraints

- **Read-only by default.** No code edits. The only file potentially modified is the plan file, and only with explicit per-finding approval.
- **Pre-execution stance.** Findings are about plan content, not existing code.
- **Never run this against a plan you are concurrently executing.** The lenses read the live tree, so landed work reads as a stale plan claim and is reported `critical`/`FIX` — a lens cannot distinguish "already done" from "wrong", and the artifacts crowd out genuine criticals. Finish the audit before executing, or re-scope the plan text to what remains.
- **Time-bounded.** Full audit (spawn → consolidate) under 5 minutes for plans <2000 words; larger may exceed.
- **Cloud compatible.** Grep fallback for csharp-ls; no Godot MCP / Obsidian MCP dependencies.
- **MANDATORY Workflow dispatch through `review_fanout.js`** — lens set per Phase 2's shape-conditional composition (5 on a default `code` plan, 4 on a `meta` one), pins per Phase 2. The engine is lens-agnostic: it takes whatever `args.agents` it is handed, so adding a shape means composing a different list, never editing the engine. No inline run; no collapse into one generic agent; bare `Task` only as Workflow-unavailable fallback.
- **Evidence-quoting for refuting claims.** Any finding that REFUTES the plan on empirical grounds ("this type already exists / file missing / already refactored") must quote the raw tool output (grep line, read excerpt). The orchestrator first-party-verifies at least one quote before the finding counts — agents fabricate confident file-state claims (`feedback_delegate_output_trust`); paraphrase survives fabrication, verbatim output rarely does.
- **Detect-and-report only for `plc-test-readiness` + the Phase-1b DoD/stub scan.** They surface findings; they never emit auto-applicable `old`/`new` edits. Test content and Definition-of-Done are scope decisions — a downstream auto-apply loop (`/part_drive`) must never silently fill in scope from them.

---

## When to run (suggested)

- Right after the plan file is drafted and BEFORE presenting it for approval, when the litmus above triggers.
- Before user approval of any plan involving cross-domain refactors, new abstractions, or deletions.
- NOT wired into `/session_end` or `/regression_gate` — those are post-implementation.
- NOT auto-fired from SessionStart or UserPromptSubmit hooks — too noisy; `plan_memory_reminder.py` covers passive enforcement.
