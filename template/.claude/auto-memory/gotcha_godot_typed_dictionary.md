---
name: gotcha-godot-typed-dictionary
description: "Godot.Collections.Dictionary<Resource,V> [Export] — .tres literal format + null-key-from-empty-Inspector-slot."
metadata: 
  node_type: memory
  type: reference
  originSessionId: 73d529b6-fa2f-4eaf-a38b-fbd2a5575729
---

A `Godot.Collections.Dictionary<TResource, TValue>` `[Export]` has two non-obvious traits:

1. **`.tres` literal format** (hand-authoring): `Field = Dictionary[KeyClass, ValueType]({ ExtResource("id"): value })` — the typed header `Dictionary[KeyClass, int]` then a brace body mapping each `ExtResource("id"): literal`. Validate any hand-authored typed-dict `.tres` with an integration scene-load test — `dotnet build` does NOT parse `.tres`, so a malformed literal silently yields an empty dict (or a load throw) only at runtime.

2. **Empty Inspector slot → `null` key.** An unfilled dictionary row surfaces as a `null` key when iterating the dict. Consumers MUST guard (`if (key == null) continue;`) or risk a `NullReferenceException` downstream.

3. **Hand-authoring can hard-crash, not just silent-empty.** A typed-dict export literal hand-added to a *scene node* (`.tscn`) can crash the Godot runtime at scene load — observed as a native `STATUS` exit code with no `Passed!`/`Failed!` line, so it masquerades as a test-runner *hang*, not a clean load error. Prefer authoring scene-node typed-dict exports via the Godot editor or Godot-MCP scene tools (they emit correct serialization); reserve hand-authoring for forms a scene-load test validates.

**How to apply:** filter null keys at the consumption boundary; author scene-node typed-dict exports via editor/MCP rather than by hand; cover any hand-authored typed-dict `.tres`/`.tscn` with a scene-load test (or an existing test that loads the scene) asserting the consumed result is non-empty. Sibling value-type `.tres` trap: [[gotcha_export_enum_out_of_range_silent_false]].

**Concrete:** `default_practice_palette.tres` (`ConfiguredSet = Dictionary[IngredientData, int]({ ExtResource("..."): 3 })`) + `DefaultPracticePaletteProvider.GetSpawnSet`'s `ingredient == null` guard, 2026-05-28. Crash mode: `arena_floor.tscn` `_infusionsByFloor = Dictionary[int, Resource]({...})` hand-add crashed the GdUnit host (exit `-1073741795`); reverted + deferred to editor wiring, 2026-05-28 (crash followed the whole scene edit; typed-dict is prime suspect per trait 1, not bisected).
