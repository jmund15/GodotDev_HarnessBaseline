---
disable-model-invocation: true
allowed-tools: Bash(git ls-files:*), Bash(find:*), Bash(ls:*), Glob, Grep, Read, Workflow
description: Audit project file/folder structure against the codified ruleset (snake_case, single-file folders, root clutter, UI rubric, framework boundary, orphan resources). Advisory; does NOT block commits.
---

Audit the entire {{PROJECT_NAME}} project structure for organizational consistency. Delegates analysis to 3 specialized subagents running in parallel, then consolidates findings.

**This command is advisory — it does NOT block commits.** Findings are improvement opportunities, not failures. The audit covers `.cs`, `.tscn`, `.tres`, assets, configs — every file in the tree.

The codified ruleset lives in [`structure_rules.md`](../skills/architecture_philosophy/structure_rules.md). Read that file to understand what the audit is actually checking.

---

## Phase 1: Scope & Load

### 1a. Build the project file manifest

Use `Glob` and `find` to enumerate every file/folder under the project root, **excluding**:
- `.git/`, `.godot/`, `.config/`, `bin/`, `obj/`, `TestResults/`
- `addons/` (third-party plugins — own conventions)
- `Jmodot/` contents (submodule has its own layout) — but DO list `Jmodot/` itself for the R11 boundary check (handled by stra-reference-integrity)
- `.claude/` (this tooling lives by its own conventions)
- `gdunit4_testadapter_v5/`

Build two artifacts:
1. **Top-level inventory** — every entry directly under the project root (folders + files).
2. **Full manifest** — every file path in the included scope.

Both go into the `CONTEXT` block. Store paths only — no file contents (the project is too large).

### 1b. Load the ruleset

Read [`structure_rules.md`](../skills/architecture_philosophy/structure_rules.md) in full. This is the agents' grading rubric.

### 1c. Load the documented top-level folder list

Read `.claude/skills/project_subsystems/SKILL.md` and extract the `subsystems:` YAML block. Concatenate every `paths:` entry across subsystems; that token set is the R12 reference list. Reserved-name exclusions (`.claude/`, `addons/`, `Jmodot/`, `Docs/`, etc.) are listed in the same SKILL.

### 1d. Load auto-memory gotchas

Search auto-memory via semantic-search:
- semantic-search: `structure`
- semantic-search: `organization`
- semantic-search: `framework` (catches `jmodot_framework_boundary_rule`)

Inject any returned content into CONTEXT under "Memory Gotchas".

### 1e. Build C# class symbol manifest

Grep the project for class declarations:
```
Grep("^(public\\s+)?(static\\s+)?(partial\\s+)?(abstract\\s+)?(sealed\\s+)?(class|record|interface)\\s+(\\w+)", glob="**/*.cs")
```

Parse each match into `{ClassName → file_path, namespace}`. Exclude `Tests/`, `Jmodot/` internals (Jmodot has its own audit), `bin/`, `obj/`, `.godot/`, `addons/`. Inject into CONTEXT under "Class Symbol Manifest".

This manifest is consumed by `stra-reference-integrity` Process step 4(b)/(c) to verify that every `ext_resource type="Script"` and every `script_class="..."` attribute in `.tres` headers references a class that still exists in the codebase. Cloud-compatible (Grep, no LSP dependency).

---

## Quick mode: `/structure_audit --quick=references`

