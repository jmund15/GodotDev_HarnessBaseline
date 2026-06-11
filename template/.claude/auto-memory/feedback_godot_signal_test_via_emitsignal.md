---
name: feedback-godot-signal-test-via-emitsignal
description: "Logic-domain tests that need to fire Godot Node signals (TreeExiting, TreeEntered, etc.) must call EmitSignal directly — tree-mutation operations don't fire these signals outside a real SceneTree"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: be7b1f7e-2ff8-4ef7-8d20-c1c5fbe62ecd
---

Logic-domain tests subscribing to Godot Node signals (`TreeExiting`, `TreeEntered`, `Renamed`, etc.) cannot drive those signals via scene-tree mutation (`AddChild`/`RemoveChild`/`Free`) because Logic tests have no SceneTree. These signals fire when a node *enters or exits a SceneTree* — without one, mutation operations are silent on the signal layer. Drive them with `node.EmitSignal(Node.SignalName.X)` directly.

**Why:** `KillAllRuleTests.ThreeEntities_CompletesOnlyAfterAllExit` initially set up entities under a `holder` Node3D and called `holder.RemoveChild(e); e.Free()` to simulate death. Production `KillAllRule.Bind` does `entity.TreeExiting += OnEntityExiting`. Test fired no signals because nothing was ever in a SceneTree — assertion `completed == 1` failed with `0`. The test design assumed Godot's tree mechanics worked in pure CLR; they don't.

**How to apply:**
- Logic tests that subscribe to Godot Node signals: simulate via `node.EmitSignal(Node.SignalName.SignalName)` not via parent-child mutation.
- The C# event subscription (`node.TreeExiting += handler`) IS triggered by `EmitSignal` — the underlying signal routes through the same delegate chain, so the production subscription code is exercised correctly.
- Reserve scene-tree mutation for `[RequireGodotRuntime]` tests that have an actual `ISceneRunner` providing the tree. Integration tests can drive signals naturally; Logic tests cannot.
- Carve-out: signals that fire from script-side state changes (not tree transitions) work fine in Logic tests without `EmitSignal` — but tree-lifecycle signals specifically require tree mediation OR direct emission.

**Concrete (2026-05-17, KillAllRuleTests.ThreeEntities_CompletesOnlyAfterAllExit):** Fixed by replacing `holder.RemoveChild(e1); e1.Free();` with `e1.EmitSignal(Node.SignalName.TreeExiting);` — test went from RED to GREEN, and `KillAllRule.OnEntityExiting` was still validly exercised because `EmitSignal` triggers C# event subscribers.

Related: [[feedback_lsp_default_for_csharp]] (Logic-domain test conventions). [[jmodot_2d_movement_architecture]] (Godot interop boundary discipline).
