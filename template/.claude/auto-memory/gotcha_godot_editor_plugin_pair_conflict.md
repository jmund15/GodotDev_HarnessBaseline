---
name: gotcha-godot-editor-plugin-pair-conflict
description: "Pairs of Godot editor plugins that each individually work fine can deterministically crash the engine during the editor-rebuild phase when both are enabled together. Signature: `STATUS_ACCESS_VIOLATION` (`0xC0000005`) at a fixed native address that is IDENTICAL across .NET runtime versions (rules out CLR-version drift). Symptom-via-GdUnit4: `[{{PROJECT_NAME}}] Rebuilding Godot Project ends with exit code: -1073741819` + silent-skip of all `[RequireGodotRuntime]` tests + the gate count drops to roughly non-runtime Logic only. Direct `godot --headless --quit` and gameplay are unaffected — the crash only fires when something triggers the editor-rebuild scan (GdUnit4's executor-launch, `godot --build-solutions`, Godot editor itself)."
metadata: 
  node_type: memory
  type: project
  originSessionId: ef7557fc-8db8-47e5-a37b-143f92fd4874
---

Editor plugins in Godot can register `EditorImportPlugin`, `EditorInspectorPlugin`, `EditorTranslationParserPlugin`, etc., and modify the editor's import-format dispatcher / file-system scanner. Two plugins that both touch the same registration slot, or one of which overwrites state the other set up, can crash the engine deterministically during the next editor-rebuild scan. The build succeeds, gameplay launches cleanly — but anything that exercises the editor codepath (test runners, headless `--build-solutions`, opening the project in the editor in some cases) hits the conflict.

**Concrete instance (2026-05-28):** the pair `TileMapLayer3D` + `nklbdev.importality` enabled together crashed the GdUnit4 test runner consistently for an entire diagnostic session. Each enabled alone passed all 8 `KnockedUpStateTests` cases. With the addition of `spell_stat_dashboard` (the third editor plugin), no change — only the two-plugin pair matters. Crash address `0x00007FFAC3D3A853` was deterministic across runs AND across runtime versions (.NET 8.0.27 and .NET 10.0.8 both crash at the identical address — proves the failure is in native engine code reached by editor-plugin registration, not in the managed CLR layer).

**Why:**
- Editor plugins are loaded by Godot at editor startup; the order and the state each leaves behind matter.
- Native code crashes deterministically at fixed addresses; the constancy across .NET versions narrows the suspect to native plugin code or engine code reached by plugin registration.
- The Windows event log records `Application: Godot_v*.exe` crashes with `Exception Info: exception code c0000005, exception address <FIXED>` — but Stack is empty because the crash is below the managed boundary.
- GdUnit4's test runner does NOT load any specific test before the crash — the executor-launch IS the crash, and it happens because Godot's editor-rebuild does the plugin scan. This is why bisecting *tests* yields nothing; bisect *plugins* instead.

**How to apply:**
- **Investigation flow when you see deterministic native crashes from GdUnit4's `Rebuilding Godot Project` step:**
  1. Don't bisect tests — they're not the trigger (executor-launch crashes before any test code runs). Confirm by running ONE runtime-required test alone; if crash, executor-launch is the issue.
  2. Verify direct `godot --headless --build-solutions --path . --quit` — usually exits 0 (because gdunit4's invocation has different flags or different process state). If it crashes too, the editor-rebuild scan itself is the problem, not gdunit4-specific.
  3. Check Windows Event Log: `Get-WinEvent -FilterHashtable @{LogName='Application'; Level=2; StartTime=(Get-Date).AddMinutes(-5)}` filtered to `Godot`. Look for the crash address + .NET version. If the address is consistent across runs, it's deterministic native code.
  4. Toggle `[editor_plugins].enabled` in `project.godot`: empty array → run test → if works, bisect via pairwise enablement.
  5. The bisect IS pairwise — single-plugin enablement may all pass while a pair crashes. Test combinations of N choose 2.
- **Workaround when conflict found:** disable whichever plugin is least currently-needed in `[editor_plugins].enabled`. Both plugins remain on disk; just remove from the enabled `PackedStringArray`. File a worklog item for re-enabling once upstream conflict is fixed.
- **Fix path (long-term):** read both plugins' `plugin.cfg` + their main `.gd` init scripts. Look for `add_import_plugin()` / `add_inspector_plugin()` / `add_export_plugin()` calls. The two plugins likely register against the same slot or hook the same engine event. Report to whichever maintainer is more responsive; suggest collaboration on a shared dispatch interface.
- **Audit check after enabling a new editor plugin:** run `dotnet test --settings .runsettings --verbosity quiet --filter "FullyQualifiedName~{{PROJECT_NAME}}.Tests.Logic.HSM.KnockedUpStateTests"` (or another known runtime-required class). If silent-skip appears (Total < expected), the new plugin conflicts with an existing one — bisect immediately rather than committing.

**Diagnostic-debt fix surfaced by this incident:** the `silent_skip_sentinels.Logic_min` in `Tests/regression_baseline.json` is currently `500`, but the non-runtime Logic test count has grown to ~658. With sentinel < actual, `/regression_gate` Tier-1 does not catch this silent-skip pattern. The sentinel needs to track non-runtime growth (suggest bumping to ~700, or computing dynamically from a per-suite filter that excludes `[RequireGodotRuntime]`).

Related rules: [[gotcha_editor_reserialize_value_export_null_strip]] is a sibling Godot data-evolution gotcha (different mechanism — data resave; same shape — quietly breaks runtime semantics while build stays green).
