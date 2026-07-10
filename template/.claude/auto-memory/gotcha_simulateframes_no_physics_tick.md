---
name: gotcha-simulateframes-no-physics-tick
description: "GdUnit4 ISceneRunner.SimulateFrames(n) advances idle/process frames only — it does NOT tick the physics server, so Area3D/PhysicsBody overlap detection (GetOverlappingBodies, BodyEntered/BodyExited) never registers. Use AwaitMillis(n) for physics-populated overlap tests."
metadata: 
  node_type: memory
  type: project
  originSessionId: a3f72909-5b41-4966-8e09-29449702919a
---

**Verified:** 2026-07-08, P2 disclosure component. An `ISceneRunner` test placing a `CharacterBody3D` (layer 1) inside an `Area3D` (mask 1) asserted the disclosure state reached `Active` via the deferred initial-overlap poll. With `await runner.SimulateFrames(2)` the body was NOT detected (state stayed non-Active); switching to `await runner.AwaitMillis(200)` made it pass — same nodes, same masks.

`SimulateFrames(n)` steps the SceneTree's idle/process loop but does not advance the fixed-timestep physics server, so collision pairs never form and `GetOverlappingBodies()` stays empty AND `BodyEntered` never fires. Real-time `AwaitMillis(n)` lets the physics server actually tick (~60Hz → 200ms ≈ 12 frames).

**How to apply:** For any GdUnit4 test that depends on physics-server state — Area3D/Area2D overlap, `BodyEntered`/`AreaEntered` signals, `MoveAndSlide` collision, raycasts against live bodies — settle with `AwaitMillis(n)`, not `SimulateFrames`/`AwaitIdleFrame`. `SimulateFrames` is fine for pure process-frame logic (tween/AnimationPlayer/`_Process` state). Sibling: this is why the encounter room-entry tests (`OnRoomApproachedTriggerTests`) use `AwaitMillis` for their timer/overlap paths. See also [[gotcha_spawn_marker_inside_trigger_volume]] (a body already inside still raises BodyEntered on the first physics step once monitoring starts — the reason the initial-overlap double-fire is idempotent).
