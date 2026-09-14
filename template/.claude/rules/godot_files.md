---
paths:
  - "**/*.tscn"
  - "**/*.tres"
  - "**/*.godot"
---

# Godot Files (`.tscn` / `.tres` / `.godot`) — Format, Editor-Resave Hazards, MCP Workflow

**Context:** These files are plain text you edit directly, but two things make them treacherous: the Godot editor silently rewrites them on every resave, and `dotnet build` does NOT parse `.tres`/`.tscn` — so format breakage is invisible until a scene/resource loads at runtime or in an integration test. You also cannot "see" the editor; the MCP tools are your only viewport.

## MCP tools — your viewport

- **Verify the engine pin before the first launch.** `mcp__godot__get_godot_version` — the MCP runs its OWN binary (`mcpServers.godot.env.GODOT_PATH` in the user's global `~/.claude.json`), NOT `$GODOT_BIN`. An older engine DOWNGRADES `project.godot` `config/features` and the csproj SDK pin silently on open. Mismatch → skip the MCP and run `bash .claude/scripts/godot_bin.sh --editor --path <dir> > <log> 2>&1` under `timeout` (wrapper form — the raw `"$GODOT_BIN"` form prompts in auto mode; doctrine: CLAUDE.md §Shell Discipline).
- `run_project`: only when you need no user input and can run autonomously.
- `get_debug_output`: **MANDATORY** while the project runs — this is how you read `JmoLogger` output and exceptions.
- Post-run logs (live/`run_project` sessions only): `%APPDATA%\Godot\app_userdata\{{PROJECT_NAME}}\logs\godot.log`. Test runs write `TestResults/godot_test.log` instead. `/analyze_godot_logs` for structured analysis.
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
- **`load_steps=N`** in the header = ext_resource count + sub_resource count **+ 1** (the container scene/resource itself). Bump it when you add a resource by hand **to a file that carries `load_steps`**; **NEW files: omit it entirely** (the editor's serializer does). Presence is NOT a dirty trigger — uid mismatches and a missing `script_class` are (see *UID handling*); a count mismatch still matters because the editor recalculates a wrong N on save.
- **`Transform3D(...)` literals are basis ROWS.** The 12 floats are Row0, Row1, Row2, origin — NOT the x/y/z axis vectors the C# `Basis(x,y,z)` constructor takes. Hand-encoding axes as the first 9 floats silently transposes the rotation (a marker's forward points inward instead of outward) with no load error; only behavior-level tests catch it.
- **Value-type `= null` is always a bug.** The editor saves value-type `[Export]` props (`bool`/`int`/`float`/`Vector2`/`Vector3`/`Color`/`enum`) that match the C# default as `= null`; runtime then loads the *type zero*, not the C# default. Fix with an explicit value (`MaxSlots = 24`), never by deleting the line (the editor re-strips it). Reference-type `null` (Resource/Node/NodePath/Script) is legitimate.
- **Omitted ≠ missing.** Godot omits properties whose value matches the C# default. A `BBBoolCondition` with `Value=false` has no `Value` line — correct, not broken. Don't add defaults back.

## Editor-resave hazards

Opening the editor rewrites files. Specific silent mutations:

- **Strips refs to custom types lacking `[Tool]`.** Any `[GlobalClass]` Resource referenced from a `.tres` must carry `[Tool]` to survive an editor save-cycle, or the editor drops the reference with no error or warning. → **Commit manual `.tres` edits immediately, before opening the editor.**
- **Regenerates UIDs across the reference graph.** On reopen the editor can rewrite `ext_resource` UIDs to a different valid UID while the source `.tres` keeps the original — ref UID ≠ source UID. Path-based resolution still works, but UID-based lookups become fragile.
- **Strips `script_class=` / `[ext_resource type="Script"]`** when the class registry is stale (e.g. just after a refactor) — `load_steps` drops and runtime NREs in `_Ready`.
- **A file needs no uid problem to be stripped: a bind-failed in-memory instance marks it dirty.** The editor booted while the DLL was newer → instance restoration fails at load → loaded≠disk → dirty → the next save flushes the stripped form. `editor/run/save_before_running=false` (in `project.godot`) removes the bulk flush (F5 no longer saves all dirty files — edits save via Ctrl+S). Restart the editor after any DLL change; the strip guard's `--editor-check` warns when an editor booted before the current assembly.
- **`project.godot` InputMap:** write `"events": [Object(...), Object(...)]` single-line. Multi-line event arrays can lose entries on editor re-save.
- **`project.godot` `DEFAULT` settings** pin to the engine version the project was *created* on, not the current one. Opt into current-engine defaults explicitly (e.g. write the `[physics]` block). See `scene_authoring.md` for `project.godot` editing philosophy.

## UID handling

- Never guess UIDs. Use `get_uid`, read the `<Target>.cs.uid` companion file directly if the editor won't start, or read the target's header `uid=`. A file's own uid lives on its HEADER line only — an `ext_resource` line further down carries the *target's* uid, and a scene's first ext_resource is its root SCRIPT's uid, not the scene's (backfilling that throws `InvalidCastException`-class errors).
- **The editor's persistent uid cache (`.godot/uid_cache.bin`) is the save-time authority for ref uids.** A ref carrying ANY uid that is not the target's registered cache uid — hand-minted included — is rewritten at the next save with the cache value, and a rewrite landing in a bind window re-strips the file. **Never mint a uid for a target the editor has seen — its cache registration wins and the file stays dirty until aligned.** The cache is ALIVE: every editor save registers formerly-unregistered targets, which re-dirties path-only refs — so re-run `.claude/hooks/uid_cache_audit.py --apply` after any editor session that saved files. The tool parses `uid_cache.bin` and aligns every committed `.tres`/`.tscn` — carried-uid mismatches replaced, carried uids with no cache entry stripped to path-only, path-only refs to registered targets given the cache uid; the saver writes nothing for unregistered paths, so a path-only ref to an unregistered target is the fixed point and must be left alone.
- **Author `.tres` in the editor's canonical serialization — the exact form the editor's own serializer writes — so the file is a serialization fixed point the editor never marks dirty:**
  - header: `[gd_resource type="Resource" script_class="X" format=3 uid="uid://..."]` — `script_class` immediately after `type`, `uid` last, **no `load_steps`** (the loader computes it; a file that carries `load_steps` differs from the serializer and is one resave away from a rewrite diff)
  - **a scripted Resource attaches its script in the `[resource]` block** — `script = ExtResource(...)` plus `metadata/_custom_type_script = "uid://..."` (the script's uid, from its `.cs.uid`). `script_class="X"` in the header alone is only a hint: without the block lines the resource loads as a bare `Resource` and typed casts throw `InvalidCastException`
  - `AudioStreamRandomizer` pools serialize as `streams_count` FIRST, then the count-gated `stream_{index}/stream` + `stream_{index}/weight` entries — property order matters to the loader
  - every `[ext_resource]` carries `uid="uid://..."` — the TARGET's uid, from its `.cs.uid` companion or its header — whenever the target has one
  - a path-only ref is a fixed point when the TARGET has no uid (no backfill pending) — leave it, never invent a uid
  - submodule (e.g. Jmodot) targets: keep path-only — their uids live in a foreign uid cache and backfilling them produces invalid-UID warnings on every load; the editor registers them itself.
- **Why it matters (recurring config-resource corruption):** a file that differs from the editor's serialization (missing `script_class`, uid-less ext_resources, stale `load_steps`) is marked dirty at EVERY editor load; the next save — including F5 play's save-before-run — rewrites it, and a rewrite landing in the editor's C# rebuild window serializes scripted sub-resources stripped (`resource_local_to_scene = ""`, bare-Resource loads, `InvalidCastException` on typed casts). **"The editor self-heals by writing `uid=` on next open" is the HAZARD (a pending rewrite), not a feature** — commit the fixed-point form instead.
- **Never repair a stripped `.tres` with `git restore`/`git checkout --`** — it re-plants the old-format dirty flag, the exact recurrence mechanism. Repair in place: `python3 .claude/hooks/tres_script_strip_guard.py --worktree --repair-inplace` (restores bindings, keeps the editor's normalization), then re-align uids with `.claude/hooks/uid_cache_audit.py --apply`. **After ANY `.tres` repair/alignment commit, the user must RESTART the editor** — a running instance's in-memory uid cache and stripped resource state flush on its next save and undo the disk fix.

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

## Editor-time inspection

**Never type-test an instantiated node at editor time.** `can_instantiate()` is `is_scripting_enabled() || is_tool()`, and scripting is OFF in the editor — so `PackedScene.Instantiate()` called from a plugin returns nodes with NO C# type attached; every `child is FooComponent` silently reads false and a validator reports a correctly-authored feature as missing. *Litmus:* is the type I'm testing `[Tool]`? If not, this test always fails. Legal alternatives:

- Mark the inspected type `[GlobalClass, Tool]` when it has no lifecycle overrides (free; pin it with a test asserting every analyzer-inspected type is `[Tool]`).
- Scan `PackedScene.GetState()` script paths when it does.
- A validator that must CALL the node (not merely test it) can't use the scan route — give it a preview-mode flag and suppress rather than false-warn.

## Resource instance uniqueness

When a Resource type has values that vary per instance, create **separate `.tres` files per configuration**. Shared Resources load once, so the last-saved value wins for every reference. Encode the distinguishing value in the filename — `fire_x2.tres`, not a generic `fire_material_trait.tres` reused with different intended Strengths.

## Deep archive

Full incident detail (dates, specific PRs, edge cases) lives in the cold-tier auto-memory archive. Semantic-search, or read the files directly under `.claude/auto-memory/archive/`:
`archive_godot_build_gotchas.md`, `archive_mcp_uid_gotchas.md`, `archive_godot_scriptclass_filenamematch_gotcha.md`
