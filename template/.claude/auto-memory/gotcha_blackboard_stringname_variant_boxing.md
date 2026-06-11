---
name: gotcha-blackboard-stringname-variant-boxing
description: "StringName payloads route through Blackboard's Variant path; subscribers see equal-by-value but distinct-by-reference wrappers. Use ToString comparison, not IsSame."
metadata: 
  node_type: memory
  type: gotcha
  originSessionId: 84c4144b-f9b9-4779-9edf-d4aa3710e5f9
---

`Blackboard.Set<StringName>(key, sn)` does NOT route through the POCO branch
(despite StringName being a sealed class, not GodotObject-derived) — Godot 4.6
bindings implicitly convert StringName to GodotObject for `is GodotObject` checks,
so Set routes through `Variant.From(godotObj)`. `Variant.From` boxes a fresh
StringName wrapper; subscribers (both per-key `Subscribe` and graph-level
`AnyKeyChanged`) receive an equal-by-value but distinct-by-reference instance.

**Why:** Empirically confirmed 2026-05-19 — `Set_StringNamePayload_AnyKeyChanged_ReceivesActualStringName`
test asserted `IsSame(payload)` and failed despite the value-preservation fix in
the POCO branch. The path was Variant, not POCO.

**How to apply:** When asserting on a subscriber payload that's a StringName, use
`observed.ToString() == expected.ToString()` or `observed.Equals(payload)`, not
`Object.ReferenceEquals` / `IsSame`. The contract is value-preservation, not
reference-identity. Related: [[Blackboard_NullStorage_Asymmetry]],
[[gotcha_godot_variant_int64_fold]].