When invoked with `--quick=references`:
- Skip Phase 1c (project_subsystems top-level folder list) — not needed.
- Skip Phase 1d (auto-memory gotchas for layout/organization) — not needed.
- Phase 1e (class symbol manifest) **IS** built (it's the whole point).
- Phase 2 spawns ONLY `stra-reference-integrity`. Do NOT spawn `stra-layout-hygiene` or `stra-domain-coherence`.
- Time bound: **under 2 minutes**. Suitable for post-rebase / post-rename invocation.
- Verdict thresholds unchanged.

This mode exists for fast feedback after events that introduce reference drift risk: rebases, class renames, file moves. Wired into `/clean_pull` to auto-fire when the pull includes `.cs` rename/delete operations. Cloud-compatible — no Godot MCP, csharp-ls, or Obsidian dependencies.

---

## Phase 2: Launch Audit Sub-Agents

The fan-out + Step-1 consolidation run deterministically in the `review-fanout` workflow. Assemble each agent's prompt (templates + CONTEXT block below) and dispatch through the engine — it runs them in parallel, appends the read-only / no-tests / no-LSP single-flight guard, and merges/dedups/sorts findings.

**Roster:** full mode = all 3 agents; `--quick=references` mode = ONLY `stra-reference-integrity`.

```
Workflow({
  scriptPath: ".claude/workflows/review_fanout.js",
  args: { agents: [
    { key: "stra-layout-hygiene", prompt: "<assembled>", model: "sonnet" },
    { key: "stra-domain-coherence", prompt: "<assembled>", model: "opus" },
    { key: "stra-reference-integrity", prompt: "<assembled>", model: "opus" } ] }
})
```

It returns `{ findings: [...deduped, sorted...], counts }`. Do not spawn agents manually.

### Agent Templates

Use the templates in [`structure_audit_agents.md`](agents/structure_audit_agents.md):
- `stra-layout-hygiene` (sonnet) — Tier 1 mechanical rules R1–R5, R13
- `stra-domain-coherence` (opus) — Tier 2 philosophy rules R6–R10
- `stra-reference-integrity` (opus) — Tier 3 boundary rules R11–R12 + orphans + cross-refs

### Shared CONTEXT Block

Assemble a single `CONTEXT` string injected into every agent prompt. It MUST contain:

1. **Project root path:** `{{PROJECT_ROOT}}/`
2. **Top-level inventory** (from Phase 1a)
3. **Full file manifest** (from Phase 1a) — paths only
4. **structure_rules.md full text** (from Phase 1b)
5. **Documented top-level folder list** (from Phase 1c) — for R12 comparison
6. **Memory Gotchas** (from Phase 1d)
7. **Folder→Style Map** — extracted from the `## Folder → Organizational Style Map` section of structure_rules.md. Agents need this to know which philosophy applies per folder.
8. **Finding Schema reference:** point to `/.claude/commands/agents/orchestrator_action_protocol.md`

---

## Phase 3: Consolidate & Report

After ALL 3 subagents return, follow the **Orchestrator Action Protocol** defined in [`orchestrator_action_protocol.md`](agents/orchestrator_action_protocol.md):

1. **Merge & deduplicate** findings across agents (same `file:line` from two agents → keep the more specific one or the `critical: true` one).
2. **Sort:** critical first, then FIX → ASK → PLAN, then bug → rule → improvement.
3. **Present unified report** in this format:

```
╔══════════════════════════════════════════════════════╗
║          STRUCTURE AUDIT - [DATE]                     ║
╠══════════════════════════════════════════════════════╣
║ Folders Scanned: X                                    ║
║ Files Scanned:   Y (.cs) + Z (.tscn/.tres) + W (assets)║
║ Findings:        FIX:N  ASK:M  PLAN:K                ║
║ Categories:      clutter:N  philosophy:M  orphan:K   ║
║                  boundary:N  doc-drift:M             ║
╚══════════════════════════════════════════════════════╝
```

Then findings grouped by tier per the Action Protocol's Step 2 format.

### Verdict

Rate the project's structural health:

- **CLEAN** — 0 critical, ≤3 total findings. Layout is in good shape.
- **MINOR POLISH** — 0 critical, 4–15 findings. Address opportunistically.
- **REVIEW RECOMMENDED** — 1+ critical findings (R11 boundary leak, broken `ext_resource`), or 16+ findings overall. Worth a focused cleanup pass.

### Notes synthesis (orchestrator-only)

After agent findings, MAY add a `## Notes` section if cross-agent patterns emerge:
- Multiple agents flagging the same subsystem → systemic gap
- A pattern across files suggests a missing rule worth adding to `structure_rules.md`
- The Folder→Style Map is missing a row that the audit kept catching

This section is orchestrator-generated — agents never produce NOTE findings.

---

## Phase 4: Execute Actions

Follow the Action Protocol's Step 4. Default flow: walk through ALL tiers in order. Confirm with the user once at the start, then proceed:

> "Ready to proceed? I'll apply the N FIX changes (renames, deletes, moves), then walk through M ASK items for your input, then we'll discuss K PLAN items if any."

**FIX tier:**
- Apply mechanically using the `old`/`new` paths in each finding.
- For file MOVES/RENAMES: BEFORE moving, Grep the entire codebase (excluding `.git/`, `bin/`, `obj/`) for inbound references using both the old basename AND the file's UID (read from the `.cs.uid` or the resource header). Update every reference site in the same operation. Use git `mv` rather than raw `mv` so history is preserved.
- For file DELETES: confirm zero inbound references exist (Grep). If any reference exists, escalate to ASK.
- After each batch, verify project still builds (`dotnet build` once at end of FIX tier — not per-fix).

**ASK tier:**
- Walk through each one at a time, presenting question + ranked options.
- "yes" / a number → apply that option.
- "skip" → defer, continue.
- For folder restructures (single-file folder inlines, UI subsystem moves), apply the same reference-update discipline as FIX.

**PLAN tier:**
- Present each with options.
- User chooses: address now (enter plan mode), defer (Obsidian TODO), or dismiss.

---

## Constraints

- **Read-only by default.** No file moves, renames, or deletes without explicit per-tier approval.
- **Reference-safe MOVES.** Any `.cs`/`.tscn`/`.tres` relocation MUST update inbound references atomically. Verify with Grep BEFORE the move and again AFTER.
- **No feature changes.** This is a layout audit, not a refactor of behavior.
- **Substantive only.** Skip cosmetic preferences without rule backing. Only report what `structure_rules.md` codifies or what reduces measurable navigation friction.
- **Submodule-aware.** `Jmodot/` is the framework submodule. Do NOT audit its internal layout (it has its own conventions). DO check `Jmodot/` files for R11 boundary leaks via stra-reference-integrity.
- **Time-bounded.** Full audit (spawn → consolidate) under 10 minutes.
- **3-agent fan-out lives in the `review-fanout` workflow.** Do not perform inline or re-spawn manually. (`--quick=references` passes only `stra-reference-integrity`.)

---

## When to run

Suggested cadence:
- After any session that adds 3+ files or introduces a new folder
- Before a PR that includes structural changes (new top-level system, large refactor)
- Periodically (monthly-ish) as a hygiene pass

NOT wired into `/session_end` — full-project scan is too noisy for per-session use. An opt-in `--quick` mode (audit only session-touched folders) may be added later.
