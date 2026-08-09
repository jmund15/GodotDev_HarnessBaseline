---
allowed-tools: Bash(git ls-files:*), Bash(grep:*), Glob, Grep, Read, Write, Workflow, Task
description: Pre-execution audit of a proposed plan — production-code plans and harness/doctrine plans alike, lens set chosen by plan shape. Advisory except critical architecture findings (revise-and-reconverge). Invoke after a plan proposal on "plan check" / "check this plan" / "audit the plan" / "verify the plan". SKIP under the litmus (≤2 files, no new types/exports, no deletions) — trust the planner.
---

Audit a proposed plan **before** implementation begins, through a lens set chosen by what the plan actually touches (`PLAN_SHAPE`, Phase 1a). Delegates to specialized subagents running in parallel, then consolidates findings.

For a **code** plan: (a) memorialized failure modes the plan walks into, (b) existing {{PROJECT_NAME}} abstractions the plan would parallel rather than extend, (c) whether the architecture is ideal — axis-nesting, authored-surface coherence, designer ergonomics, ownership seams — and (d) test-first executability under Hybrid TDD.

For a **meta** (harness/doctrine) plan those axes have no referent, so three are swapped out: (a) memorialized failure modes still apply, plus (b) the `instruction_quality` rubric and whether each edit states how it is verified, and (c) contradiction against live doctrine and inbound-reference rot.

**Both shapes** additionally run *evidence-grounding* — whether the plan's load-bearing factual claims are measured or asserted. That lens is universal because the failure it catches is: a plan states a fact confidently, several files get written on top of it, and the fact turns out to have come from a single observation of a variable condition.

> **Coverage note.** Beyond the Phase-2 lenses, Phase 1b adds *Definition-of-Done completeness*, *stub/TODO scan*, and *cross-Part dependency soundness* (Claude-side) — together broadening the audit from "does this fit our codebase/rules" into "can a downstream executor actually drive this RED→GREEN and finish it."

**This command is advisory — it does NOT block execution.** Findings are scope/approach corrections, not failures.

## When to invoke

User-requested after a plan proposal (auto-invokable on the trigger phrases in the frontmatter). Worth running when **any** of the following hold:
- Plan touches **3+ files**
- Plan introduces a **new type, folder, or top-level concept**
- Plan adds a **new `[Export]`/authored field or behavior-selecting bool/enum** to an existing class — especially one whose folder/base carries a `*Strategy`/`*Config` sibling (a 2-file, scope-2 plan is still the canonical reuse failure)
- Plan adds a **subclass to an existing 2+ family**, or refactors a domain that has one (e.g., StatusEffect, SpellEffect, StateBase, IBlackboardProvider, ISpell, SpellBehavior)
- Plan involves **deleting or replacing existing files** (so `/session_audit` Phase 1.5 retired-code parity check has a pre-enumerated surface to verify against later)
- Plan edits an **always-loaded harness surface** (`CLAUDE.md`, `MEMORY.md`, a `paths:`-globbed rule, a skill `description:`), or changes doctrine across **3+ `.claude/` files** — the `meta` shape. Blast radius is every future session and there is no downstream gate: no `/regression_gate` (meta commits are exempt), no compiler, no test. Plan-time is the only gate these changes get.

Below that, trust the planner. Routine plans are covered passively by the `plan_memory_reminder.py` PostToolUse hook, which reminds about Memory + Skills without spawning agents. It fires on a `Write`/`Edit` to `.claude/plans/*.md` — the plan-file write is the trigger, so every drive command trips it when it lands the plan. **Coverage hole:** the hook emits nothing below a 50-word plan floor, and SKIP-eligible plans are the terse ones most likely to sit under it — so a below-litmus plan in a hook-only domain can get no domain-gotcha recall from anywhere.

## Composition with other audits

| Lifecycle stage | Tool | What it covers |
|---|---|---|
| Plan-entry (before plan draft) | `/plan_part` | Design surface load (verbatim) + codebase drift classification + macro→`arch-rework` kick |
| Plan-time (after plan draft) | `/plan_check` | Memory gotchas + ordering hazards + existing-abstraction discovery + structure rules + test-first executability + DoD/stub/cross-Part-dep completeness |
| Post-implementation (before commit) | `/session_audit` Phase 1.5 (MERGE-BLOCKER tier) | Stub markers + retired-code parity diff against deleted files |
| Post-implementation (3 reviewer lenses) | `/session_audit` Phases 2–3 | Code-quality / robustness / testability rubric findings |

