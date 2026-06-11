---
name: Refactor Procedure
description: >-
  Procedure for refactoring, moving, renaming, deprecating, or migrating C# classes/files
  in {{PROJECT_NAME}} when scene/resource references must be kept in sync. Triggers:
  "refactor [class/file]", "move [class/file]", "rename [class]", "deprecate [exported
  field]", "migrate [from] [to]", "unify [X] and [Y]", "consolidate [pattern]". SKIP for
  pure logic refactors with no scene/resource impact (just edit + test), or architectural
  redesigns (use `architecture_brainstorm` first).
---

# Refactor Procedure

## Pre-Workflow Check
- [ ] **auto-memory** searched for refactor-related gotchas (`refactor`, `disposal`, `lifecycle`).
- [ ] **Plan check** considered if this touches 3+ files, introduces a new top-level type, refactors a 2+ subclass family, OR deletes/replaces existing files — see CLAUDE.md *Planning Phase Checklist* §4 (`/plan_check`).
- [ ] **Refactor parity audit committed to** — line-by-line behavior diff old→new before merge; "deferred/stub/TODO" markers are merge-blockers (`feedback_refactor_parity_audit.md`).

## Procedure

1.  **Search:** Use LSP `findReferences` for C# symbol references, plus `Grep` for `.tscn`/`.tres` text references (LSP doesn't cover resource files).
2.  **Plan:** List files that will break. Identify if scene files need to be recreated (if source folder deleted).
3.  **Execute:** Move/Rename the file. If folder is deleted, create new scene file with same UID.
4.  **Patch:** Update `.tscn` text:
    *   `[ext_resource]` paths to new locations
    *   Node names if convention changed (e.g., `CauldronUI` → `PotionUI`)
    *   Node path references in properties
    *   *Caution:* Preserve existing UIDs when file moved (same UID, new path). Remove `metadata/_custom_type_script` when script path changes (auto-regenerated).
5.  **Verify:** Fix ALL scene references BEFORE running Godot `update_project_uids` tool.
6.  **Verify Legacy Data:** When deprecating/refactoring exported fields:
    *   Search `.tres` and `.tscn` files for references to the old pattern
    *   Ensure legacy `[Export]` fields still function (not just present in code)
    *   Test with existing data files to catch silent breaking changes
    *   *Example:* `MultiShotEffect.ChildModifiers` was kept as a property but wasn't being applied after refactor.
7.  **Build:** `dotnet build`.
8.  **Regression gate:** `/regression_gate` is mandatory for `.cs` changes — see CLAUDE.md *Build & Test Commands*.

## Cross-references

- [`testing`](../testing/SKILL.md) — `/regression_gate` step 8; legacy-data tests for step 6.
- [`architecture_philosophy`](../architecture_philosophy/SKILL.md) — Deletion Test before refactoring; *Consume new APIs or migration is incomplete* (`feedback_consume_new_apis_or_migration_is_incomplete.md`).
- `.claude/rules/scene_authoring.md` (auto-loads on `**/*.tscn`, `project.godot`, `UI/**/*.cs`) — scene-vs-programmatic construction, teardown anti-patterns.
- `.claude/rules/csharp_lsp.md` (auto-loads on `**/*.cs`) — LSP-first call-site enumeration for step 1.
- `feedback_refactor_parity_audit.md` — line-by-line behavior diff before merge.
- `feedback_consume_new_apis_or_migration_is_incomplete.md` — audit motivating call sites after introducing a new API.
- `feedback_dont_defer_existing_framework_abstractions.md` — grep BBDataSig + Jmodot.Core before saying "X varies per project".
- `.claude/rules/godot_files.md` (auto-loads on `**/*.tscn`/`**/*.tres`/`**/*.godot`) — `.tres`/`.tscn` format + editor-resave hazards; commit manual edits before opening the editor.
