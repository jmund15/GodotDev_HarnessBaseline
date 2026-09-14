---
name: gotcha-simulateframes-no-physics-tick
description: "GdUnit4 ISceneRunner.SimulateFrames(n) advances idle/process frames only — it does NOT tick the physics server, so Area3D/PhysicsBody overlap detection (GetOverlappingBodies, BodyEntered/BodyExited) never registers. Use AwaitMillis(n) for physics-populated overlap tests."
metadata: 
  node_type: memory
  type: project
---

**Verified by a controlled overlap probe:** an `ISceneRunner` test placed a `CharacterBody3D` inside an `Area3D` with matching layer and mask. With `await runner.SimulateFrames(2)`, the body was not detected. Switching only the wait to `await runner.AwaitMillis(200)` populated the overlap.

`SimulateFrames(n)` steps the SceneTree's idle/process loop but does not advance the fixed-timestep physics server, so collision pairs never form and `GetOverlappingBodies()` stays empty AND `BodyEntered` never fires. Real-time `AwaitMillis(n)` lets the physics server tick.

**Corollary — `MoveAndSlide` position integration:** synchronously pumping `MovementProcessor3D.ProcessMovement` in a loop preserves `Velocity` but does not integrate `GlobalPosition`; the transform lands on a real physics-server step. Velocity assertions are safe synchronously. Displacement assertions require interleaved physics frames.

**How to apply:** For any GdUnit4 test that depends on physics-server state — Area3D/Area2D overlap, `BodyEntered`/`AreaEntered` signals, `MoveAndSlide` collision, or raycasts against live bodies — settle with `AwaitMillis(n)`, not `SimulateFrames`/`AwaitIdleFrame`. `SimulateFrames` is fine for pure process-frame logic such as tweens, AnimationPlayer, and `_Process` state.
