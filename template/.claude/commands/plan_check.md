---
allowed-tools: Bash(git ls-files:*), Bash(grep:*), Glob, Grep, Read, Task
description: Pre-execution audit of a proposed plan via 3 parallel subagents (Memory gotchas + existing-abstraction discovery + test-readiness). Invoke when the user asks to "plan check", "check this plan", "audit the plan", or "verify the plan" after a plan proposal. Advisory; does NOT block. SKIP under the litmus (≤2 files, no new types, no deletions) — trust the planner.
---

Audit a proposed plan **before** implementation begins. Surfaces (a) memorialized failure modes the plan walks into, (b) existing PP abstractions the plan would parallel rather than extend, and (c) whether the plan is test-first executable under Hybrid TDD. Delegates to 3 specialized subagents running in parallel, then consolidates findings.

> **Coverage note.** The first two lenses are *project-alignment + memory*; `plc-test-readiness` adds the *test-first executability* dimension, and Phase 1b adds *Definition-of-Done completeness*, *stub/TODO scan*, and *cross-Part dependency soundness* (Claude-side). Together these broaden the audit past "does this fit our codebase/rules" into "can a downstream executor actually drive this RED→GREEN and finish it."

**This command is advisory — it does NOT block execution.** Findings are scope/approach corrections, not failures.

## When to invoke

User-requested after a plan proposal (auto-invokable on the trigger phrases in the frontmatter). Worth running when **any** of the following hold:
- Plan touches **3+ files**
- Plan introduces a **new type, folder, or top-level concept**
- Plan refactors a domain that already has a **2+ subclass family** (e.g., StatusEffect, SpellEffect, StateBase, IBlackboardProvider, ISpell, SpellBehavior)
- Plan involves **deleting or replacing existing files** (so `/session_audit` Phase 1.5 retired-code parity check has a pre-enumerated surface to verify against later)

Below that, trust the planner. Routine plans are covered passively by the `plan_memory_reminder.py` PostToolUse hook (which reminds about Memory + Skills on every ExitPlanMode without spawning agents).

## Composition with other audits

| Lifecycle stage | Tool | What it covers |
|---|---|---|
| Plan-entry (before plan draft) | `/plan_part` | Design surface load (verbatim) + codebase drift classification + macro→`arch-rework` kick |
| Plan-time (after plan draft) | `/plan_check` | Memory gotchas + ordering hazards + existing-abstraction discovery + structure rules + test-first executability + DoD/stub/cross-Part-dep completeness |
| Post-implementation (before commit) | `/session_audit` Phase 1.5 (MERGE-BLOCKER tier) | Stub markers + retired-code parity diff against deleted files |
| Post-implementation (3 reviewer lenses) | `/session_audit` Phases 2–3 | Code-quality / robustness / testability rubric findings |

`/plan_check` is **complementary** to `/session_audit` Phase 1.5 — Phase 1.5 verifies *retired surface was reproduced* against committed code; `/plan_check` verifies *proposed surface covers requirements* before code exists. The orchestrator output explicitly cross-references Phase 1.5 for plans involving deletion/replacement.

It is also **downstream** of `/plan_part` — `/plan_part` briefs Plan Mode with the verbatim design surface + codebase drift findings *before* the plan is drafted (and HARD-STOPS on macro drift); `/plan_check` audits the resulting draft for Memory-gotcha walks and abstraction-parallel mistakes. The two compose: `/plan_part` ensures the agent enters Plan Mode with the right context and gates; `/plan_check` ensures the plan that comes out doesn't walk into a known landmine.

---

## Phase 1: Scope & Load

### 1a. Read the plan

Argument forms:
- `/plan_check <inline plan text>` — plan text passed directly
- `/plan_check @<filepath>` — plan text read from file (e.g., `@.claude/plans/foo.md`)
- `/plan_check` (no argument) — read the most-recently-modified `.md` under `~/.claude/plans/`

Store the plan text as `PLAN_TEXT` for downstream phases.

### 1b. Inline scope-coverage check (orchestrator, no agent)

