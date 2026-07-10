---
name: feedback_considerations_consume_perception_not_queries
description: "Steering considerations / AI consumers read the perception manager (context.Memory), never issue parallel physics queries. Missing data = sensor config gap to fix, not a side-channel to add."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 66931000-c20f-4f54-b483-af3fa3e136e8
---

AI consumers (steering considerations, conditions) get world knowledge from the entity's `AIPerceptionManager3D` (`SteeringDecisionContext3D.Memory`), NOT from their own per-frame physics queries. If the data isn't in perception, that is a **sensor configuration gap** — fix the sensor (mask/category/decay), don't duplicate detection.

**Why (user, 2026-07-06):** "if the allies aren't in the perception manager, that seems like an issue with the perception system, not a reason for custom physics queries duplicating detection." The duplicate query was masking a real config bug (ThreatSensor mask excluded the NPC body layer), and parallel channels diverge from perception semantics (decay, confidence, identity).
**How to apply:** add/adjust a sensor (e.g. AllySensor: mask 128, InstantForgetDecay) + read `GetSensedByCategory`/`GetSensedByCollLayer`. Bonus tell: tests simplify to percept injection.
