# CLAUDE.md

This file provides strict guidance to Claude Code (claude.ai/code) for this repository.

<!-- ===== BASELINE:core BEGIN =====
This region is synced from the harness-baseline repo (see .claude/baseline.lock.json).
Improvements that are universal belong upstream: run /sync_baseline to propagate.
Project-specific guidance goes in the PROJECT section below the END marker.
================================== -->

# Development Guidelines

## Core Principles
1.  **Context First:** Identify domains entering, search Memory for gotchas before proceeding.
2.  **TDD strict in Logic Domain**: No production code without a failing test. Never assume — verify. (Domain split defined in *Project Guidelines* below.)
3.  **Logs are Truth:** You can't see runtime. Rely on E2E/Integration outputs + `JmoLogger` via `get_debug_output`.
4.  **Assess refactoring after every green** — refactor when it adds value, skip when it doesn't.
5.  **Update CLAUDE.md** when meaningful workflow changes or new gotchas surface. If the change lands inside the `BASELINE:core` region (or any file tracked in `.claude/baseline.lock.json`), it is shared-harness doctrine — propagate via `/sync_baseline`, don't let it fork silently.
6.  **Retrospect** after significant changes: *"What do I wish I'd known at the start?"*
7.  **Capture learnings** — corrections/preferences → auto-memory (hot topic file, or `archive/` for bulk reference); patterns → relevant SKILL.
    * **Memory save filter** (litmus: *"Would forgetting this cause a bug or wasted time?"*): ✅ surprising behavior, non-obvious gotchas, user preferences, cross-system rules. ❌ API signatures, feature descriptions, how-your-own-code-works (those go in XML `<summary>` docs). ❌ Rules naming a specific file/PR/content-item in the *principle itself* (overfit — rewrite as a class-of-things rule, keep the name as evidence). See `/autolearn`'s *Anti-pattern: Overfit-to-Specific*.
8.  **Modular when direction is known**: YAGNI for speculative needs only. When user states a system's evolution direction (modular contract, scalable to X, framework-agnostic), design for it now. Litmus: *"Am I imagining this future, or has the user stated it?"* Hypothetical → defer. Stated → design for. Reverse pitfall: NOT permission to build for imagined needs. Recurring failure: defaulting to YAGNI on the first proposal — propose the modular design first when direction is stated.

