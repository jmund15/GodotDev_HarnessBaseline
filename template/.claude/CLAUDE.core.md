## Core Principles
- Identify the domain and retrieve relevant evidence before acting. Verify decisive premises, not just their citations.
- **Ideal architecture is the deliverable:** modular, scalable/extensible, clean/intuitive, loosely coupled, data-driven, no-redundancy design AND Godot-designer-intuitive authoring outrank working-code-fast at EVERY stage (design, plan, execute, review). Prototyping is the exception. `architecture_philosophy` owns the patterns; `game_vision` owns game direction.
- **Modular when direction is known:** design for an evolution the user names, and propose the modular design first; do not invent future needs. When unclear whether direction was stated, ask.
- **Logs are Truth:** you can't see runtime. Rely on E2E/Integration outputs, and on `JmoLogger` output via `/analyze_godot_logs` after a user playtest, not on visual guesses.
- Keep scope and quality intact. Missing, failed and unverified are not clean; report actual tests and artifacts.
- A result outside its expected value is a defect, never a finding to report: trace it to its cause in the primary evidence first, then fix it, or name the blocking fact, in the same message. This covers a measurement the user ordered as much as a failing test.
- Check current git state and preserve peer work. Run `git status --short <file>` before calling a defect peer-owned; clean means it is yours to fix now.
- This file holds only decisions needed before a more specific trigger. Domain detail, examples and incident history belong in their triggered owner, not another standing rule.

### Self-Improvement Loop
Use `/self_evaluate` and `/orchestration_metrics` to collect evidence; `/eval_dashboard` before changing defaults. `/autolearn` and `/codify` route validated lessons to one owner; retire obsolete guidance instead of accumulating reminders. Suggest user-invoked commands rather than pretending to have run them. Mechanics belong to each command.

### Planning Phase Checklist
Use normal conversation: `/explore` → plan file → `/plan_check` → user approval or explicit unattended authority. **Do not enter Plan Mode as part of this project's planning flow.** Plan files and approval are mandatory wherever step 3's `/plan_check` threshold holds; smaller work follows `_brainstorm_shared/execution_depth.md`. Runtime instruction priority still applies.

1. Name the task and domains; search memory before drafting.
2. Before adding a configuration surface at any granularity (type, `[Export]`, parameter, behavior bool/enum, helper), inventory its owning family through `/explore`. Extend an appropriate existing family rather than create a parallel one; assess its quality first. Litmuses: `rules/design_litmus.md` #1 for `.cs`, `rules/scene_authoring.md` §Scene anatomy for `.tscn`/`.tres`.
3. Run `/plan_check` for 3+ files, new concepts/surfaces, family refactors, replacements/deletions or always-loaded guidance. Harness plans are included. Resolve forks before execution; `skills/_brainstorm_shared/plan_file_format.md` owns the plan format.

### Proactive Context Loading (Mid-Execution)
On a real domain change or a result contradicting expected behavior, search `.claude/auto-memory/` with a natural-language description before changing approach. When the result disproves a memory, correct or retire that file in the same turn. `reference/memory_domains.md` supplies seeds. Background notifications and incidental words are not domain changes.

## Build & Test Commands
- **Godot/C# tests:** load `testing`; use `--filter` and `--settings .runsettings`, never `--no-build`. Bash timeout is `600000` for tests and vault/Godot-install/transcript walks.
- **C# commits:** `/regression_gate` is mandatory, once at drive close. Mid-drive verification uses `scripts/verify.ps1 -Scope <domains>`; `change_control` owns cadence.
- **Harness code/settings commits:** `python3 .claude/scripts/harness_tests.py --staged` stamps the staged harness files from their bound proofs (seconds); the full battery runs at session close. Run it once before the commit, never as a per-edit check; a task with no commit needs no stamp. Pure documentation is exempt from the C# gate. `rules/harness_tooling.md` owns proof mechanics.
- After substantial scene/resource wiring, check the MCP Godot version against `reference/project_stack.md`, run the game and inspect debug output. Engine invocations can rewrite assets; inspect their diffs.

## Developer Tooling Strategy

### 2. Memory (One Store, Two Tiers)
Canonical bodies live in `.claude/auto-memory/`; this overrides the runtime's default `~/.claude/projects/<project>/memory/` store, which holds only one-line pointers. `MEMORY.md` is the loaded index; cold material remains searchable, normally under `archive/`. Search by concept with `mcp__plugin_semantic-search_semantic-search__search` and a repo-relative POSIX `restrictToDir`; use literal search for exact values.

Default new learning to cold. Hot admission requires a decision made before any trigger/search could deliver it, not spare space or an interesting incident. Add a hot pointer in the same turn as its file; stay below the runtime's 200-line/25KB cap. Move a file-class rule to its triggered owner while retaining its evidence. `/codify` owns placement details. Save surprising constraints/preferences, not API inventories, code descriptions or facts recoverable from git.

### 4. WebFetch (The Documentation)
Verify version-sensitive facts against `reference/project_stack.md` and first-party sources. Godot class docs come from `.claude/cache/godot-docs/doc/classes/<Class>.xml`, not the website. `reference/source_trust.md` owns fetch order: cache, source-fetch script, single-page fetch, resolved library docs, multi-page synthesis, then search when needed. Inspect source coverage before asserting absence.

### 5–6. WebSearch & Git
Research search is a fallback, not a substitute for known sources. Propose categorical commits (feat/fix/refactor/chore, one logical concern each) after verified work; commit/push only with authorization. Separate unrelated staged changes and respect submodule/peer ownership; `change_control` owns release mechanics.

