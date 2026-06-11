---
name: arch-rule-packedscene-resource-inline-copy
description: "PackedScene.Pack serializes Resource [Export]s as INLINE COPIES when the Resource has no ResourcePath — Instantiate produces a fresh instance, not a reference to the original"
metadata: 
  node_type: memory
  type: project
  originSessionId: be7b1f7e-2ff8-4ef7-8d20-c1c5fbe62ecd
---

`PackedScene.Pack(node)` serializes every `[Export] Resource` property on the captured node. When the Resource has no `ResourcePath` (i.e., constructed in-memory or unsaved), the serialization is an **inline copy** of the Resource's state — NOT a reference. On `PackedScene.Instantiate()`, each inline-copied Resource is re-materialized as a **fresh instance**.

**Consequence:** Code that constructs a Resource, wires it onto a Node, Packs the Node, then Instantiates from the packed scene gets back a Node whose `[Export] Resource` property points to a **different object** than the original. Reference equality fails; state mutations on the original after Pack don't propagate; runtime code that uses the "same" Resource on both sides of the boundary needs explicit re-wiring.

**Why:** `EncounterRuntime.Bind(definition, ...)` does `definition.RuntimeScene.Instantiate()`. The instantiated root's `Definition` property (when the .tscn had an Inspector-wired value) is an inline copy of whatever `EncounterDefinition` was packed — not the `definition` parameter the runtime was invoked with. `RaiseCompleted` reads `Definition.CompletionOutcome` — on the copy, which can drift from the canonical .tres's outcome. In tests packing a bare `StubEncounterRoot` with no Inspector-wired Definition, the copy is null → NRE in `RaiseCompleted`.

**How to apply:**
- When runtime code is the authoritative source of which Resource an instantiated Node belongs to, **propagate the canonical reference explicitly post-Instantiate**. Don't trust the Inspector-set path — that's a snapshot, not a binding.
- Pattern (`EncounterRuntime` precedent): runtime calls a public `AssignCanonicalDefinition(definition)` on the root BEFORE AddChild, so any subclass `_Ready` validation sees the canonical state.
- The general shape: any system where `(a)` a Resource references a PackedScene AND `(b)` the scene's root has an `[Export]` back-reference to that same Resource is at risk. The back-reference is a circular Inspector wiring that PackedScene serialization breaks.
- Discovery aid: if instances of `[Export] Resource` properties produce reference-equality surprises post-Instantiate, suspect inline-copy round-trip first.

**Concrete (2026-05-17):** `EncounterRuntime.Bind` instantiated packed `StubEncounterRoot` with no Inspector-set Definition. `KillAllRule.Bind` fired Completed on empty-roster → `RaiseCompletedFromExternal` → `RaiseCompleted` → `Definition.CompletionOutcome` → NRE. Fix: added `EncounterRootBase.AssignCanonicalDefinition(definition)` (public method) called by `EncounterRuntime.Bind` BEFORE `AddChild` so any future `_Ready` validation sees canonical state. Resolved 9 latent test failures sharing the same root cause.

Related: [[arch_rule_test_namespace_matches_gate_filter]] (the coverage gap that hid these for a session). [[Blackboard_NullStorage_Asymmetry]] (sibling Godot-interop gotcha at the BB layer).