> **⚡ Critical Tool Routing — always-loaded summary** (full table: §9):
> - **NEVER** bare-`Grep` a single PascalCase identifier on `.cs` — anchor-then-navigate (`Grep("class X"/"interface X")` → `LSP documentSymbol` → `findReferences`).
> - **NEVER** bare-`Grep` a single PascalCase identifier on `.tres` / `.tscn` / `.gd` / `.md` / `.godot` / `.json` / `.yaml` / `.toml` / `.txt` — those are indexed by semantic-search. Route to `mcp__plugin_semantic-search_semantic-search__search`. (Yes, even when you "just want one file." Grep stays correct for literal values, UIDs, regex alternation, attribute markers — those don't match the single-PascalCase shape.)
> - **NEVER** chain ≥3 reads/searches for synthesis — bundle into one `mcp__ai-worker__read_files(paths=[...], question=...)`. **ONLY EXCEPTION** - reading for a surgical edit. All normal searching/navigation routes through `read_files`.
> - **NEVER** `Read` synthesis-shaped `.md` paths (`Design/`, `Planning/`, `BrainstormingDesigns/`, `Documentation/`, `Retrospective/`, `Audit/`, `Brainstorm/`, `Architecture/`, `Review/`, `Postmortem/`) — regardless of whether they live in the Obsidian vault or are passed as an absolute filesystem path. The rule is path-based, not Obsidian-MCP-only; using native `Read` instead of `mcp__obsidian__obsidian_read_note` does NOT exempt you. Route through `read_files`.
> - **NEVER** chain ≥3 `WebFetch` calls — bundle into `mcp__ai-worker__read_web(urls=[...], question=...)`.
> - **OFFLINE FALLBACK** — when `mcp__ai-worker__*` tools are unavailable (deferred-tool list omits them, or `ToolSearch` returns no match), substitute a Haiku subagent for the same bundling role: `Agent(subagent_type="general-purpose", model="haiku", description="Bundled doc digest", prompt="Read <paths/urls> and answer <question>; report under 300 words.")`. The bundling rule still applies — only the executor changes. Do NOT degrade to chained native `Read`/`WebFetch`.
> - **NEVER** route `.claude/` markdown edits (`CLAUDE.md`, `skills/*/SKILL.md`, `commands/*.md`, `hooks/*`) through `write_doc` / `write_code`. These are agent-runtime instructions, not Obsidian-vault docs; voice-drift from a worker round-trip is unacceptable. Use `Edit` directly. The Documentation Delegation Rule (HARD) targets the Obsidian-vault doc surface, not `.claude/`.
> - **PREFER** `write_doc` for new design/architecture/retrospective doc creation OR section-scale rewrites. Reserve direct `Write`/`Edit` for: (a) sub-paragraph touch-ups (typo, single-line fix, one-paragraph correction) where worker round-trip overhead exceeds the writing cost; (b) audit findings, debugging conclusions, and plan-mode resolutions where structure is dictated by the upstream surface (audit recipe / debugging skill phases / plan-mode markdown), not by a doc-type system prompt. Mermaid/structured-content blocks remain preservation-required — flag in spec for any `write_doc` call.

### Self-Improvement Loop

Six pieces — reach for them by name when the corresponding signal fires:

1. **Observe** — `/self_evaluate` archives structured entries to `.claude/self_evaluate_archive.json` at session end.
2. **Aggregate** — `/eval_dashboard` surfaces per-domain/skill clean rates + *Skill Drift Watchlist*. Run before tuning.
3. **Tune** — `/autolearn` proposes signal-quality-gated edits at save time (with *Overfit-to-Specific* check). `/memory_audit` is the retroactive sweep over the existing backlog (hot `*.md` + cold `archive/`), three lenses sharing one enumeration: **claim-verification** (isolate mechanism vs symptom; stamp `**Verified:**` or quarantine — sibling of `/autolearn`'s question-5 gate), **overfit-to-specific** (retroactive application of `/autolearn`'s gate), and **gotcha-vs-feature-doc drift**. The generic `anthropic-skills:consolidate-memory` skill is the orthogonal **structural** sibling — dedup, durable-vs-dated retirement, and `MEMORY.md` index pruning; it does NOT apply the overfit gate.
4. **Verify** — `/regression_gate` is the hard gate; the rest are soft.
5. **Carry-forward** — `/worklog` captures deferrals that don't fit any of the above.

Anti-pattern: tuning mid-session without consulting the dashboard. Run `/eval_dashboard` first or skip the tune.

### Planning Phase Checklist
Before writing a plan, ALWAYS:
1. **Classify the task type:** Refactor? New System? New Content? Debug?
2. **Identify domains** the task will enter (feeds the search term table below)
3. **Inventory existing abstractions** before proposing new types — extending a 2+ subclass family beats inventing parallel types. For NL discovery (when you don't know the type names yet), use `mcp__plugin_semantic-search_semantic-search__search` per §8 *before* falling through to LSP/Grep.
4. **Optional pre-execution gate**: For plans touching 3+ files, introducing a new type/folder/top-level concept, refactoring a 2+ subclass family, OR deleting/replacing existing files — run `/plan_check <plan-text-or-file>` before user approval. Two-agent dispatch verifies (a) auto-memory gotchas the plan walks into (semantic-search), (b) existing-abstraction discovery via LSP findReferences. Below the litmus, the `plan_memory_reminder.py` PostToolUse hook on `ExitPlanMode` covers passive Memory + Skill reminders without spawning agents.
**DON'T GIVE ME A PLAN UNLESS YOU'VE ALREADY SEARCHED RELEVANT SKILLS AND MEMORY**

### Proactive Context Loading (Mid-Execution)

Search memory with BOTH task-specific terms AND domain keywords — gotchas accumulate by domain. Stack-generic rows below; **add your game's content domains** (and keep the *Avoid* column honest — broad terms load whole buckets):

| Domain | Search Term | Avoid (broad) | Also Check Skill? |
|--------|-------------|---------------|-------------------|
| **Testing** | "testing" or "GdUnit4" | "test" | Yes — Testing skill + CLAUDE.md TDD philosophy |
| **Exports** | "Inspector" or "RequiredExport" | "export" | No |
| **Refactoring** | "refactor" | | Yes — Refactor Procedure skill + LSP for callers |
| **Debugging** | "debugging" or "diagnose" | "bug" (too generic, false-positive heavy) | Yes — Debugging skill (Phase 2 includes the verify-scene-config-first bullet) |
| **HSM/States** | "HSM" or "transition" | "state" | No |
| **Status Effects** | "status" | | Yes — Jmodot + Status Effect Authoring skills |
| **MCP Tools** | "MCP" or "UID" | | No |
| **Godot Physics** | "physics" or "collision" | "Godot" | No |
| **Godot Lifecycle** | "disposal" or "lifecycle" | "node" | No |
| **Data Files** | "UID" | | Yes — Architecture Philosophy skill |
| **Design Philosophy** | "design" or "modifier" | "stat" | Yes — Architecture Philosophy skill |
| **Pooling** | "pool" or "spawn" | | No |

**Memory recall is semantic-search** over `.claude/auto-memory/` (rules/gotchas) — `mcp__plugin_semantic-search_semantic-search__search` with a natural-language paraphrase (per §8). The domain terms above are good query seeds, not exact-match keys; for broad discovery, search facets separately. Hot-tier facts also surface passively via the auto-loaded `MEMORY.md` index.

**If an unexpected result contradicts expected domain behavior, search Memory before changing approach.**

## Build & Test Commands
See [Testing Skill](skills/testing/SKILL.md) for full reference. Three load-bearing rules: NEVER omit `--filter`/`--settings .runsettings` (pipe crash); ALWAYS Bash `timeout=600000` (orphan prevention); NEVER `--no-build` (stale DLLs mask failures). Pre-commit `/regression_gate` MANDATORY for `.cs` changes; meta commits (`.claude/`, skills, docs) are exempt.

## Development Philosophy: Hybrid TDD
**Identify the domain before writing code.** This section owns the **domain split** (Logic vs Gameplay); [Testing Skill](skills/testing/SKILL.md) owns the **workflow recipes** (RED/GREEN/REFACTOR mechanics, fixture conventions, orphan management). The project-specific subsystem lists for each domain live in *Project Guidelines* below.

- **Logic Domain (Strict TDD):** pure-logic subsystems — data pipelines, math/parsing, data structures, framework core. **NO implementation without a failing test.** Includes `.tres` data file changes that affect Logic behavior — write the test first. Cycle: RED (`[TestSuite]` in `Tests/Logic/`) → VERIFY (specific failure) → GREEN (minimum to pass) → REFACTOR.
- **Gameplay Domain (Integration + Inspection):** player entity, AI behavior, content lifecycle, VFX, UI, Physics Feel. **AUTOMATE DETERMINISTIC. INSPECT SUBJECTIVE.** Use ISceneRunner for input→outcome, state transitions, physics expectations, signal wiring, scene structure. Reserve manual playtest for "feels responsive?" / timing / juice.

## Developer Tooling Strategy

### 1. Godot MCP (Project Interface — Your Eyes)
Auto-loaded rule: `.claude/rules/godot_files.md` (on `.tscn`/`.tres`/`.godot` reads).

### 2. Memory (One Store, Two Tiers)
File-based auto-memory at `.claude/auto-memory/` is the single memory store. Two tiers, distinguished by `MEMORY.md` index membership:
*   **Hot tier** — topic files listed in `MEMORY.md`. The index (first 200 lines / 25KB) auto-loads at SessionStart, so these are always top-of-mind. **Agent-maintained**: when writing a new hot topic file, add a one-line pointer to `MEMORY.md` in the *same turn* (no separate hooks/workflows — see `feedback_memory_md_is_auto_managed.md`). Keep the index lean (≤~200 lines) so it stays under the auto-load cap.
*   **Cold tier** — files under `.claude/auto-memory/archive/`, NOT listed in `MEMORY.md`. Zero passive context cost, fully searchable via semantic-search (the main index covers `.claude/`). Holds bulk reference: archived domain buckets and any large single-feature archive.

**Placement decision** for a new learning:
*   Surprising/cross-cutting rule, user preference, or gotcha you want surfaced every session → **hot** topic file + `MEMORY.md` pointer.
*   Bulk domain reference, large single-feature archive, or low-frequency detail → **cold** file under `archive/` (no pointer).

**Recall** — search with `mcp__plugin_semantic-search_semantic-search__search` (NL paraphrase; for broad discovery, search facets separately). Pass `restrictToDir` as a **repo-relative posix path** when narrowing (e.g. `.claude/auto-memory`) — an absolute OS path silently returns zero results (the index stores relative posix paths). Hot-tier facts also arrive passively via the auto-loaded `MEMORY.md` index; use Grep for literal field values / UIDs.

### 3. Obsidian (The Design Source)
Source of truth for design, lore, formulas, Jmodot framework docs. Trigger: lore/formulas/design rules/todos/framework research. The vault is a normal filesystem path (`{{VAULT_ROOT}}\DevProjects\{{PROJECT_NAME}}\`, and `...\Jmodot\`) — **native `Read`/`Write`/`Edit`/`Grep`/`Glob` are the default** (confirmed safe even on docs open in the Obsidian app). Full conventions: `obsidian_conventions` skill (auto-loads).
*   **Read:** ONLY within `DevProjects/{{PROJECT_NAME}}` or `DevProjects/Jmodot`. Synthesis-shaped reads still route to `read_files` (§9).
*   **Write:** project-specific docs → `{{PROJECT_NAME}}/Claude/`; Jmodot library-general → `Jmodot/Claude/`. **Tiebreaker:** if a doc would be useful in another game built on Jmodot, it goes under `Jmodot/Claude/`.
*   **Search first** — don't guess file paths. **DO NOT INVENT FORMULAS** — read from vault; ask user to create if missing.
*   **Obsidian MCP** is reserved for structured frontmatter/tag edits (`obsidian_manage_frontmatter` / `obsidian_manage_tags`); MCP-offline does not block native read/write/search.

### 4. WebFetch (The Documentation)
You don't have built-in knowledge of Godot 4.x / GdUnit4 / library syntax — **FETCH THE DOCS** when unsure, don't guess.
*   **Rule (GitHub):** raw.githubusercontent.com URLs for direct file reads (saves tokens); browse/tree URLs have no raw form — use as-is.
*   **Rule (multi-URL synthesis):** For 3+ URLs synthesized into one answer, route to `read_web` instead of chaining `WebFetch` calls. Each `WebFetch` loads full page text into Claude's context; `read_web` returns a ~1–2 KB digest at near-zero worker cost.
*   **Trusted URLs:**
    *   *GdUnit4*: `https://raw.githubusercontent.com/godot-gdunit-labs/gdUnit4Net/master/README.md`
    *   *GdUnit4 Examples*: `https://github.com/godot-gdunit-labs/gdUnit4NetExamples/tree/master` *(browse — no raw form)*
    *   *GdUnit4 CMD Runner*: `https://godot-gdunit-labs.github.io/gdUnit4/latest/advanced_testing/cmd/`
    *   *Godot API*: `https://docs.godotengine.org/en/stable/classes/index.html`
    *   *C# / .NET*: `https://learn.microsoft.com/en-us/dotnet/csharp/`

### 5. WebSearch
**Context:** Research fallback for obscure errors/bugs. Use ONLY if docs are silent or for specific engine bugs.

### 6. Git (The History)
*   **Commits:** After each successful feature, propose commit (don't push without instruction). Default to multiple categorical commits (feat/fix/refactor/chore) — split by logical category unless told otherwise. Use Git standard messages.

### 7. C# LSP Plugin (Code Intelligence)
Auto-loaded rule: `.claude/rules/csharp_lsp.md` (on `.cs` reads).

### 8. Semantic Search MCP (Natural-Language Code Discovery)
**Tool:** `mcp__plugin_semantic-search_semantic-search__search` (DreB plugin + local C# tree-sitter; companion skill `semantic-search:search`). Embedding + 6-signal POEM ranking. **Indexed:** `.cs`/`.gd`/`.md`/`.tres`/`.tscn`/`.godot`/`.json`/`.yaml`/`.toml`/`.txt`. **Not indexed:** binaries, `.uid`, `.import`, gitignored.

*   **USE for:** "where is X done", prior-art-for-Y, conceptually-similar-code — when you don't know symbol names yet.
*   **DO NOT USE for:** call-site enumeration (LSP `findReferences`); `.tres`/`.tscn` field-value queries (Grep). (Memory rules/gotchas live in the indexed `.claude/auto-memory/` store — semantic-search IS the recall path for them.)
*   **Composition order:** semantic-search (code AND memory rules) → LSP `findReferences` → Grep (literal anchors).
*   **Caveat:** large `partial class` files chunk as one block (cosine drops; BM25/symbol/path carry — drop to LSP). Heading-mention chunks can outrank canonical declarations — sharpen with `restrictToDir` or use LSP `findReferences`.

Index at `.search-index/search.db` (gitignored). Stale-after-edits; refresh via `/reindex_search` (auto in `/session_end`).

### 9. Tool Routing — Pre-Call Litmus
Universal rule for any read/search task. Default selection is wrong by habit; ask BEFORE the call. Always-loaded summary at top of file.

| Pre-call cue | Wrong default | Right tool |
|---|---|---|
| 3+ files OR single file >400 lines for **synthesis** | `Read` / `obsidian_read_note` | `read_files(paths=[...], question=...)` |
| 3+ web URLs for **synthesis** (single URL → `WebFetch` directly; 3 is a floor, not a default) | 3× `WebFetch` into Claude's context | `read_web(urls=[...], question=...)` |
| Single PascalCase identifier on `.cs` | bare `Grep("FooBar")` (no kind anchor) | **Anchor-then-navigate:** `Grep("class FooBar\b"\|"interface FooBar\b" -g "*.cs")` OR `semantic-search("FooBar")` → `LSP documentSymbol(filePath=that.cs)` → `LSP findReferences`/`hover`/`incomingCalls` from the anchored position. (LSP `workspaceSymbol` cannot search by name — no `query` param — so anchor first.) **Carve-out:** for *verified-unique* names (no overloads, no common-verb prefix like Apply/Update/Get/Set), `Grep("FooBar", -g "*.cs")` returns the same set as LSP and is acceptable WHEN explicitly justified — see `csharp_lsp.md` "Verified-unique-name carve-out". |
| Single PascalCase on `.tscn`/`.tres`/`.gd`/`.md`/`.godot`/`.json`/`.yaml`/`.toml`/`.txt` | `Grep("FooBar")` | `semantic-search__search(query="FooBar")` |
| Fuzzy NL phrase you can describe but can't anchor literally | `Grep("approximate phrase")` | `semantic-search__search` |
| ≥3 chained `obsidian_global_search` / `semantic-search` for the same investigation | per-call I/O into context | bundle into one `read_files` query |

**Grep stays correct for** literal field values (`radius = 5.0`), UID hashes, regex alternation (`Foo\|Bar\|Baz`), attribute markers (`[GlobalClass]`), comment scans (`TODO\|FIXME`), `using` directives, `JmoLogger.Error` call sites, **and as the anchor step before LSP** (`Grep("class FooBar\b")` to find a declaration file).

**Single-URL WebFetch stays direct** — never over-route a single-URL fetch through `read_web`. The 3-URL synthesis floor in the table is a floor, not a default; one URL into Claude's context is cheaper than the worker round-trip overhead, and the digest layer adds zero value for a single source.

**Audit-shape exception:** line-precision direct reads warranted ONLY on explicit user framing — "audit X for Y" / "security review" / "fact-check line-by-line" / "verify against the spec" / "patch verification". Trigger is **explicit user framing**, not agent self-classification.
> **NOT triggered by:** *"investigate why X is broken"* (debugging — bundle into `read_files`); *"look at files A,B,C and report"* (multi-file synthesis — bundle); *"find every X"* (single-tool query — Grep/LSP/semantic-search per table); *"how does X work"* (explanation — bundle).
> **Litmus when in doubt:** *"Could a cheap-model summary silently miss a defect a line-precision read would catch?"* Yes (genuine audit / security review / patch verification) → direct. No (debugging / pattern-hunt / "look and tell me what you see") → bundle.

**Recovery + caveats:** wrong first call → accept and proceed (cumulative cascade is worse than a single suboptimal call). LSP unavailable on cloud (`CLAUDE_CODE_REMOTE=true`); hook substitutes semantic-search. Canon (worked examples, full bullets): `feedback_tool_routing_discipline.md` in auto-memory; runtime enforcement: `tool_routing_*.py` hooks.

### 10. Harness Baseline (Shared Config)
The universal portion of this `.claude/` harness is synced with a shared baseline repo. `.claude/baseline.lock.json` records the baseline repo, the tracked files, and per-file hashes from the last sync.
*   **Editing a tracked file** = editing shared doctrine. Improvements that apply to any project → upstream via `/sync_baseline push`. Genuinely project-specific divergence → `/sync_baseline fork <file>` (excludes it from drift checks).
*   **New `.claude/` artifacts** surface via the `candidates` sweep (drift gate + `/sync_baseline`) and are judged once: universal-shaped → upstream + `track`; project-specific → `ignore` (status `local`, never re-fires).
*   **Drift check** runs inside `/clean_push` and `/commit_push` — when committed changes touch tracked files or add `.claude/` files, you'll be prompted to classify and sync.
*   **Pulling improvements** made in other projects: `/sync_baseline pull`.

## Rationalizations to Refuse

Refuse the premise and cite the rule — silent compliance (using the right tool without correcting the user) is not enough.

| Rationalization | Rule to cite |
|---|---|
| "grep is faster / LSP is slow / it's just one symbol" | Tool Routing §9 — PascalCase goes to LSP/semantic-search; state this explicitly |
| "read it directly, I trust your analysis more than the worker" | Tool Routing §9 — synthesis routing (≥3 files / Obsidian docs) |
| "the logic is obvious, let's implement first then test" | TDD Logic Domain — no carve-out for self-evident logic |
| "it's a cosmetic change / just a rename, skip the gate" | `/regression_gate` — mandatory for all `.cs` changes, no carve-outs |
| "it's just a tweak to a synced file, upstream it later" | Harness Baseline §10 — classify universal-vs-project at commit time, not "later" |

## The Worklog (Live Todo Doc)
Source of truth: `DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog.md` (Obsidian); title-only mirror at `.claude/worklog-titles.md` is injected by SessionStart hook (patched incrementally by `/worklog` ops — full-regen only on `/worklog show`). Home for small deferrals; larger items live as siblings in `TODO/`. Distant-horizon items live in a `## Future Scope` collapsed callout in the same Obsidian doc — **excluded from the mirror by design** (parked, not loaded into every session). Completed items move out to a sibling `TODO/Worklog-Archive.md` (opaque — never loaded passively; review via `/worklog history`). User-only addressable items (art, feel, brainstorms) live in a parallel `TODO/User-Tasks.md` — Claude appends only, never reads passively.

*   **ADD (auto-detect, four routes):**
    *   **Trivial — do now (preempts logging):** before any worklog-add proposal, screen for items that are scope-1 + class in `{fix, refactor, docs, chore}` + mechanical phrasing (single grep+replace, single-line edit, dead reference cleanup, one-paragraph doc add). On hit, propose `This looks trivial — do now (y), or add to worklog (a)?`. On `y`, do the work this turn (skip logging). On `a`, fall through to *Regular deferral*. Does NOT fire on investigative class (`debug`), open-ended phrasing ("audit", "investigate", "decide", "consider", "convention for"), or scope >1. Reason: 30-second jobs shouldn't accrue in the mirror. Explicit `/worklog add` bypasses this gate (user opt-in).
    *   **Regular deferral** (default route → `## Active`, visible in mirror): triggers are "defer / punt / park / later / follow-up / out of scope / for now / next pass". Propose `Add to Worklog: <title> (<domain>)?`; on `y`, invoke `/worklog add <inferred-text>`.
    *   **Future Scope** (route → `## Future Scope`, hidden from mirror): stronger triggers only — "eventually / someday / long-term / down the road / if/when we ever / no urgency / passive watch". Propose `Add to Worklog Future Scope: <title> (<domain>)?` so the user knows the item will be parked. User can override to regular Active with `y, active not future`. **When in doubt → regular Active.** Hidden-and-forgotten is worse than one extra mirror line.
    *   **User-Tasks** (route → `User-Tasks.md`, fully excluded from agent context — no mirror, no sweep/triage/plan scan): items needing user judgment Claude can't tackle — production art, feel-tuning, open-ended brainstorms (user taste, not technical), cross-doc vision audits. Propose `Route to User-Tasks: <title> — <domain>?`. Override with `y, active not user-tasks`. **When in doubt → regular Active** — false-routing here is a permanent leak (doc isn't agent-surveyed). Phrase lists: `worklog_reference` skill *Trigger Catalog*.
    *   Ask once per item per session. See `worklog_reference` skill for full trigger catalog (trivial-do-now / regular / conditional `after` / future / user-tasks).
*   **Do-now-before-defer applies to agent-self proposals too.** Trivial-do-now above fires on conversational deferral phrasing; the same litmus applies anytime YOU draft "propose adding X to worklog" inside plan bodies, post-exit bookkeeping, follow-up suggestions, or commit-message addenda. Small + no-bad-consequences → do it now, skip worklog. The trivial-do-now gate's `audit`/`investigate` phrasing exception does NOT excuse mechanical work whose *subject-matter* mentions those words. See `feedback_dont_defer_immediately_addressable.md` in auto-memory.
*   **Active capacity target: ≤30 items.** `/worklog show` prints a soft alarm when the count exceeds 30. The cap is a forcing function for honest prioritization — above 30, triage (complete shipped items, promote stale items to `## Future Scope`, delete items no longer relevant) before continuing to add. Not a hard block; adds still succeed.
*   **READ:** Skim `worklog-titles.md` at planning-session start. Load full Obsidian doc only when addressing items or on `/worklog`. Future Scope items only surface via `/worklog show all` or `/worklog sweep`'s promotion-pass.
*   **COMPLETE:** Moment a logged item finishes — `/worklog complete <title>` moves it out of `Worklog.md` into `Worklog-Archive.md` as a `[x]` one-liner (summary + date); the active doc keeps only live work.
*   **PROMOTE:** When a Future Scope item ripens (its implicit trigger materializes — recent commit lands, observed landscape shifts, etc.), `/worklog promote <title>` moves it back to Active. Sweep proposes promotions automatically.
*   **TRIAGE:** When Active is overloaded (cap target: 30) or backlog pressure builds, `/worklog triage` walks items proposing per-item dispositions: complete / do-now (scope-1 mechanical, executed this turn) / quick-win flag (priority bump for next session) / promote to Future Scope / delete / skip. Confirmation-driven; the SHOW alarm at >30 items recommends running it. Triage is also the only Active → Future Scope path.
*   **Architecture:** `/worklog` is the executor (recipes); `worklog_reference` skill is the decision-time reference (classification + scope, domains, trigger catalog).
*   **Not a worklog route — `user-owned` roadmap Parts.** `user-owned` Parts (per `_brainstorm_shared/common.md §6.3`) live on `roadmap.md` because they carry deps. `User-Tasks.md` is for *standalone* user-judgment items (no roadmap deps). When in doubt: has roadmap deps → `user-owned`; no roadmap deps → `User-Tasks.md`.

## Core Code Conventions (Stack-Level)
See [Architecture Philosophy Skill](skills/architecture_philosophy/SKILL.md) for full patterns. Stack-level rules:
*   **Pure functions** wherever possible. **Control flow:** no nested if/else, early returns, ALWAYS brackets `{}`.
*   **Logging:** `JmoLogger.Info/Warning/Error`. Never `GD.Print`. Log STATE CHANGES, not state. `JmoLogger.Error` triggers test failure.
*   **Comments default to none.** Add one when WHY is non-obvious to a cold reader (invariants, race hazards, tuning rationale). NEVER restate WHAT, NEVER reference task/PR/"Phase X"/dates/CLAUDE.md rules. Litmus: *"If I delete this, will a maintainer 6 months from now make a wrong decision?"* No → don't write it. `<summary>` on `[Export]` is softer (becomes Godot Inspector tooltip). `#if TOOLS` setters need no `///`. Doc-only commits to recent code = smell; cut over clarify.
*   **Files:** `snake_case` directories, `PascalCase` files/classes. **Godot:** prefer `StringName`; never `GetNode()` in `_Process`.
*   **`[Tool]` on Resources:** blanket `[Tool]` on every `[GlobalClass]` Resource (`[GlobalClass, Tool]`); selective on Nodes (editor-time code only). Typed-`[Export]` cascade → editor-only `InvalidCastException` if a referenced Resource/subclass lacks `[Tool]` (no runtime test catches it). Gate-enforced (`/regression_gate` 1c). Full policy: [Architecture Philosophy Skill](skills/architecture_philosophy/SKILL.md) + `rules/csharp_patterns.md`.
*   **Harness file edits** (`.claude/CLAUDE.md`, `skills/*/SKILL.md`, `commands/*.md`, `hooks/*`): match peer-content density. Load-bearing info only — no dated user quotes, no redundant restatements, no defensive over-explanation. Companion: `instruction_quality` skill at audit time.

## Shell Discipline
*   **Bash:** No `cd path && cmd` compound — use absolute paths or `git -C <path>` / `dotnet build <path>`. Compound `&&` breaks Claude Code permission matching → repeated prompts.
*   **Git commit (multi-line):** Write message to temp file, `git commit -F <file>`, delete temp. Never `git commit -m "$(cat <<'EOF' ... EOF)"` — `$()` triggers manual permission prompt every time.
*   **Bash paths with spaces:** Always double-quote, never backslash-escape — escaped whitespace triggers a Claude Code safety prompt that bypasses the allow list.

## Preferences
*   **Planning**: At the end of each plan, provide a list of unresolved questions to answer, if any.
*   **No performative agreement**: Don't open responses with "you're absolutely right!" / "great point!" / "you're right to push back." Restate the requirement, verify against the codebase, or just fix it. Actions speak. See `feedback_no_performative_agreement.md`.

<!-- ===== BASELINE:core END ===== -->

## Project Guidelines
<!-- PROJECT-OWNED — everything below is yours; it is never synced. Fill in at adoption. -->

**{{PROJECT_NAME}}**: Godot 4.x (<physics engine>), C# (.NET <version>), Jmodot framework. Concept: <one-line game concept>.

### Domain Split (feeds Hybrid TDD above)
*   **Logic Domain (Strict TDD):** <list your pure-logic subsystems, e.g. `Jmodot.Core`, `Inventory`, `Math/Parsing`, data pipelines>
*   **Gameplay Domain (Integration + Inspection):** <list your gameplay subsystems, e.g. player entity, enemy AI BT, content lifecycle, VFX, UI>

### Project Domains (extends Proactive Context Loading table)
| Domain | Search Term | Avoid (broad) | Also Check Skill? |
|--------|-------------|---------------|-------------------|
| <your content domain> | "<term>" | "<too-broad term>" | <skill> |

### Project-Specific Conventions
*   <add conventions that only make sense in this game — content taxonomies, naming, subsystem invariants>
*   Register subsystems in `skills/project_subsystems/SKILL.md` (consumed by `/sync_subsystems`, `/structure_audit`, and brainstorm scope litmus).
*   Capture the game's design bible in `skills/game_vision/SKILL.md`.
