---
name: Refactor Procedure
description: >-
  Use to refactor, move, rename, deprecate, migrate or consolidate C# when scene/resource references must stay in sync. SKIP architectural redesign; use architecture_brainstorm first.
---

# Refactor Procedure

## Procedure

1.  **Search:** Use LSP `findReferences` for C# symbol references, plus `Grep` for `.tscn`/`.tres` text references (LSP doesn't cover resource files).
2.  **Plan:** List files that will break. Identify if scene files need to be recreated (if source folder deleted).
3.  **Execute:** Move/Rename the file. If folder is deleted, create new scene file with same UID.
4.  **Patch:** Update `.tscn` text:
    *   `[ext_resource]` paths to new locations
    *   Node names if convention changed (e.g., `CauldronUI` → `PotionUI`)
    *   Node path references in properties
    *   *Caution:* Preserve existing UIDs when file moved (same UID, new path). Remove `metadata/_custom_type_script` when script path changes (auto-regenerated).
5.  **Verify:** Fix ALL scene references BEFORE running a headless import: `bash .claude/scripts/godot_bin.sh --headless --path . --import`. Inspect the asset diffs the import rewrites before continuing.
6.  **Verify Legacy Data:** When deprecating/refactoring exported fields:
    *   Search `.tres` and `.tscn` files for references to the old pattern
    *   Ensure legacy `[Export]` fields still function (not just present in code)
    *   Test with existing data files to catch silent breaking changes
    *   Parity can preserve defects: read each relocation destination as well as its source, and verify deletion and migration coverage
7.  **Regression gate:** each slice runs its items' targeted tests, the refactor's close runs `verify -Scope` once, and the drive's close gate backs the commits (`change_control` §Gate cadence).

## Cross-references

- [`testing`](../testing/SKILL.md) — `/regression_gate` step 7; legacy-data tests for step 6.
- [`architecture_philosophy`](../architecture_philosophy/SKILL.md) — Deletion Test before refactoring; *Consume new APIs or migration is incomplete*.
- `.claude/rules/scene_authoring.md` (auto-loads on `**/*.tscn`, `project.godot`, `UI/**/*.cs`) — scene-vs-programmatic construction, teardown anti-patterns.
- `.claude/rules/csharp_lsp.md` (auto-loads on `**/*.cs`) — LSP-first call-site enumeration for step 1.
- `feedback_dont_defer_existing_framework_abstractions.md` — grep BBDataSig + Jmodot.Core before saying "X varies per project".
- `.claude/rules/godot_files.md` (auto-loads on `**/*.tscn`/`**/*.tres`/`**/*.godot`) — `.tres`/`.tscn` format + editor-resave hazards; commit manual edits before opening the editor.
