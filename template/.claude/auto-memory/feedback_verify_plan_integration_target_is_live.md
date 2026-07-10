---
name: feedback_verify_plan_integration_target_is_live
description: "A plan's named wiring/integration target may be orphaned by a concurrent/prior Part — verify it's the LIVE consumer before wiring."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 66931000-c20f-4f54-b483-af3fa3e136e8
---

When a plan names a specific **integration/wiring target** — a scene, room, autoload entry, floor slot, or `.tres`/`.tscn` the new work "plugs into" — verify it is still the *live* target before wiring into it. A plan authored against an in-flight branch names the target as it existed at plan time; a concurrent or prior Part can rename, relocate, or replace it in between.

**Why:** wiring into a stale/orphaned target fails silently in the worst way — the new content *loads clean* (build + import green) but nothing consumes it, so the feature is dead with no error. This is distinct from the relocated-vs-missing *dependency* check ([[feedback_verify_explore_agent_empirical_claims]]): that's about a type/file the plan *reads*; this is about the seam the plan *writes into*.

**How to apply:** trace the named target to its live consumer before editing — does anything still reference it (floor definition, autoload list, scene tree, selector array)? If orphaned, find the live equivalent; a genuine plan-fact change is halt valve (a), not a silent substitution. Cheap to check (one grep/reference-walk), expensive to miss (a shipped-but-inert feature). Related: [[feedback_concurrent_session_shared_tree]], [[gotcha_plan_pending_part_already_shipped]].

**Evidence:** Part 3b's plan named `Dungeon/PrototypeFloor/proto_room.tscn` as the prototype-wiring target. The concurrent Part 3a had refactored the prototype floor into `prototype_floor.tres` (`FloorDefinition` → `RoomSlot`s → `proto_{entrance,chamber,boss}.tscn`), orphaning `proto_room.tscn` — it was referenced by no live floor. Correct wiring was `proto_chamber.tscn` + a `StaticEncounter` on the Combat `RoomSlot`, discovered only by tracing the FloorDefinition, not by trusting the plan's file name.