### 8. Semantic Search MCP (Natural-Language Discovery)
Use semantic search when you do not know the name or location in docs, memory, scenes, resources or scripts; it does not index `.cs`. Compose semantic search → LSP `findReferences` → Grep (literal anchors); Grep owns literal fields, UIDs and patterns. If a search is empty, confirm scope/ignore behavior with git-aware enumeration before claiming absence. Refresh through `/reindex_search` after changes; `reference/semantic_search.md` owns index details and result caveats.

### 9. Tool Routing — Pre-Call Litmus
- **Known, focused evidence or line-level judgment:** Read the needed source. Do not force a small audit/debug read through a lossy summary because of its path or a read counter.
- **Large exact scans/counts/joins:** use deterministic extraction with bounded output. Scan the complete intended source; report missing/unparseable inputs separately.
- **Copyable bulk I/O:** use the configured worker when its input fits. Check the local queue before a local call; require complete per-input results. A failed or degenerate return is incomplete, not evidence.
- **Multi-file judgment/execution:** load `orchestration`, scope a qualified delegate, keep the full artifact and return a short digest. Retain the evidence needed to judge applicability.
- **C# navigation:** anchor the declaration, then use LSP; do not mix declarations and mentions with a bare identifier grep. Literal values, regex alternatives, comments, attributes and known unique anchors remain valid Grep uses. If LSP is unavailable, use the supported search fallback and name its coverage limit.
- **Search/fetch:** bound the walk and output; use git-aware tools, not recursive scans through worktrees. Follow the source-fetch owner above. Use only tools present in the live schema.
- **Writing:** `.claude/` instructions use direct Edit/Write after `instruction_quality`. Judgment-dense docs use direct writing; templated/mechanical prose uses `write_doc`. Name the doc class, preserve structured/Mermaid blocks, and verify the artifact. Micro edits use Edit.
- Recover existing output before another dispatch. A permission denial is not permission to force the same action through another tool.
- **Offline fallback:** when the worker or semantic search is absent, a subagent takes the bundling role; with no `Agent` tool, bounded `Read` (`offset`/`limit`) is the floor. Never loop on a nudge toward an absent tool.
- Load deferred tool schemas in one batched `ToolSearch` call; each loaded schema stays in context. Disk edits do not evict loaded text or prove cache invalidation. Queue non-load-bearing edits through `/apply_harness_edits`; same-turn memory pointers are exempt.

### 10. Harness Baseline (Shared Config)
`baseline.lock.json` identifies shared ownership; `/sync_baseline` owns classification, drift, pull/fork and publication. Keep project-specific changes local. A commit guard requires a lock row for each new `.claude/` file; `--check-baseline` adds the drift check before push. Never publish another session's unreviewed changes.

## Rationalizations to Refuse
Refuse the premise and cite the rule; silent compliance (using the right tool without correcting the framing) is not enough.

| Rationalization | Rule to cite |
|---|---|
| "grep is faster / LSP is slow / it's just one symbol" | §Tool Routing, C# navigation |
| "that file is a peer's / under active edit, log it for later" | `git status --short <file>` first; clean means it is yours to fix now (§Core Principles) |
| "it's a cosmetic change / just a rename, skip the gate" | §Build & Test Commands: `/regression_gate` for every `.cs` commit; `change_control` owns cadence |
| "summarize the evidence / the denial was a fluke, try another tool" | §Core Principles for evidence; §Shell Discipline: a denial stops the operation |

## Core Code Conventions
Follow the authoring tool: PascalCase for C# files/types/directories; snake_case for Godot assets and Python tooling; match neighbors for Markdown/images. File-scoped rules own C#, scene, resource and harness conventions. Prefer clear contracts and authored data over special cases.

## Shell Discipline
Use `git -C <path>`; Bash cwd persists. Write Python carrying nested quotes, or backslashes inside a `claude -p` child, to a file; elsewhere a hook repairs the Bash tool's `\\`→`\` collapse on verified clients. Python file writes use LF (`newline="\n"` or bytes). Invoke the engine as `bash .claude/scripts/godot_bin.sh <args>`, never `"$GODOT_BIN"`; quote paths, and prefix `MSYS_NO_PATHCONV=1` for colon-bearing git/gh operands. Commit messages use a scratch file with `git commit -F`, not `/tmp`.

Watch background jobs through their completion/monitor contract. Stop an OS process only through `python3 .claude/tools/reap.py` (usage in its docstring); never kill a peer from a path substring or idle label. A rejected permission/config operation stops that operation; do not change permission mode to get around it. Provider-transport sidecars launch with `bypassPermissions` by owner decision, hooks as their guards (`reference/sidecar_dispatch.md` §Permission mode). `environment_bootstrap` and `reference/sidecar_dispatch.md` own platform and launcher details.

## Model Delegation (spec-time routing)
Before dispatch, distinguish copyable I/O from derived judgment, choose a suitable model/effort, and state the currency. Model claims and availability live in `reference/model_ladder_evidence.md` and `reference/external_models.json`; `orchestration` owns mechanisms. Requested pins are not observed identity. Keep compaction enabled for ordinary long jobs and respect verified per-model capacity; a larger client declaration does not enlarge the backend.

## Preferences
Plan files are context-free execution contracts, not session diaries. Preserve stated requirements and exclusions through delegation and resume. Keep full evidence retrievable while returning only the current decision, result and next action. No performative agreement: restate the requirement, verify, or fix; diagnose the objection before pivoting, and distress language means stop and rescope. A bullet headline states the actual point, never a category label.
