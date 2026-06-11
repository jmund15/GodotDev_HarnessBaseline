---
disable-model-invocation: true
---

Generate the full documentation suite for a system (usage + architecture + retrospective).

## Arguments
- `$ARGUMENTS` — The system/feature name to document (e.g., "DynamicMeshTrailSystem"). If omitted, infer from this session's development history.

## Workflow Overview

```
Phase 1: SETUP             (identify system)
Phase 2: PARALLEL DOCS     (delegate 3 subagents, one per doc command)
Phase 3: QUICK REFERENCE   (consolidate into hub document)
Phase 4: START HERE UPDATE  (reconcile Start Here.md with folder structure)
```

---

## Phase 1 — Setup

### 1a. Identify the System
Determine the system name from `$ARGUMENTS` or the session's development history. This becomes `{SystemName}` for all subagent prompts and file paths.

### 1b. Apply Obsidian Conventions
Read `agents/documentation_structure.md` (folder structure) and the `obsidian_conventions` skill (vault tooling), and apply:
- **Normalize name** — PascalCase, no spaces, strip redundant domain prefixes
- **Determine domain folder** — read Start Here "By Domain", route to `{Domain}/{SystemName}/`
- **Check MCP** — verify online, check if doc folder exists (pass to agents as context)

---

## Phase 2 — Parallel Documentation

Spawn all 3 agents **in parallel** using the Task tool in a **single message** (one tool call per agent).

**Agent waiting strategy:** Launch all agents **without** `run_in_background` in a single message. They execute in parallel and all results return together when the slowest agent finishes — no polling or manual checking needed. Do NOT use `run_in_background: true` followed by `TaskOutput` polling.

**Model:** Use `opus` for all 3 agents (creative + technical depth required).

### Usage Agent

> You are writing documentation for the **{SystemName}** system in a Godot 4.6 / C# project.
>
> Read `.claude/commands/doc_usage.md` for the complete documentation procedure, then execute it step by step for this system.
>
> **System name:** `{SystemName}`
> **Doc folder:** `{resolved doc folder path from Phase 1c}`
> **Folder exists:** {yes/no}
>
> You have full access to the codebase, the Obsidian vault, and the ai-worker `write_doc` tool (per your command file, doc prose is generated via `write_doc`, not typed by hand). Explore the source files independently — focus on `[Export]` properties, configuration points, `.tres` resources, and editor-facing interfaces relevant to this system.

### Architecture Agent

> You are writing documentation for the **{SystemName}** system in a Godot 4.6 / C# project.
>
> Read `.claude/commands/doc_architecture.md` for the complete documentation procedure, then execute it step by step for this system.
>
> **System name:** `{SystemName}`
> **Doc folder:** `{resolved doc folder path from Phase 1c}`
> **Folder exists:** {yes/no}
>
> You have full access to the codebase, the Obsidian vault, and the ai-worker `write_doc` tool (per your command file, doc prose is generated via `write_doc`, not typed by hand). Deep-read ALL source files for this system — map class hierarchies, signal connections, dependency chains, design patterns, and extension points. Read relevant tests to understand behavioral contracts.

### Retrospective Agent

> You are writing documentation for the **{SystemName}** system in a Godot 4.6 / C# project.
>
> Read `.claude/commands/doc_retrospective.md` for the complete documentation procedure, then execute it step by step for this system.
>
> **System name:** `{SystemName}`
> **Doc folder:** `{resolved doc folder path from Phase 1c}`
> **Folder exists:** {yes/no}
>
> You have full access to the codebase, the Obsidian vault, the ai-worker `write_doc` tool (per your command file, doc prose is generated via `write_doc`, not typed by hand), auto-memory, git history, and transcript backups at `logs/transcript_backups/` (index: `logs/pre_compact.json`). Follow the command file's Context Tiers to determine what depth of retrospective is appropriate based on available sources.
>
> **Note:** If no meaningful development history exists for this system (Tier 3), abort gracefully — that is expected behavior, not an error.

### 2b. Collect Results
After all agents complete, note which docs were successfully written:
- Usage: written / updated / failed
- Architecture: written / updated / failed
- Retrospective: written / updated / aborted (Tier 3) / failed

Proceed to Phase 3 regardless of retrospective outcome.

---

## Phase 3 — Quick Reference Hub