`/plan_check` is **complementary** to `/session_audit` Phase 1.5 — Phase 1.5 verifies *retired surface was reproduced* against committed code; `/plan_check` verifies *proposed surface covers requirements* before code exists. The orchestrator output explicitly cross-references Phase 1.5 for plans involving deletion/replacement.

It is also **downstream** of `/plan_part` — `/plan_part` briefs the planning session with the verbatim design surface + codebase drift findings *before* the plan is drafted (and HARD-STOPS on macro drift); `/plan_check` audits the resulting draft for Memory-gotcha walks and abstraction-parallel mistakes. The two compose: `/plan_part` ensures the draft starts from the right context and gates; `/plan_check` ensures the plan that comes out doesn't walk into a known landmine.

---

## Phase 1: Scope & Load

### 1a. Read the plan

Argument forms:
- `/plan_check <inline plan text>` — plan text passed directly
- `/plan_check @<filepath>` — plan text read from file (e.g., `@.claude/plans/foo.md`)
- `/plan_check` (no argument) — read the most-recently-modified `.md` under the project-local `.claude/plans/` (where `part_drive` writes)

Any form additionally accepts `--meta` or `--code` to override shape detection (below). Strip the flag before storing the plan text.

Store the plan text as `PLAN_TEXT` for downstream phases.

**Prototype-grade detection.** If `PLAN_TEXT`'s work targets the throwaway tree — `prototypes/<slug>/` files or a `prototype/<slug>` branch — this is a prototype brief, not a plan: abort and route to the `prototype` skill (prototypes have no plan; `QUESTION.md` + `ANSWER.md` are their artifacts). A plan that *cites* `prototypes/<slug>/ANSWER.md` under `Constraints` is the real Part's plan — run the FULL gate.

**Shape classification.** Set `PLAN_SHAPE` — it selects the Phase 2 lens set, because the default lenses are calibrated for C# gameplay code and score a harness plan on axes it has none of (types, exports, scene nodes, TDD slices).

- `meta` — EVERY path the plan writes is agent-runtime or docs infrastructure (`.claude/**`, `docs/**`, `Docs/**`, Obsidian vault paths), AND the plan writes no `.cs`, `.tscn`, `.tres`, or `.csproj`. Harness doctrine, skills, commands, hooks, scripts, rules, memory files.
- `code` — anything else. **This is the default: a plan touching even one production file is `code`, however much harness work rides along.** Mixed plans stay `code` so the production surface is never audited by the lighter set.

Explicit overrides, both directions, for the cases detection gets wrong: `--meta` forces the meta set, `--code` forces the default set. An override is stated in the report header with the detected shape it replaced. A plan that *edits* harness files in service of a production change (adding a domain row because a new subsystem landed) is `code` — the production change is the subject.

### 1b. Inline scope-coverage check (orchestrator, no agent)

Parse the plan for stated requirements vs. plan steps. Heuristics:
- Look for sections labelled "Requirements", "Goals", "Must do", "Acceptance Criteria", "Scope".
- Look for plan steps in numbered/bulleted lists, "Implementation", "Steps", "Phases".
- For each requirement, attempt to identify which step(s) address it.
- Flag any requirement with no addressing step (orphan requirement) and any step that doesn't trace to a requirement (orphan step).
- Flag **internally contradictory directives** — two steps that prescribe opposite actions on the same target (e.g. one section says "add the pause binding to each input profile", another says "pause is global — NOT added to any profile"). Plans that evolved across design iterations leave stale sections contradicting the current design; requirement↔step tracing does NOT catch step↔step contradiction, so scan for it explicitly.
- **Definition-of-Done completeness** — does the plan name its *closing* steps? A plan touching `.cs` that doesn't name `/regression_gate` before commit, doesn't add `<summary>` doc-coverage for any new `[Export]`, or (for a roadmap Part) doesn't end with the `/update_roadmap` state flip is incomplete. Flag the missing closing step. (Mirrors `/pr_ready` at plan-time — the "done but not actually done" class.)
- **Stub / deferral marker scan** — grep the plan text for `TODO|FIXME|deferred|defer to|follow-up|stub|placeholder|later pass|out of scope (then used anyway)`. Any marker on in-scope work is a plan-time catch of the parity merge-blocker class (`feedback_refactor_parity_audit`) — surface it before it ships as a `/session_audit` Phase-1.5 finding.
- **Invention scan** — every element the plan introduces (type, `[Export]`, file, behavior, step) must trace to the design doc/brief the plan was drafted from, or to cited codebase evidence. An element grounded in neither is silent scope invented during planning: flag it, and require the plan either justify it explicitly at the point it appears or drop it. Distinct from orphan-step tracing above — that asks whether a step serves a stated requirement; this asks where the element came from at all.
- **Cross-Part dependency soundness** — when the plan is for a roadmap Part, check each artifact it *assumes already exists* (a symbol, scene node, autoload, or `.tres` "provided by Part N-1"): is that deliverable actually in a prior Part's Definition-of-Done (or already shipped)? A plan built on a phantom prior-Part output passes the other lenses and still stalls the executor. Flag any assumed-but-unprovided dependency. (This is the #1 executor-stall cause; it's distinct from intra-plan orphan-reqs and from ordering hazards.)

