---
name: feedback_prefer_data_params_over_injected_delegates
description: "User prefers data params (values, typed pairs/records) over injected-behavior delegates (Func<>) in API design; reserve Func for pure value-projections."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 6cf67230-882a-4bd3-8f90-c516d3feaba6
---

User is wary of "abstract floating function" parameters — a `Func<>` that injects *behavior* (e.g. an RNG-draw delegate `Func<long,long>`). Default to passing the relevant thing as **data**: a value (`long roll`), a typed pair list (`IReadOnlyList<(T Item, long Weight)>`), a record.

**Why:** data params are more legible at the call site (you see the value, not a lambda), more decoupled (no captured-closure surprises), and trivially testable with literals. The user states this as a standing instinct ("usually wary… unless justified"), not a one-off.

**How to apply:**
- Reserve `Func<>` for a pure **value projection** (LINQ-style `Func<T,key>` selector) — that's idiomatic and accepted; the objection is to injected *behavior*, not all delegates.
- Litmus that settled the canonical case (WeightedPick, P3a.1, 2026-06-05): a cumulative weighted pick must sum ALL weights to know the draw range → a weight-`Func` double-evaluates AND forces a hoisted `Func` local at the call site. Pre-computed `(item, weight)` pairs evaluate once and were already the idiom in the one computed-weight consumer (`WeightedIngredientSelector`). Conclusion: weights-as-data + roll-as-data, zero delegates.
- When unsure delegate-vs-data, check the real consumers empirically (stored-value → projection sugar is fine; computed-value → prefer materialized data). Don't defend the delegate from first principles.

Sibling of [[feedback_prefer_typed_shapes_over_empty_markers]] (both: lead with concrete typed shapes). Related: [[feedback_no_magnitude_as_type_discriminator]].
