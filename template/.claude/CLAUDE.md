# CLAUDE.md — Development Guidelines

<!-- ===== BASELINE:core BEGIN =====
This region is synced from the harness-baseline repo (see .claude/baseline.lock.json).
Improvements that are universal belong upstream: run /sync_baseline to propagate.
Project-specific guidance goes in the PROJECT section below the END marker.
================================== -->

## Communication
Lead with the answer; close with the next action. Use plain, specific words and state each fact once — much of what you write is read later, by the user after a run or by a delegate with none of this context, so the close stands alone. For three or more findings, decisions, options, risks, questions or actions, assign stable codes (F1…, D1…, O1…, R1…, Q1…, A1…) and keep them for the whole conversation; no codes for a short answer.

## Core Principles
1.  **Ideal architecture is the deliverable:** modular, scalable/extensible, clean/intuitive, loosely coupled, data-driven, no-redundancy design AND designer-intuitive authoring outrank working-code-fast at EVERY stage (design, plan, execute, review). Prototyping is the exception. Litmuses: `rules/design_litmus.md` (auto-loads on `.cs`), `rules/scene_authoring.md` §Scene anatomy (auto-loads on `.tscn`/`.tres`).
2.  **Context First:** identify the domains you are entering and search Memory for gotchas before proceeding. Verify decisive premises, not just their citations.
3.  **TDD strict in Logic Domain:** no production code without a failing test. Never assume — verify. (Domain split: *Hybrid TDD* below; project subsystem lists: *Project Guidelines*.)
4.  **Logs are Truth:** you can't see runtime. Rely on E2E/Integration outputs autonomously and `JmoLogger` via `/analyze_godot_logs` after a user playtest. After a session with significant `.tres`/`.tscn` wiring, launch the game via the Godot MCP and verify clean via `get_debug_output` (check `get_godot_version` matches the project first — the MCP's engine pin is independent of `$GODOT_BIN`, and an older engine rewrites `project.godot`/csproj on open).
5.  **Assess refactoring:** a similar surface existing is not a reason to reuse it — if that code is suboptimal, reusing it or copying its pattern adds debt. Refactor when it adds value, skip when it doesn't.
6.  **Update CLAUDE.md** when meaningful workflow changes or new gotchas surface — queue non-load-bearing edits per §9 rather than editing mid-session. A change inside the `BASELINE:core` region (or any file tracked in `.claude/baseline.lock.json`) is shared-harness doctrine — propagate via `/sync_baseline`, don't let it fork silently.
7.  **Retrospect** after significant changes: *"What do I wish I'd known at the start?"*
8.  **Capture learnings** — place each learning where it will be loaded when the decision recurs (routing: `/codify` §Step 4). Save-filter litmus: *"Would forgetting this cause a bug or wasted time?"* — ✅ surprising behavior, gotchas, preferences, cross-system rules; ❌ API signatures, feature docs, how-your-own-code-works (a member's own contract → its XML `<summary>`), rules naming a specific file/PR in the principle itself (overfit — rewrite as class-of-things; see `/autolearn` *Overfit-to-Specific*).
9.  **Modular when direction is known:** YAGNI for speculative needs. When the user states a system's evolution direction (modular contract, scalable to X, framework-agnostic), design for it now. Litmus: *"Am I imagining this future, or has the user stated it?"* Hypothetical → defer. Stated → design for. Reverse pitfall: NOT permission to build for imagined needs. Propose the modular design first when direction is stated. **When unclear: ASK.**
10. **Keep scope and quality intact.** Missing, failed and unverified are not clean; report actual tests and artifacts. Check current git state and preserve peer work — `git status --short <file>` before calling a defect peer-owned; clean means it is yours to fix now.

> **⚡ Tool Routing:** §9 owns the pre-call litmus (read/search AND write routing) — consult it before any read/search/doc-write call. Runtime `tool_routing_*.py` hooks nudge violations at call time; `session_model_rails.py` injects the session rails at SessionStart.

### Self-Improvement Loop
Reach for these by name when the signal fires. **Observe** — `/self_evaluate` at session end; `/orchestration_metrics` is its empirical sibling when the session dispatched Workflows. **Aggregate** — `/eval_dashboard`, run before tuning. **Tune** — `/codify` routes one correction or repeated workflow to the surface that will fire (suggest it; the user invokes); `/autolearn` at save time; `/memory_audit` is the retroactive sweep, and a clean quality report on an over-budget `MEMORY.md` IS the fragmentation signal. **Verify** — `/regression_gate` is the hard gate; the rest are soft. **Carry-forward** — `/worklog` captures deferrals fitting none of the above. Each command's own file owns its mechanics; suggest user-invoked commands rather than pretending to have run them.

### Planning Phase Checklist
Before constructing a plan, ALWAYS:
1. **Classify the task** (refactor / new system / new content / debug) and **name the domains** it enters — that choice drives the skills-and-memory search, which precedes the first plan line.
2. **Inventory existing abstractions before introducing ANY new named configuration surface**, at every granularity (type, `[Export]`, parameter, behavior bool/enum, helper — `rules/design_litmus.md` #1 owns the kind-list). Name the family that owns the concern or record "none exists"; extending a 2+ family beats inventing a parallel surface. Dispatch `/explore` rather than hand-rolling the sweep — it reports an UNCOVERED dimension where a hand sweep returns an empty result that reads as "nothing exists".
3. **Run `/plan_check` before asking for approval** when the plan touches 3+ files, introduces a new type/folder/top-level concept, refactors a 2+ subclass family, deletes/replaces files, or changes always-loaded guidance. **Harness plans are in scope**: no compiler and no `/regression_gate`, so plan-time is the only gate they get. Resolve user-facing forks before approval; `skills/_brainstorm_shared/plan_file_format.md` owns the plan format.

### Proactive Context Loading (Mid-Execution)
**Entering a new domain mid-task → search `.claude/auto-memory/` before acting**, with a natural-language paraphrase of what you are about to do (semantic search, per §2 *Recall*). Gotchas accumulate by domain, so the domain you just entered is the one whose memory you have not read. Per-domain search seeds: `reference/memory_domains.md` (the documented home for the table `hooks/plan_memory_reminder.py` applies on plan writes) — **add your project's content domains there.** Background notifications and incidental words are not domain changes.

**If an unexpected result contradicts expected domain behavior, search Memory before changing approach. Never leave memory inaccurate — the same mistake should never reoccur.**

## Build & Test Commands
See [Testing Skill](skills/testing/SKILL.md) for full reference. Three load-bearing rules: NEVER omit `--filter`/`--settings .runsettings` (pipe crash); ALWAYS Bash `timeout=600000` for tests (orphan prevention) and for any vault/engine-install/transcript walk (a timed-out walk reads as an empty result); NEVER `--no-build` (stale DLLs mask failures). Pre-commit `/regression_gate` is MANDATORY for `.cs` changes — once, at drive close; mid-drive slices verify by `scripts/verify.ps1 -Scope <domains>`, never by a gate. Meta commits (`.claude/`, skills, docs) are exempt from the gate; a commit staging anything under `.claude/{hooks,tools,scripts,workflows,tests}` or `settings.json` instead needs a green `.claude/scripts/harness_tests.py` stamp (`rules/harness_tooling.md`).

## Development Philosophy: Hybrid TDD
**Identify the domain before writing code.** This section owns the **domain split** (Logic vs Gameplay); [Testing Skill](skills/testing/SKILL.md) owns the **workflow recipes** (RED/GREEN/REFACTOR mechanics, fixture conventions, orphan management). The project-specific subsystem lists for each domain live in *Project Guidelines* below.

- **Logic Domain (Strict TDD):** pure-logic subsystems — data pipelines, math/parsing, data structures, framework core. **NO implementation without a failing test.** Includes `.tres` data file changes that affect Logic behavior — write the test first. Suites live in `Tests/Logic/`. No testing-first exception for an obvious Logic change.
- **Gameplay Domain (Integration + Inspection):** player entity, AI behavior, content lifecycle, VFX, UI, Physics Feel. **AUTOMATE DETERMINISTIC. INSPECT SUBJECTIVE.** Use ISceneRunner for input→outcome, state transitions, physics expectations, signal wiring, scene structure. Reserve manual playtest for "feels responsive?" / timing / juice. Discovery-time feel/model questions (design not yet locked), and closed designs deliberately shipped minimal, both route to `/prototype` — that skill owns both modes and their branch/registry mechanics.

## Developer Tooling Strategy

### 1. Godot MCP (Project Interface — Your Eyes)
Auto-loaded rule: `.claude/rules/godot_files.md` (on `.tscn`/`.tres`/`.godot` reads).

### 2. Memory (One Store, Two Tiers)
File-based auto-memory at `.claude/auto-memory/` is the canonical memory store. The runtime's memory format targets `~/.claude/projects/<project>/memory/`; by project decision that store holds one-line pointers and bodies live here — this overrides the default format. Two tiers, distinguished by `MEMORY.md` index membership. **Hot** — topic files listed in `MEMORY.md`; the index auto-loads at SessionStart, so these are always top-of-mind (200 lines / 25KB is the hard truncation point, NOT the target). **Agent-maintained**: when writing a new hot topic file, add its one-line pointer to `MEMORY.md` in the *same turn* (no separate hooks/workflows — `archive/feedback_memory_md_is_auto_managed.md`), and keep the index ≤~200 lines so it stays under the cap. **Cold** — files under `archive/`, NOT listed in `MEMORY.md`: zero passive context cost, fully searchable via semantic-search (the main index covers `.claude/`), holding archived domain buckets and large single-feature archives.

**Placement** — route by *when the decision is made*: before any file is open → **hot** topic file + `MEMORY.md` pointer; with a file of a prefix-anchored class open → `rules/<name>.md` + `paths:`, as a split (rule moves, evidence file stays); on deliberate domain entry or bulk reference → **cold** under `archive/`, no pointer. Standard: `instruction_quality` §5 A1–A5; destinations: `/codify` §Step 4. Demotion is not deletion — cold stays fully searchable, so tier moves carry no claim-re-verification burden.

**Admission, not headroom:** default to cold. A hot pointer is justified only when the decision it pre-empts fires before any search would run; the index is capped by the runtime, so admitting a line costs one already there (arithmetic: `/codify` Step 5). "Surprising", "cost an hour", "will recur" justify a *memory*, never a hot one: cold is searchable and search IS the recall path. A memory-domains trigger exists → `archive/` directly, no pointer. Save surprising constraints and preferences, not API inventories, code descriptions or facts recoverable from git. **Recall** — search with `mcp__plugin_semantic-search_semantic-search__search` (NL paraphrase; for broad discovery, search facets separately); pass `restrictToDir` as a repo-relative posix path (`gotcha_semantic_search_restricttodir_posix.md`). Use Grep for literal field values / UIDs.

### 3. Obsidian (The Design Source)
Source of truth for design, lore, formulas, Jmodot framework docs. Trigger: lore/formulas/design rules/todos/framework research. The vault is a normal filesystem path (`{{VAULT_ROOT}}\DevProjects\{{PROJECT_NAME}}\`, and `...\Jmodot\`) — **native `Read`/`Write`/`Edit`/`Grep`/`Glob` are the default** (confirmed safe even on docs open in the Obsidian app). Full conventions: `obsidian_conventions` skill (auto-loads).
*   **Read** ONLY within `DevProjects/{{PROJECT_NAME}}` or `DevProjects/Jmodot`; synthesis-shaped reads still route to `read_files` (§9). **Write:** project-specific → `{{PROJECT_NAME}}/Claude/`, Jmodot library-general → `Jmodot/Claude/` — **tiebreaker:** useful in another game built on Jmodot ⇒ `Jmodot/Claude/`; templated or mechanical docs and section-scale restructures route to `write_doc`; assessments, reviews, design verdicts and retrospectives are written directly (§9). **Search first** — don't guess paths. **DO NOT INVENT FORMULAS** — read from vault; ask the user to create if missing. Obsidian MCP only for structured frontmatter/tag edits; MCP-offline doesn't block native read/write/search.

### 4. WebFetch (The Documentation)
Engine and runtime versions are pinned in §Project Guidelines — re-verify there before a version-sensitive claim. For GdUnit4 and library syntax you have no built-in knowledge — **fetch the docs when unsure, never guess.** **Never source a Godot class from docs.godotengine.org** — read the version-pinned cache at `.claude/cache/godot-docs/doc/classes/<Class>.xml` (`scripts/godot_docs_cache.sh` builds it). **Fetch order:** godot cache → `scripts/fetch_source.sh` → `WebFetch` for a single URL → context7 for a resolved library id → `read_web` for multi-page synthesis only → `WebSearch`; per-tier costs and prohibitions: §9. Cache rebuild, class index, quote-checkability, GitHub raw URLs, trust tiers: `reference/source_trust.md` §Fetch order. Inspect source coverage before asserting absence.

### 5–6. WebSearch & Git
*   **WebSearch:** research fallback for obscure errors/bugs — ONLY if docs are silent or for specific engine bugs. **Git commits:** after each successful feature, propose a commit (don't push without instruction); default to multiple categorical commits (feat/fix/refactor/chore) split by logical category unless told otherwise, Git-standard messages. Separate unrelated staged changes and respect submodule/peer ownership.

### 7. C# LSP Plugin (Code Intelligence)
Auto-loaded rule: `.claude/rules/csharp_lsp.md` (on `.cs` reads).

### 8. Semantic Search MCP (Natural-Language Code Discovery)
**Tool:** `mcp__plugin_semantic-search_semantic-search__search` (companion skill `semantic-search:search`). Index `.search-index/search.db` goes stale after edits — refresh via `/reindex_search` (auto in `/session_end`). Engine, indexed file types, ranking: `reference/semantic_search.md`.

*   **USE for** "where is X done", prior-art-for-Y, conceptually-similar-code — when you don't know symbol names yet. **NOT for** call-site enumeration (LSP `findReferences`) or `.tres`/`.tscn` field-value queries (Grep). Memory rules/gotchas live in the indexed `.claude/auto-memory/` store — semantic-search IS their recall path. **Composition order:** semantic-search (code AND memory rules) → LSP `findReferences` → Grep (literal anchors). **Caveat:** large `partial class` files chunk as one block (cosine drops; BM25/symbol/path carry — drop to LSP); heading-mention chunks can outrank canonical declarations — sharpen with `restrictToDir` or use LSP `findReferences`. If a search is empty, confirm scope/ignore behavior with git-aware enumeration before claiming absence.

### 9. Tool Routing — Pre-Call Litmus
Ask which tool BEFORE the call — each prohibition below names its positive target.

- **NEVER** bare-`Grep` a PascalCase identifier on `.cs` — anchor-then-navigate: `Grep("class X")` → LSP `documentSymbol` → `findReferences`; a bare grep returns declarations and mentions indistinguishably. Verified-unique names carve out with stated justification (`csharp_lsp.md`).
- **NEVER** bare-`Grep` a PascalCase identifier on `.tres`/`.tscn`/`.gd`/`.md`/`.godot`/`.json`/`.yaml`/`.toml`/`.txt`, or a fuzzy phrase you cannot anchor literally — route to `semantic-search`.
- **NEVER** chain ≥3 reads/searches for synthesis, or `Read` a synthesis-shaped vault path (`Design/`, `Planning/`, `BrainstormingDesigns/`, `Documentation/`, `Retrospective/`, `Audit/`, `Brainstorm/`, `Meetings/`, `Architecture/`, `Review/`, `Postmortem/`) — bundle into one `read_files(paths=[...], question=...)`, single small docs included. Surgical-edit reads are exempt, and known, focused evidence for a line-level judgment is read directly — do not force a small audit/debug read through a lossy summary because of its path or a read counter.
- **NEVER** leave a recursive `grep -r`/`rg`/`find` unbounded — bound the WALK (`-l`/`-c`/`-m1`, narrower root). `| head -N` bounds only output: SIGPIPE reaches the producer on its next WRITE, so a silent walk runs to completion. Output size is unknowable pre-call and lands in context permanently.
- **NEVER** let `grep -r`/`find` stand in for a gitignore-aware search — they sweep `.claude/worktrees/`, whole extra checkouts whose hits are indistinguishable from real ones. Bounding does not fix scope; use `Grep`, `git grep`, or `git ls-files`.
- **NEVER** fetch a doc page ad hoc — follow §4's order; a single URL goes to `WebFetch` direct, and `curl` at an external SOURCE routes through `scripts/fetch_source.sh`. `read_web` is the multi-page SYNTHESIS tier ONLY: it spends dollars and truncates silently, so read its per-URL `raw=/seen=` preflight before recording any negative.
- **NEVER** route `.claude/` markdown through `write_doc`/`write_code` — direct `Edit`, and load `instruction_quality` BEFORE the first harness edit; worker prose drifts from house voice, and this file is loaded doctrine. In the Obsidian vault: `write_doc` for templated or mechanical docs and section-scale restructures of existing prose; direct `Write`/`Edit` for judgment-dense docs (assessments, reviews, design verdicts, retrospectives), sub-paragraph touch-ups, and findings whose structure is dictated upstream — and name the class in the writing turn, since `routing_audit.py` logs every vault write and an unclassified direct one reads as a silent bypass. Mermaid/structured blocks are preservation-required — say so in every `write_doc` spec.
- **Large exact scans, counts or joins** use deterministic extraction with bounded output: scan the complete intended source and report missing/unparseable inputs separately. A failed or degenerate worker return is incomplete, not evidence — recover existing output before another dispatch.
- Batch likely deferred tools when a discovery tool is available; otherwise use the registered tools directly. Loaded schemas consume context; disk edits do not evict loaded instructions or by themselves prove a cache reset. Queue non-load-bearing harness edits per `/apply_harness_edits`, which owns load-mode distinctions and the `MEMORY.md` same-turn exemption.

`Grep` stays correct for literal field values, UID hashes, regex alternation, attribute markers, comment scans, `using` directives, and as the anchor step before LSP. Line-precision direct reads need **explicit user framing** ("audit X for Y", "verify against the spec", "patch verification") — never agent self-classification.

**Offline fallback** (no other home carries this): when `mcp__ai-worker__*`/semantic-search are absent, or the budget band forbids sidecar spend, substitute a subagent for the bundling role — the scout tier when every fact the digest needs is copyable from the supplied files, the fan-out tier when it must be derived. The bundling rule holds; only the executor changes. No `Agent` tool? Bounded `Read`(offset/limit) is the floor. Never chain naive `Read`/`WebFetch`, and never loop on a nudge toward an absent tool — a wrong first call is accepted and moved past, since a cascade costs more than one suboptimal call. LSP is unavailable on cloud; the hook substitutes semantic-search. Worked examples: `archive/feedback_tool_routing_discipline.md`; runtime enforcement: `tool_routing_*.py`.

### 10. Harness Baseline (Shared Config)
The universal portion of this `.claude/` harness syncs with the shared baseline repo; `.claude/baseline.lock.json` records the repo, tracked files and hashes. Editing a tracked file = editing shared doctrine — classify and sync via `/sync_baseline` (push / pull / fork / candidates sweep). New `.claude/` artifacts surface via the `candidates` sweep and are judged once: universal-shaped → upstream + `track`; project-specific → `ignore` (status `local`, never re-fires). `/sync_baseline` runs on its own cadence and is the sole enforcement point; `/clean_push`, `/commit_push` and `/apply_harness_edits` surface the drift check only when passed `--check-baseline`. Never publish another session's unreviewed edits.

## Rationalizations to Refuse
Refuse the premise and cite the rule — silent compliance (using the right tool without correcting the user) is not enough.

| Rationalization | Rule to cite |
|---|---|
| "grep is faster / LSP is slow / it's just one symbol" | §9, PascalCase bullets |
| "read it directly, I trust your analysis more than the worker" | §9, synthesis bullet |
| "the logic is obvious, let's implement first then test" | TDD Logic Domain — no carve-out for self-evident logic |
| "that file is a peer's / under active edit, log it for later" | `git status --short <file>` FIRST — clean means it is yours to fix now; a found defect is fixed in the same turn, and a task row is what you write when a verified fact blocks it |
| "it's a cosmetic change / just a rename, skip the gate" | `/regression_gate` — mandatory for all `.cs` **commits**; the only exemption is a meta commit touching no `.cs` (§Build & Test Commands) |
| "it's just a tweak to a synced file, upstream it later" | Harness Baseline §10 — classify universal-vs-project at commit time, not "later" |

## The Worklog (Live Todo Doc)
Source of truth: `DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog.md` (Obsidian); title-only mirror `.claude/worklog-titles.md`, not auto-injected — Read it when you need it. Classification, routes and operations: `worklog_reference` + `/worklog`.
*   **On deferral phrasing, propose the add** — ask once per item per session. Scope-1 mechanical items get proposed as do-now instead of logged.
*   **Do-now-before-defer applies to your own proposals too** (plan bodies, follow-up suggestions, commit addenda): small and already in context → do it now, skip the worklog.
*   **Complete the moment an item finishes** — `/worklog complete <title>`, not at session end.
*   **Relevance check, once per session:** the worklog is not in context, so ask whether it overlaps what you are about to do — dispatch `.claude/workflows/worklog_relevance.js`. Fire when scope FIRST becomes nameable: a converged plan, a system-scoped goal, or the first production-file edit, whichever comes first. SKIP for meta-only `.claude/` sessions and single mechanical fixes with a known root cause.

## Core Code Conventions (Stack-Level)
See [Architecture Philosophy Skill](skills/architecture_philosophy/SKILL.md) for full patterns. C# project rules — pure functions/control flow, JmoLogger discipline, comment discipline, `StringName`, `[Tool]` policy — auto-load via `rules/csharp_patterns.md` on `.cs` reads (gate-enforced: `/regression_gate` 1c).
*   **Naming — follow the tool that authors the file.** `PascalCase` for directories and `.cs` files/classes; `snake_case` for Godot-authored assets (`.tscn`, `.tres`, `.gdshader`, `.gd`). `.py` tooling follows PEP 8 snake_case. `.md`/`.png` are ungoverned — match the neighbours.
*   **Harness file edits** (`.claude/CLAUDE.md`, `skills/*/SKILL.md`, `commands/*.md`, `hooks/*`): match peer-content density. Load-bearing info only — no dated user quotes, no redundant restatements, no defensive over-explanation. Companion: `instruction_quality` skill — routing is by FILE CLASS, not edit size: a one-word fix to a `.claude/` file is still a harness edit.

## Shell Discipline
*   **Bash cwd persists across calls** — a bare `cd` retargets every later `git`, which succeeds and reports the other repo's truth; use `git -C <path>` / `dotnet build <path>`.
*   **Multi-line Python → a file.** A python program piped through a heredoc fails on `unexpected EOF` or an escape error whenever its body carries nested quotes or backslashes. `Write` the script and run `python3 <file>`. Any Python that writes a repo file passes `newline="\n"` (or writes bytes): Windows text mode emits CRLF, and a CRLF Workflow script is denied at dispatch (`hooks/workflow_script_lf_guard.py` self-heals `.claude/` scripts; `python3 .claude/tools/lf_normalize.py` sweeps the rest).
*   **Git commit (multi-line):** write the message to a scratchpad file and `git commit -F <file>` — never `/tmp` (Windows `git` cannot read the unconverted path) and never `git commit -m "$(cat <<'EOF' ...)"`.
*   **Git Bash path conversion:** prefix `export MSYS_NO_PATHCONV=1` on any `rev:path` or colon-bearing `gh` argument — Git Bash rewrites `a:b` as a PATH list and the error misnames the REVISION as ambiguous.
*   **Engine invocations:** `bash .claude/scripts/godot_bin.sh <args>` (statically allowlisted), never the raw `"$GODOT_BIN"` form — allow rules match EXPANDED text, so runtime env expansion can never match a rule. Quote paths with spaces; never backslash-escape whitespace.
*   **Background shells:** a shell nobody watches is a hang waiting for a human — `hooks/shell_census.py` lists every claude-spawned shell older than 45 min on each prompt; decide kill-or-keep on sight, never wait on it. Prove process ownership before any kill; never kill a peer from a path substring or idle label.
*   **Auto mode:** the classifier judges edit CONTENT, not allow rules or chat approval — a `.claude/` edit that changes a halt valve, approval step or attempt cap needs a non-auto permission mode for that session (`acceptEdits`), and a denial is the signal to stop and say so, not to retry through another tool.

## Model Delegation (spec-time routing)
Two delegation decisions are made *while writing the spec* — strictly before any dispatch is attempted, so nothing that loads at dispatch time can deliver them in time. They are the only two that live here.
*   **Route by COPYABLE vs DERIVED.** Is every fact the delegate needs already present in the material you are handing it, or must some be inferred, ordered, or chained? Copyable → the free local tier or a scout dispatch. Derived → an executor tier or above. You know which one you wrote; the delegate cannot tell you afterwards, and the failure is silent — a model that cannot chain still returns a confident answer.
*   **State which currency a fan-out spends before dispatching it.** Plan-quota routes spend prepaid quota billed PER MODEL, so cheap-by-tokens is not cheap-by-quota; the sidecar spends marginal dollars; the local tier spends neither. The sidecar's band floor is guarded at dispatch (`hooks/sidecar_dispatch_context.py`); which currency it spends is not.

Which model fills which role, and the evidence behind it: `reference/model_ladder_evidence.md` §Role guidance — load it whenever you pin. Framework: `rules/model_delegation.md`. How to dispatch — mechanism, effort defaults by work shape, budget bands, availability, transport, delegation grain, spec discipline: [`orchestration`](skills/orchestration/SKILL.md). Requested pins are not observed identity; keep compaction enabled for ordinary long jobs.

## Preferences
*   **Plan-file format**: plan files are context-free execution docs (executor + reviewer) — resolved design as fact, rationale inline, no process-meta sections; resolve user-facing forks via `AskUserQuestion` before approval, never leave them open in the plan. Full rule: `skills/_brainstorm_shared/plan_file_format.md`.
*   **Unresolved questions**: at the end of each plan, list the unresolved questions to answer, if any.
*   **No performative agreement**: don't open responses with "you're absolutely right!" / "great point!" / "you're right to push back." Restate the requirement, verify against the codebase, or just fix it. Actions speak. See `feedback_no_performative_agreement.md`.
*   **Bullet headlines say the thing, not the category**: a bolded lead-in must state the actual point in plain language on its own — never a vague label or a jargon-stacked phrase requiring decode. Applies to any user-facing doc or output a session writes (digests, summaries, retrospectives, PR descriptions) — run bulleted prose through `instruction_quality` §6/§6b before finalizing.

<!-- ===== BASELINE:core END ===== -->

## Project Guidelines
<!-- PROJECT-OWNED — everything below is yours; it is never synced. Fill in at adoption. -->

**{{PROJECT_NAME}}**: Godot 4.x (<physics engine>), C# (.NET <version>), Jmodot framework. Concept: <one-line game concept>. Pin the exact engine and runtime versions here — §4 re-verifies against this section before any version-sensitive claim.

### Domain Split (feeds Hybrid TDD above)
*   **Logic Domain (Strict TDD):** <list your pure-logic subsystems, e.g. `Jmodot.Core`, `Inventory`, `Math/Parsing`, data pipelines>
*   **Gameplay Domain (Integration + Inspection):** <list your gameplay subsystems, e.g. player entity, enemy AI BT, content lifecycle, VFX, UI>

### Project Domains (extends Proactive Context Loading)
Add your content domains to `reference/memory_domains.md` and mirror them in `hooks/plan_memory_reminder.py` `DOMAINS`.

### Project-Specific Conventions
*   <add conventions that only make sense in this game — content taxonomies, naming, subsystem invariants>
*   Register subsystems in `skills/project_subsystems/SKILL.md` (consumed by `/sync_subsystems`, `/structure_audit`, and brainstorm scope litmus).
*   Capture the game's design bible in `skills/game_vision/SKILL.md`.