These are string/structure-matching exercises over the pushed plan + roadmap state — they do not earn a subagent. Surface findings in the orchestrator's final report under a "Scope Coverage" subsection.

### 1c. Domain inference

Infer affected domains by case-insensitive keyword matching against `PLAN_TEXT`. Use the same domain table as `plan_memory_reminder.py` (mirrors CLAUDE.md "Proactive Context Loading" table). Build `INFERRED_DOMAINS` list.

**The empty-domain abort applies to `code` plans only.** If `PLAN_SHAPE == code` and `INFERRED_DOMAINS` is empty, abort with: "Plan inference matched no {{PROJECT_NAME}} domains — `/plan_check` has no useful contribution. Proceed with the plan as authored."

A `meta` plan is EXPECTED to match no domain — the table maps gameplay subsystems, and harness work touches none of them. Aborting there is a false negative that silently withholds the audit from exactly the plans whose blast radius is every future session (measured 2026-08-09: a harness plan hit this abort, and the hand-rolled substitute is what caught a defect the default lenses would not have looked for). For `meta`, skip the abort and set `INFERRED_DOMAINS = ["Harness/Meta"]` so 1d has a search seed.

### 1d. Load auto-memory gotchas

For each domain in `INFERRED_DOMAINS`, run the corresponding auto-memory single-keyword search (per CLAUDE.md "Search Strategy"). Concatenate hits into `MEMORY_HITS` (preserve entity/file names for citation in findings). The `Harness/Meta` pseudo-domain has no row in that table — seed it by searching auto-memory for instruction-quality, tool-routing, harness-file, and process-discipline rules (`MEMORY.md`'s *Communication & process discipline*, *Tool routing & workflow*, and *Harness files* clusters are the standing hit set). This orchestrator search is a **seed/floor**, not the memory lens's full coverage — Phase 2's *Dispatch doctrine* mandates that lens to search auto-memory itself and exceed it.

**Ordering-hazard subset (feeds `plc-memory-alignment`).** Beyond per-domain gotchas, always include the step-*ordering* gotchas in `MEMORY_HITS` so the memory-alignment lens can check the plan's step *sequence*, not just individual steps: autoload subscription order (`gotcha_autoload_to_autoload_subscription_order`), `OnExit` clobbering a consumer's `OnEnter` read (`arch_rule_onexit_must_not_clobber_consumer_onenter`), init-timing (spell spawn pipeline), and spawn-marker-inside-trigger-volume. A plan whose steps are individually fine but ordered to walk into one of these is the target.

### 1e. Load known-failure-mode catalog

Read [`commands/checklists/known_failure_modes.md`](checklists/known_failure_modes.md) in full. Inject as `KNOWN_FAILURE_MODES` into CONTEXT. This is one of the only consumers that loads it (the catalog is on-demand, not universal).

### 1f. Symbol references for proposed types AND authored surfaces

**`PLAN_SHAPE == meta` replaces this step with the surface sweep in 1f-meta below** — a harness plan has no types, exports, or `.tres` corpus to sweep, and the lenses that consume `SYMBOL_REFS`/`NEIGHBOUR_FAMILIES` are not running.

Enumerate from the plan BOTH proposed types/classes/interfaces AND proposed `[Export]`s / authored fields / parameters / behavior flags (the plan's *Authored surfaces* section, when present, is the primary source). Then:
- **Local sessions**: run LSP `findReferences` on each named symbol via the csharp-ls plugin.
- **Cloud sessions** (csharp-ls disabled): fall back to `Grep("class\\s+<TypeName>|interface\\s+<TypeName>|: <TypeName>", glob="**/*.cs")`.

Capture the results as `SYMBOL_REFS`. Specifically: for each proposed *new* type, the count of existing siblings in the same namespace/folder is the load-bearing signal for `plc-pattern-fit`. **Existence sweep (mandatory per NEW-file/type row):** also semantic-search the proposed type by NAME and by CONCEPT (what it computes, not what it's called) across the repo incl. Jmodot — a live equivalent under a different path/shape is a REUSE finding, and two `[GlobalClass]` Resources sharing a simple name collide regardless of namespace. (Evidence: a twice-audited plan authored a parallel Resource while a tested framework twin existed; only a downstream dependency cross-check caught it.)

**Export-surface sweep (mandatory per NEW-export row):** for each proposed export/field/flag, semantic-search the described BEHAVIOR (what the knob selects, not its name) restricted to the target file's directory and its parent, plus the target class's base; and Grep the `.tres` corpus for an existing field already carrying that value. Capture hits as `NEIGHBOUR_FAMILIES` — labelled as unverified claims per *Verify, don't trust*. This is what catches a bool shadowing an existing `*Strategy` family and a value about to be authored in a second home.

### 1f-meta. Inbound-reference sweep (`PLAN_SHAPE == meta` only)

The meta analogue of the symbol sweep: a harness edit's blast radius is measured in *citations*, not call sites. For each surface the plan renames, moves, deletes, renumbers, or rewords, enumerate who points at it. Capture as `INBOUND_REFS`, labelled unverified per *Verify, don't trust*.

- **File path** — `Grep` the cited path across `.claude/` (commands, skills, rules, hooks, memory, tests) and the Obsidian vault.
- **Rule / lens / agent key / hook rule-name string** — a renamed identifier is the highest-rot class, because nothing errors: the old name simply stops matching and the check silently never fires again. Grep the literal old string.
- **Section anchor / `§N`** — inserting or removing a numbered section renumbers every one below it; grep for `§` citations of the target file.
- **Test fixtures and batteries** — `.claude/tests/`, routing batteries, hook fixtures. A fixture encoding the pre-revision behavior rewards the obsolete action and penalizes the corrected one (`instruction_quality` §4 *inbound-reference rot*).

Use the `Grep` tool, not raw `grep -r`: `.claude/worktrees/` holds whole extra checkouts of this repo whose hits are indistinguishable from real ones.

### 1g. Load support skills

These are pre-loaded into agent CONTEXT, not freshly read at agent time.

`PLAN_SHAPE == code`:
- `architecture_philosophy/SKILL.md` (for plc-pattern-fit)
- `architecture_philosophy/structure_rules.md` (for plc-pattern-fit)
- `.claude/generated/abstraction_families.md` — rows only, for plc-pattern-fit and plc-architecture-quality: pre-load the family rows whose family name or owning folder matches INFERRED_DOMAINS or the plan's touched paths (never the whole 300-family file), labelled as the existing-abstraction inventory the plan must be reconciled against.

`PLAN_SHAPE == meta` (none of the above — they have no referent):
- `skills/instruction_quality/SKILL.md` — WHOLE, for plc-instruction-quality.
- Live text of each doctrine surface the plan edits, for plc-doctrine-consistency (CONTEXT item 11).

---

## Phase 2: Launch Plan-Check Sub-Agents

**CRITICAL — Sub-agent delegation is MANDATORY, dispatched via Workflow.** Write the assembled CONTEXT block (Phase 1 outputs, full plan text inline) to a scratchpad file, then invoke the shared engine:
`Workflow({scriptPath: ".claude/workflows/review_fanout.js", args: {agents: [{key, prompt: <lens template text>, model, effort}], contextPrefixPath: <context file path>}})` — this command's instruction IS the Workflow authorization.

- **Per-lens pins (user directive 2026-08-03, refined same day):** the design-judgment lenses are opus-floored per `orchestration` §5 — sub-architectural plans run `plc-pattern-fit` + `plc-architecture-quality` at `opus·low` (measured: `opus·low` matched `sonnet·high` at 0.53× tokens); **architecturally-loaded plans (new abstractions, framework-boundary changes, 2+ subsystem reach) raise `plc-architecture-quality` to `opus·high`** (the one lens doing open-ended structural judgment) **and `plc-pattern-fit` + `plc-memory-alignment` to `opus·medium`** (anchored by the symbol inventory / memory seed even on loaded plans — high buys steps their anchored mandates don't need). `plc-test-readiness` stays `sonnet·medium` everywhere; `plc-memory-alignment` defaults `sonnet·medium` on sub-architectural plans. Raises pass `args.justification` naming the ambiguity — measured (P-D pin comparison, benchmark 2026-07-29): on such a plan `opus·high` lenses found 15 unique valid defects vs `sonnet·medium`'s 6, the memory and pattern-fit lenses carrying most of the gap.
- **`plc-evidence-grounding` pins `opus·low` on every plan, both shapes, never escalated.** Its mandate is closed — enumerate the plan's load-bearing factual assertions, check each against its cited backing — so effort buys steps a bounded enumeration does not need. It is the cheapest lens in the set by design, because it runs universally; a raise here is a recurring tax on every plan for no measured gain.
- **No inline audit, no collapsed lenses, no unpinned fallback.** Do NOT perform the audit inline; do NOT collapse the lenses into one generic agent; fall back to parallel `Task` dispatch ONLY if the Workflow tool is unavailable (bare subagents inherit session effort unpinned — the failure mode this conversion removes).

**Lens composition (conditional on `PLAN_SHAPE`).** The report header MUST state the shape, the lens set, and why any default lens was omitted.

**`PLAN_SHAPE == code` — the default set: the four original lenses plus `plc-evidence-grounding`.** **Omit `plc-pattern-fit` AND `plc-architecture-quality`** only when the plan proposes ZERO new types, ZERO new files (renames excluded), ZERO new exports/authored fields/scene nodes, and no new Jmodot code — i.e. pure retirement/rename plans; a plan that reuses a family or restructures an export surface is NOT a "mechanical-edit plan". `plc-memory-alignment` and `plc-test-readiness` are never omitted from a `code` plan.

**`PLAN_SHAPE == meta` — swap the three C#-calibrated lenses for the harness set.** Run `plc-memory-alignment` (unchanged — process and tool-routing gotchas are the densest memory cluster there is, and they bind harness work hardest), plus:

| Lens | Replaces | Pin |
|---|---|---|
| `plc-instruction-quality` | `plc-architecture-quality` | `opus·low`; `opus·medium` when the plan edits an always-loaded surface |
| `plc-doctrine-consistency` | `plc-pattern-fit` | `opus·low`; `opus·medium` when the plan edits an always-loaded surface |

`plc-test-readiness` is **omitted** for `meta` — Hybrid TDD's domain split has no jurisdiction over markdown, and scoring a doctrine edit against RED→GREEN produces noise, not findings. Verification does not go unaudited: `plc-instruction-quality` owns *"does the plan state how each edit is verified"* (hook compiles and fires, script runs both paths, cited path resolves, battery still passes), which is the meta analogue and the thing that actually catches an unverifiable harness change.

*Always-loaded surface* = `CLAUDE.md`, `.claude/auto-memory/MEMORY.md`, any `rules/*.md` with a `paths:` glob that the plan's own edits would trigger, or a skill `description:`. These escalate because their blast radius is every future session and a contradiction there cannot be caught downstream.

**`plc-evidence-grounding` runs on BOTH shapes — it is the one universal addition.** See its pin note below.

**Parallel dispatch:** the engine runs the selected lenses concurrently and appends its read-only/single-flight guard to every lens. **Single-flight applies ONLY to expensive/stateful ops** — the csharp-ls LSP and test runs — which the orchestrator resolves once (Phase 1f) and injects; agents never re-run those. It does NOT forbid cheap independent investigation: the `plc-memory-alignment` lens MUST run its OWN `semantic-search` over `.claude/auto-memory` and Read the real files to verify, and every lens may Read/Grep freely. The pushed CONTEXT is a starting seed, never the ceiling.

### Dispatch doctrine — seed, don't scope

The orchestrator's pre-loaded CONTEXT exists to *orient* agents efficiently, NEVER to *cap* what they investigate. An agent handed only the orchestrator's conclusions inherits the orchestrator's blind spots — the exact failure these lenses exist to catch. Three rules:

- **Seed, don't cap.** `MEMORY_HITS` (Phase 1d) and any gotcha list are a FLOOR for `plc-memory-alignment`, not the set to check. Mandate it to search auto-memory itself across the plan's domains and surface anything beyond the seed. A closed checklist caps discovery at the orchestrator's recall.
- **Orient, don't conclude.** Inject *facts* (what the code IS — surfaces, sibling counts, structure), never *conclusions* (what's right/wrong, "this is redundant", a pre-decided verdict). Conclusions invite confirmation bias; the lens's value is independent judgment.
- **Verify, don't trust.** Label every injected codebase fact as a claim to confirm first-party, not ground truth. Orchestrator facts can be stale or extrapolated (a maiden run asserted a global "no occlusion system" from a 6-file sample — false); an agent that trusts them propagates the error. The best refuting findings come from a lens Reading the real file instead of believing the brief (reinforces *Evidence-quoting for refuting claims*, Constraints).

### Agent Templates

Use the templates in [`plan_check_agents.md`](agents/plan_check_agents.md).

Both shapes:
- `plc-memory-alignment` (sonnet·medium; **opus·medium** when the plan is architecturally loaded — new abstractions, framework-boundary changes, 2+ subsystem reach; the raise requires `args.justification`) — Memory + known-failure-mode cross-check + step-ordering-hazard scan
- `plc-evidence-grounding` (opus·low, never escalated) — are the plan's load-bearing factual claims measured, or asserted from one observation

`PLAN_SHAPE == code` only:
- `plc-pattern-fit` (opus·low; **opus·medium** when architecturally loaded) — Existing-abstraction discovery + framework-boundary + structure rules
- `plc-architecture-quality` (opus·low; **opus·high** when architecturally loaded) — axis-nesting, authored-surface coherence, designer ergonomics, ownership seams (the ideal-architecture lens; conditionally omitted per lens-composition above)
- `plc-test-readiness` (sonnet·medium) — Test-first executability under Hybrid TDD (detect-and-report only)

`PLAN_SHAPE == meta` only:
- `plc-instruction-quality` (opus·low; **opus·medium** on an always-loaded surface) — the `instruction_quality` rubric + verification-readiness
- `plc-doctrine-consistency` (opus·low; **opus·medium** on an always-loaded surface) — contradiction against live doctrine + inbound-reference rot

### Shared CONTEXT Block

Assemble a single `CONTEXT` string injected into both agent prompts. It MUST contain:

1. **Plan text** (`PLAN_TEXT` from 1a) — inline the FULL text in the CONTEXT file. Fanned workflow agents hit intermittent empties on *searches* (`gotcha_workflow_fanout_search_false_absence`); a single Read of the context file with the engine's retry-once instruction is reliable, and the file keeps `args` small (`gotcha_workflow_args_generation_fidelity`).
2. **Inferred domains** (`INFERRED_DOMAINS` from 1c)
3. **Memory Hits** (`MEMORY_HITS` from 1d) — entity/file names + brief content. Label as a **search seed, not the checklist** — the memory lens searches auto-memory itself and goes beyond (per *Dispatch doctrine*).
4. **Known Failure Modes** (`KNOWN_FAILURE_MODES` from 1e) — full catalog text
5. **Symbol References** (`SYMBOL_REFS` from 1f) — per-symbol existing-sibling counts
6. **Support skills** (`architecture_philosophy/SKILL.md` + `structure_rules.md` from 1g) — for plc-pattern-fit and plc-architecture-quality; can be omitted from plc-memory-alignment's CONTEXT to save tokens. For plc-architecture-quality additionally inline `rules/scene_authoring.md` §Scene anatomy + `rules/design_litmus.md` (small files — include whole).
6b. **Neighbour families** (`NEIGHBOUR_FAMILIES` from 1f export-surface sweep) — for plc-pattern-fit and plc-architecture-quality; labelled unverified.
7. **Hybrid TDD domain split** (Logic = strict TDD with concrete `[TestCase]` names; Gameplay = integration + inspection) — only for plc-test-readiness; a one-paragraph summary from CLAUDE.md *Development Philosophy* suffices
8. **Finding Schema reference:** point to `/.claude/commands/agents/orchestrator_action_protocol.md`

`PLAN_SHAPE == meta` additionally (and skips 5, 6, 6b, 7 — the symbol/family/TDD material has no referent):

9. **`PLAN_SHAPE` and the touched-path list**, plus whether any path is an always-loaded surface — both meta lenses branch on it.
10. **`skills/instruction_quality/SKILL.md`** — inline WHOLE for `plc-instruction-quality`. It is that lens's rubric; a paraphrase would re-derive it worse.
11. **Live text of every doctrine surface the plan edits** — for `plc-doctrine-consistency`, the CURRENT content of each target file, so contradiction is judged against what is really there rather than against the plan's description of it. This is the meta analogue of `SYMBOL_REFS`: same *Verify, don't trust* label, same reason.

---

## Phase 3: Consolidate & Report

After the dispatched lenses return, follow the **Orchestrator Action Protocol** defined in [`orchestrator_action_protocol.md`](agents/orchestrator_action_protocol.md):

1. **Merge & deduplicate** findings across the lenses.
2. **Sort:** critical first, then FIX → ASK → PLAN, then bug → rule → improvement.
3. **Present unified report** in this format:

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

`Shape` names the detected value AND whether an explicit `--meta`/`--code` overrode it. `Symbol refs` on a `code` plan, `Inbound refs` on a `meta` one. Stating the lens set is not decoration: a reader who cannot see which lenses ran will read a short finding list as a clean bill of health rather than as narrow coverage.

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

> **Critical architecture findings revise-and-reconverge — capped at 2 dispatched rounds.** A `critical` finding from `plc-architecture-quality` or `plc-pattern-fit` is answered by REVISING THE PLAN and re-running that lens — never by annotating a disposition and proceeding (self-adjudicated "acceptable for now" is the accretion pattern this lens exists to kill). Round policy (measured 2026-08-02: rounds yielded 41→17→0; the round-3 verifier burned 189k tokens confirming no new judgment):
> - **Round 2 re-runs are delta-scoped**: only the lenses that raised criticals, prompted with the revision + round-1's verified evidence quotes passed as trusted (they were first-party-verified once; re-deriving them is the main token sink).
> - **Convergence confirmation is orchestrator-inline** whenever the revision is a verbatim fold of lens-authored fix text plus user-ruled forks — read the revised sections against the finding list yourself. Dispatch a verifier agent ONLY when the revision introduces new design the lenses haven't seen.
> - **Hard cap: 2 dispatched rounds.** Still divergent after 2 → escalate to the user (disputed on evidence, or a genuine taste fork). Residual risk past the cap is covered downstream: TDD RED verification, executor report-don't-adapt, `/regression_gate`.

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
- **MANDATORY Workflow dispatch through `review_fanout.js`** (lens set per Phase 2's shape-conditional composition — 5 on a default `code` plan, 4 on a `meta` one; pins per Phase 2 — design-judgment lenses opus-floored, memory raised to `opus·medium` and architecture-quality to `opus·high` on architecturally-loaded plans, `plc-evidence-grounding` fixed at `opus·low`). The engine is lens-agnostic: it takes whatever `args.agents` it is handed, so adding a shape means composing a different list, never editing the engine. Do not perform inline; do not collapse into one generic agent; bare `Task` dispatch only as Workflow-unavailable fallback.
- **Evidence-quoting for refuting claims.** Any agent finding that REFUTES the plan on empirical grounds ("this type already exists / file missing / already refactored") must quote the raw tool output (grep line, read excerpt) supporting it. The orchestrator first-party-verifies at least one quote before the finding counts — agents fabricate confident file-state claims (`feedback_delegate_output_trust`); paraphrase survives fabrication, verbatim output rarely does.
- **Detect-and-report only for `plc-test-readiness` + the Phase-1b DoD/stub scan.** They surface findings; they never emit auto-applicable `old`/`new` edits. Test content and Definition-of-Done are scope decisions — a downstream auto-apply loop (e.g. `/part_drive`) must never be able to silently fill in scope from these findings.

---

## When to run (suggested)

- Right after the plan file is drafted and BEFORE presenting it for approval, when the litmus above triggers.
- Before user approval of any plan involving cross-domain refactors, new abstractions, or deletions.
- NOT wired into `/session_end` or `/regression_gate` — those are post-implementation.
- NOT auto-fired from the SessionStart or UserPromptSubmit hooks — too noisy. The `plan_memory_reminder.py` hook covers passive enforcement.
