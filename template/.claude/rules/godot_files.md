---
paths:
  - "**/*.tscn"
  - "**/*.tres"
  - "**/*.godot"
---

# Godot Files (`.tscn` / `.tres` / `.godot`) — Format, Editor-Resave Hazards, MCP Workflow

**Context:** These files are plain text you edit directly, but two things make them treacherous: the Godot editor silently rewrites them on every resave, and `dotnet build` does NOT parse `.tres`/`.tscn` — so format breakage is invisible until a scene/resource loads at runtime or in an integration test. You also cannot "see" the editor; the MCP tools are your only viewport.

## MCP tools — your viewport

- `run_project`: only when you need no user input and can run autonomously.
- `get_debug_output`: **MANDATORY** while the project runs — this is how you read `JmoLogger` output and exceptions.
- Post-run logs: `%APPDATA%\Godot\app_userdata\{{PROJECT_NAME}}\logs\godot.log`. `/analyze_godot_logs` for structured analysis.
- `create_scene` / `add_node` / `save_scene`: scaffold `.tscn` files with valid headers.
- `get_uid`: fetch UID strings before manual `.tscn`/`.tres` edits — never guess.
- `update_project_uids`: run on suspected "Dependencies Missing" errors. **Unreliable on Windows** — builds a `res://C:\...` path and finds 0 resources; fall back to reading `.cs.uid` companion files directly.

### The "invisibility" workflow
1. **Code/Edit:** write C# logic / `.tres` data.
2. **Instrument:** `JmoLogger.Info()` at critical state changes.
3. **Run:** automated → `run_project`; manual → ask the user and wait (don't run-and-delegate; they're often mid-task).
4. **Verify:** automated → `get_debug_output` live; manual → read the post-run log path above.

## Format invariants (`.tres` / `.tscn`)

- **Treat as text.** Read the whole `[sub_resource]` / `[ext_resource]` web before editing; do precise text replacement.
- **No forward references.** `SubResource("X")` must resolve to an `[sub_resource id="X"]` declared *above* its first use — the loader is single-pass. A forward ref fails the entire resource at load while `dotnet build` still passes.
- **`load_steps=N`** in the header must equal the actual ext + sub resource count. Bump it when you add a resource by hand.
- **Value-type `= null` is always a bug.** The editor saves value-type `[Export]` props (`bool`/`int`/`float`/`Vector2`/`Vector3`/`Color`/`enum`) that match the C# default as `= null`; runtime then loads the *type zero*, not the C# default. Fix with an explicit value (`MaxSlots = 24`), never by deleting the line (the editor re-strips it). Reference-type `null` (Resource/Node/NodePath/Script) is legitimate.
- **Omitted ≠ missing.** Godot omits properties whose value matches the C# default. A `BBBoolCondition` with `Value=false` has no `Value` line — correct, not broken. Don't add defaults back.

## Editor-resave hazards

Opening the editor rewrites files. Specific silent mutations:

- **Strips refs to custom types lacking `[Tool]`.** Any `[GlobalClass]` Resource referenced from a `.tres` must carry `[Tool]` to survive an editor save-cycle, or the editor drops the reference with no error or warning. → **Commit manual `.tres` edits immediately, before opening the editor.**
- **Regenerates UIDs across the reference graph.** On reopen the editor can rewrite `ext_resource` UIDs to a different valid UID while the source `.tres` keeps the original — ref UID ≠ source UID. Path-based resolution still works, but UID-based lookups become fragile.
- **Strips `script_class=` / `[ext_resource type="Script"]`** when the class registry is stale (e.g. just after a refactor) — `load_steps` drops and runtime NREs in `_Ready`.
- **`project.godot` InputMap:** write `"events": [Object(...), Object(...)]` single-line. Multi-line event arrays can lose entries on editor re-save.
- **`project.godot` `DEFAULT` settings** pin to the engine version the project was *created* on, not the current one. Opt into current-engine defaults explicitly (e.g. write the `[physics]` block). See `scene_authoring.md` for `project.godot` editing philosophy.

## UID handling

- Never guess UIDs. Use `get_uid`, or read the `.cs.uid` companion file directly if the editor won't start.
- A `.tres` written via `Write` with no `uid=` header is loadable by path and self-heals (the editor writes `uid=` on next open) — **safe to commit without a UID.**
- If a `.tres` has no resource-level UID of its own, do **not** add `uid=` to its `[ext_resource]` entry. A UID there would be the *script's* UID, which Godot resolves to the script — throwing `InvalidCastException` on load. Omit `uid=` entirely.

## C# class ↔ file

- The `.cs` filename must match the class name **exactly, case-sensitive**, or scene load fails with `can_instantiate`. The downstream consumer then treats the missing component as null, so the feature silently no-ops with no runtime exception — grep `godot.log` for `can_instantiate` first when a refactored feature dies quietly.
- Renaming a `[GlobalClass]` script: `git mv` both the `.cs` *and* its `.cs.uid` (reuse the UID — don't regenerate), then update every `.tscn`/`.tres`/`.csproj` reference. Full procedure: `refactor_procedure` skill.

## Rebase / merge conflict resolution

`.tscn`/`.tres` conflicts are high-risk because the build stays green while wiring silently dies:

- **`--theirs` on a `.tscn` conflict drops scene-authored export wiring** added on the other side (`_property = ExtResource(...)` / `NodePath(...)`). Symptom: "input does nothing," feature disabled. Hand-merge at-risk wirings back; bump `load_steps`.
- **Orphaned `SubResource`:** taking one side's `SubResource("X")` reference without confirming the `[sub_resource id="X"]` block survived → runtime parse error `Condition "!int_resources.has(id)" is true` (misleading — the id is *missing*, not duplicate).

## Post-edit / post-rebase audit

After any manual `.tres`/`.tscn` edit, editor reopen, or `.tscn` conflict resolution:

- `git diff` the file; scan for `= null$` and classify each match by field type (value-type = bug).
- Cross-check declared vs referenced IDs: every `SubResource("X")` / `ExtResource("X")` has a matching `[sub_resource id="X"]` / `[ext_resource id="X"]`.
- Confirm `load_steps=N` matches the ext + sub resource count.
- After an editor reopen on a branch with hand-written UIDs: `grep -rn '<old_uid>' .` should return 0 matches.

## Resource instance uniqueness

When a Resource type has values that vary per instance, create **separate `.tres` files per configuration**. Shared Resources load once, so the last-saved value wins for every reference. Encode the distinguishing value in the filename — `fire_x2.tres`, not a generic `fire_ingredient_trait.tres` reused with different intended Strengths.

## Deep archive

Full incident detail (dates, specific PRs, edge cases) lives in the cold-tier auto-memory archive. Semantic-search, or read the files directly under `.claude/auto-memory/archive/`:
`archive_godot_build_gotchas.md`, `archive_mcp_uid_gotchas.md`, `archive_godot_scriptclass_filenamematch_gotcha.md`