After all subagents complete, create or update `Quick Reference.md` in the system folder.

### 3a. Read the completed docs
Use `obsidian_read_note` to read the Usage, Architecture, and Retrospective docs that were just written. Extract:
- Key types and their roles (from Architecture)
- Key configurable properties (from Usage)
- File paths (from Architecture)
- Last-updated dates (from each doc's Changelog)

### 3b. Write Quick Reference

Place at: `{resolved doc folder path}/Quick Reference.md`

```markdown
# {SystemName} — Quick Reference

> [!abstract] System Overview
> 3-5 sentence high-level summary of what this system is, what it does, and its role in the project.
> Written for someone who has never seen this system and needs to decide which doc to read.

## Document Index

| Document | Description | Last Updated |
|----------|-------------|--------------|
| [[Designer Usage]] | Editor workflow, configuration, and in-game behavior | YYYY-MM-DD |
| [[Architecture]] | Code structure, design decisions, and extension points | YYYY-MM-DD |
| [[Retrospective]] | Development history and decision journal | YYYY-MM-DD |

## Key Reference

### Core Types
| Type | Role | File |
|------|------|------|
| {ClassName} | {One-line responsibility} | `{file_path}` |
| ... | ... | ... |

### Key Properties / Exports
| Property | Type | Where | Description |
|----------|------|-------|-------------|
| {PropertyName} | {Type} | {ClassName} | {What it controls} |
| ... | ... | ... | ... |

### Pure Static Methods (if applicable)
If the system exposes testable pure static methods (Logic Domain), list them here:
| Method | Purpose |
|--------|---------|
| `{MethodName}()` | {One-line description} |
| ... | ... |

Omit this section entirely if the system has no pure static methods.

### File Map
Source files belonging to this system:
- `Scripts/{path}` — {brief description}
- `Resources/{path}` — {brief description}
- `Scenes/{path}` — {brief description}
- `Tests/{path}` — {brief description}

> [!info] Related Systems
> - [[{OtherSystem}/Quick Reference|{OtherSystem}]] — How it relates
> - ...
```

### Quick Reference Update Rules
- **New:** Create the full structure above, populated from the three docs just written.
- **Existing:** Read it first. Update the Document Index dates, refresh the Key Reference tables based on current codebase state, and update the File Map if files were added/removed/moved.
- Curate the Key Reference table — include only the 5-10 most essential types and properties. This is a quick reference, not an exhaustive list.

### 3c. Enforce Bidirectional Links

After writing the Quick Reference, ensure every system referenced in the Related Systems callout links back to this system.

For each system `B` listed in this system's (`A`) Related Systems:
1. Read `B`'s Quick Reference via `obsidian_read_note`
2. Check if `B`'s Related Systems callout already mentions `A`
3. If not, use `obsidian_search_replace` to append a return link entry to `B`'s Related Systems callout

This prevents the most common audit finding (X1: unidirectional links). All cross-references must be bidirectional.

---

## Phase 4 — Start Here Reconciliation

After the Quick Reference is written, read `.claude/commands/doc_start_here_update.md` and execute it as an inline procedure (not a subagent). This ensures the newly documented system appears in `Start Here.md` with correct:
- Domain table entry (By Domain)
- Role table entries (By Role: Designer and/or Developer)
- Entry point link pointing to the Quick Reference created in Phase 3

If the system was already in Start Here with up-to-date links, the command will detect no diff and exit without rewriting.

---

## Constraints

- **Delegate docs to subagents** — this command orchestrates, does not define document structure or templates
- **Each agent reads its own command file** — template updates propagate automatically without changing this orchestrator
- **Quick Reference is the only doc written directly (NOT via `write_doc`)** — it needs cross-doc synthesis the subagents lack, and it is a structured landing page (tables + wikilinks + a 3–5 sentence overview), not free prose. This is the documented carve-out to the Documentation Delegation Rule; the three suite docs all route through `write_doc`
- **Retrospective may abort** — Tier 3 (no history) is expected behavior, not failure

## Formatting & Writing Rules
All formatting, wikilink, and vault-tooling rules are in the `obsidian_conventions` skill.
- Keep Quick Reference SHORT — it's a 1-page landing page, not a comprehensive doc.
- Tables over prose. Links over descriptions.
