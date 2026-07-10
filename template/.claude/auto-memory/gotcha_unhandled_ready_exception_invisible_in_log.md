---
name: gotcha_unhandled_ready_exception_invisible_in_log
description: "Unhandled C# exceptions in _Ready/lifecycle hooks never reach godot.log — entity dies half-alive with a CLEAN log; grep the log proves nothing about init failures"
metadata: 
  node_type: memory
  type: project
  originSessionId: 66931000-c20f-4f54-b483-af3fa3e136e8
---

An unhandled C# exception in `_Ready` (or any engine-called lifecycle hook) aborts that node's init but is NOT written to `godot.log` — only `GD.PushError`/engine errors land there. The entity ends up half-alive (self-initializing children like perception/hitbox keep running; everything downstream of the aborted bootstrap is dead) with a log that looks clean.

**Why:** Mono unhandled-exception output goes to stderr, not the file logger. `ValidateRequiredExports` throws are the most common source.

**How to apply:** When an entity is inert but its log shows normal child-component activity, don't trust log absence — check for the entity's own "Ready" state-change line (e.g. `[NPC] Ready`). Missing = init aborted silently. Pin required-export wiring with load-time scene tests (see `TargetTrackerWiringTests`), because no runtime log or test output will surface the throw.

**Concrete:** TankBoss boss-room playtest 2026-07-07 — `tank_boss.tscn` carried the retired `_primaryTarget` property, `_strategy` [RequiredExport] was null, `_Ready` threw invisibly, boss stood inert while perception logged detections. Related: [[gotcha_inherited_scene_override_replaces_never_merges]], [[feedback_test_fixture_must_match_production_topology]].
