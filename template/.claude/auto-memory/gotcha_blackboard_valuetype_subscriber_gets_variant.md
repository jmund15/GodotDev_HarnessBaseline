---
name: gotcha_blackboard_valuetype_subscriber_gets_variant
description: "Blackboard.Set stores value types as Variant and hands SUBSCRIBERS the Variant — `payload is bool/int` in a subscription callback silently never matches"
metadata: 
  node_type: memory
  type: project
  originSessionId: 66931000-c20f-4f54-b483-af3fa3e136e8
---

`Blackboard.Set<T>` stores value types (bool/int/float/vectors) as `Godot.Variant` and `NotifySubscribers` delivers **the Variant**, not a boxed CLR value. A graph/BB subscription callback pattern-matching `payload is bool b` never matches — the subscriber silently sees nothing. Reference/POCO payloads (StringName included) arrive as the raw object, which is why existing StringName-channel consumers (`InteractionCompletionRule`) work.

**How to apply:** subscription callbacks on value-typed channels must accept both shapes:
`payload switch { bool b => b, Variant v when v.VariantType == Variant.Type.Bool => v.AsBool(), _ => default }`.
`TryGet<T>` is unaffected (it unboxes) — the asymmetry is subscriber-only.

**Verified:** RoomEntryActivationTests RED→GREEN 2026-07-07 — `OnRoomActivatedTrigger`'s (then `OnRoomEnterTrigger`) `payload is bool` never fired on the `RoomEntered` latch; Variant-aware match fixed it. Sibling of [[gotcha_blackboard_stringname_variant_boxing]] / [[Blackboard_NullStorage_Asymmetry]].
