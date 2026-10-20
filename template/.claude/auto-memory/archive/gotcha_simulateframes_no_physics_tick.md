---
name: gotcha-simulateframes-no-physics-tick
description: "GdUnit4 ISceneRunner.SimulateFrames(n) advances idle/process frames only — it does NOT tick the physics server, so Area3D/PhysicsBody overlap detection (GetOverlappingBodies, BodyEntered/BodyExited) never registers. Await real physics frames for physics-populated overlap tests."
metadata: 
  node_type: memory
  type: project
---

**Verified by a controlled overlap probe:** an `ISceneRunner` test placed a `CharacterBody3D` inside an `Area3D` with matching layer and mask. With `await runner.SimulateFrames(2)`, the body was not detected. Switching only the wait to `await runner.AwaitMillis(200)` populated the overlap, because 200 ms of wall-clock time spans real physics ticks.

`SimulateFrames(n)` steps the SceneTree's idle/process loop but does not advance the fixed-timestep physics server, so collision pairs never form and `GetOverlappingBodies()` stays empty AND `BodyEntered` never fires. The physics server ticks only across real physics frames, which `await tree.ToSignal(tree, SceneTree.SignalName.PhysicsFrame)` awaits (~60Hz → 12 frames ≈ 200ms of real physics ticks).

**Corollary — MoveAndSlide position integration:** synchronously pumping `MovementProcessor3D.ProcessMovement` (→ `MoveAndSlide`) in a loop preserves `Velocity` perfectly but does NOT integrate `GlobalPosition` — the transform lands only on a real physics-server step. Velocity assertions are safe synchronously. Displacement assertions require interleaved physics frames.

**How to apply:** `.claude/rules/test_authoring.md` rule 9 owns the wait choice; this file is its evidence. Physics-server state settles by awaiting real physics frames. `_Process` state settles by awaiting process frames; `SimulateFrames` stays correct only where the test's `ISceneRunner` owns the frame pump.