Parse the plan for stated requirements vs. plan steps. Heuristics:
- Look for sections labelled "Requirements", "Goals", "Must do", "Acceptance Criteria", "Scope".
- Look for plan steps in numbered/bulleted lists, "Implementation", "Steps", "Phases".
- For each requirement, attempt to identify which step(s) address it.
- Flag any requirement with no addressing step (orphan requirement) and any step that doesn't trace to a requirement (orphan step).
- Flag **internally contradictory directives** — two steps that prescribe opposite actions on the same target (e.g. one section says "add the pause binding to each input profile", another says "pause is global — NOT added to any profile"). Plans that evolved across design iterations leave stale sections contradicting the current design; requirement↔step tracing does NOT catch step↔step contradiction, so scan for it explicitly.
- **Definition-of-Done completeness** — does the plan name its *closing* steps? A plan touching `.cs` that doesn't name `/regression_gate` before commit, doesn't add `<summary>` doc-coverage for any new `[Export]`, or (for a roadmap Part) doesn't end with the `/update_roadmap` state flip is incomplete. Flag the missing closing step. (Mirrors `/pr_ready` at plan-time — the "done but not actually done" class.)
- **Stub / deferral marker scan** — grep the plan text for `TODO|FIXME|deferred|defer to|follow-up|stub|placeholder|later pass|out of scope (then used anyway)`. Any marker on in-scope work is a plan-time catch of the parity merge-blocker class (`feedback_refactor_parity_audit`) — surface it before it ships as a `/session_audit` Phase-1.5 finding.
- **Cross-Part dependency soundness** — when the plan is for a roadmap Part, check each artifact it *assumes already exists* (a symbol, scene node, autoload, or `.tres` "provided by Part N-1"): is that deliverable actually in a prior Part's Definition-of-Done (or already shipped)? A plan built on a phantom prior-Part output passes the other lenses and still stalls the executor. Flag any assumed-but-unprovided dependency. (This is the #1 executor-stall cause; it's distinct from intra-plan orphan-reqs and from ordering hazards.)

These are string/structure-matching exercises over the pushed plan + roadmap state — they do not earn a subagent. Surface findings in the orchestrator's final report under a "Scope Coverage" subsection.

### 1c. Domain inference

Infer affected domains by case-insensitive keyword matching against `PLAN_TEXT`. Use the same domain table as `plan_memory_reminder.py` (mirrors CLAUDE.md "Proactive Context Loading" table). Build `INFERRED_DOMAINS` list.

If `INFERRED_DOMAINS` is empty, abort with: "Plan inference matched no PP domains — `/plan_check` has no useful contribution. Proceed with the plan as authored."

### 1d. Load auto-memory gotchas

For each domain in `INFERRED_DOMAINS`, run the corresponding auto-memory single-keyword search (per CLAUDE.md "Search Strategy"). Concatenate hits into `MEMORY_HITS` (preserve entity/file names for citation in findings).

**Ordering-hazard subset (feeds `plc-memory-alignment`).** Beyond per-domain gotchas, always include the step-*ordering* gotchas in `MEMORY_HITS` so the memory-alignment lens can check the plan's step *sequence*, not just individual steps: autoload subscription order (`gotcha_autoload_to_autoload_subscription_order`), `OnExit` clobbering a consumer's `OnEnter` read (`arch_rule_onexit_must_not_clobber_consumer_onenter`), init-timing (spell spawn pipeline), and spawn-marker-inside-trigger-volume. A plan whose steps are individually fine but ordered to walk into one of these is the target.

### 1e. Load known-failure-mode catalog

Read [`commands/checklists/known_failure_modes.md`](checklists/known_failure_modes.md) in full. Inject as `KNOWN_FAILURE_MODES` into CONTEXT. This is one of the only consumers that loads it (the catalog is on-demand, not universal).

### 1f. Symbol references for proposed types

For every type/class/interface name the plan mentions:
- **Local sessions**: run LSP `findReferences` on each named symbol via the csharp-ls plugin.
- **Cloud sessions** (csharp-ls disabled): fall back to `Grep("class\\s+<TypeName>|interface\\s+<TypeName>|: <TypeName>", glob="**/*.cs")`.

Capture the results as `SYMBOL_REFS`. Specifically: for each proposed *new* type, the count of existing siblings in the same namespace/folder is the load-bearing signal for `plc-pattern-fit`.

### 1g. Load support skills

These are pre-loaded into agent CONTEXT, not freshly read at agent time:
- `architecture_philosophy/SKILL.md` (for plc-pattern-fit)
- `architecture_philosophy/structure_rules.md` (for plc-pattern-fit)

---

## Phase 2: Launch Plan-Check Sub-Agents

**CRITICAL — Sub-agent delegation is MANDATORY.** Spawn each agent as a separate `Task` subagent with `subagent_type: "general-purpose"` and the model specified in the template. Do NOT perform the audit inline.

**Parallel dispatch:** Spawn all three agents in a SINGLE message with 3 Task tool calls. Do NOT use `run_in_background` — let them execute in parallel and return together. Each agent inspects the pushed CONTEXT only; none runs tests or the csharp-ls LSP (single-flight — the orchestrator already resolved symbol refs in Phase 1f and injects them).

### Agent Templates

Use the templates in [`plan_check_agents.md`](agents/plan_check_agents.md):
- `plc-memory-alignment` (opus) — Memory + known-failure-mode cross-check + step-ordering-hazard scan
- `plc-pattern-fit` (opus) — Existing-abstraction discovery + framework-boundary + structure rules
- `plc-test-readiness` (sonnet) — Test-first executability under Hybrid TDD (detect-and-report only)

### Shared CONTEXT Block

Assemble a single `CONTEXT` string injected into both agent prompts. It MUST contain:

1. **Plan text** (`PLAN_TEXT` from 1a)
2. **Inferred domains** (`INFERRED_DOMAINS` from 1c)
3. **Memory Hits** (`MEMORY_HITS` from 1d) — entity/file names + brief content
4. **Known Failure Modes** (`KNOWN_FAILURE_MODES` from 1e) — full catalog text
5. **Symbol References** (`SYMBOL_REFS` from 1f) — per-symbol existing-sibling counts
6. **Support skills** (`architecture_philosophy/SKILL.md` + `structure_rules.md` from 1g) — only for plc-pattern-fit; can be omitted from plc-memory-alignment's CONTEXT to save tokens
7. **Hybrid TDD domain split** (Logic = strict TDD with concrete `[TestCase]` names; Gameplay = integration + inspection) — only for plc-test-readiness; a one-paragraph summary from CLAUDE.md *Development Philosophy* suffices
8. **Finding Schema reference:** point to `/.claude/commands/agents/orchestrator_action_protocol.md`

---

## Phase 3: Consolidate & Report

After BOTH subagents return, follow the **Orchestrator Action Protocol** defined in [`orchestrator_action_protocol.md`](agents/orchestrator_action_protocol.md):

1. **Merge & deduplicate** findings across both agents.
2. **Sort:** critical first, then FIX → ASK → PLAN, then bug → rule → improvement.
3. **Present unified report** in this format:

```
╔══════════════════════════════════════════════════════╗
║          PLAN CHECK — [DATE]                          ║
╠══════════════════════════════════════════════════════╣
║ Plan source:        [inline | file path]              ║
║ Inferred domains:   [comma-separated list]            ║
║ Symbol refs:        [N existing types referenced]     ║
║ Findings:           FIX:N  ASK:M  PLAN:K              ║
║ Critical:           [Y/N — count of critical:true]    ║
╚══════════════════════════════════════════════════════╝
```

Then:
- **Scope Coverage** subsection — orphan requirements / orphan steps from Phase 1b (if any).
- **Findings** grouped by tier per the Action Protocol's Step 2 format.
- **Cross-reference to /session_audit Phase 1.5** (only if the plan deletes/replaces files):
  > Heads-up: this plan deletes/replaces `<file list>`. Pre-enumerate the public surface (Exports, lifecycle hooks, signal subscriptions, BB writes, side effects) of the deleted files in your plan now. Phase 1.5 of `/session_audit` will verify your replacement reproduces every item — surfaces missed here become MERGE-BLOCKER findings later.

### Verdict

| Verdict | Criteria |
|---------|----------|
| **APPROVE** | 0 critical findings, ≤2 total findings, no orphan requirements |
| **APPROVE WITH NOTES** | 0 critical findings, 3+ findings (all addressable in revision) |
| **REVISE PLAN** | 1+ critical findings, OR orphan requirements present, OR a Logic-domain change with no tests-first (critical per `plc-test-readiness`), OR ASK findings the user can't resolve without changing the plan |

> **Test-first is a hard criterion.** A Logic-domain change the plan does not gate with a named failing test FIRST is a critical finding → **REVISE PLAN**, no carve-out (mirrors the TDD Logic-Domain rule). Gameplay-domain work without an ISceneRunner plan is critical *unless* the plan explicitly flags it subjective ("feel/juice — manual playtest").

---

## Phase 4: Execute Actions

Follow the Action Protocol's Step 4. Plan-check FIX findings rewrite the plan text (not code), so verification is "plan now reads as updated." ASK findings produce the user's design choices; the plan is updated accordingly. PLAN findings indicate the plan needs a re-design pass before any implementation.

**Confirmation prompt** (after presenting the report):
> "Ready to revise the plan? I'll apply the N FIX text-edits, then walk through M ASK items for your input, then we'll discuss K PLAN items if any. After revision, the plan goes back to you for approval before implementation begins."

---

## Constraints

- **Read-only by default.** No code edits. The only file potentially modified is the plan file itself (and only with explicit per-finding approval).
- **Pre-execution stance.** This command runs BEFORE code is written. Findings are about plan content, not existing code.
- **Time-bounded.** Full audit (spawn → consolidate) under 5 minutes for typical plans (<2000 words). Larger plans may exceed.
- **Cloud compatible.** Uses Grep fallback for csharp-ls; no Godot MCP / Obsidian MCP dependencies.
- **MANDATORY 3-agent parallel dispatch.** Do not perform inline; do not collapse into one generic agent.
- **Detect-and-report only for `plc-test-readiness` + the Phase-1b DoD/stub scan.** They surface findings; they never emit auto-applicable `old`/`new` edits. Test content and Definition-of-Done are scope decisions — a downstream auto-apply loop (e.g. `/plan_drive`) must never be able to silently fill in scope from these findings.

---

## When to run (suggested)

- Right after drafting a plan in plan mode but BEFORE calling `ExitPlanMode`, when the litmus above triggers.
- Before user approval of any plan involving cross-domain refactors, new abstractions, or deletions.
- NOT wired into `/session_end` or `/regression_gate` — those are post-implementation.
- NOT auto-fired from the SessionStart or UserPromptSubmit hooks — too noisy. The `plan_memory_reminder.py` hook covers passive enforcement.
