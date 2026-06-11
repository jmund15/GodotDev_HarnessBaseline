---
name: gotcha-godot-variant-int64-fold
description: "Godot stores integer variants as Int64; generic-object fold receives boxed `long`, not `int`. Cast to `long` first."
metadata: 
  node_type: memory
  type: reference
  originSessionId: 3458cc73-6e07-455f-8d44-8d1251b9edee
---

When a fold or generic accessor reads a value from `Blackboard.TryGet<object>` (or any path that boxes a Variant-stored integer via `variant.Obj`), the boxed runtime type is `System.Int64`, not `System.Int32`. A direct `(int)v` cast on the boxed `long` throws `InvalidCastException: Cannot cast System.Int64 to System.Int32`. The symptom doesn't point at the storage-width mismatch — it surfaces as a runtime cast failure deep inside a fold lambda.

**How to apply:** For fold accumulators or generic-object readers consuming BB integer payloads, cast to `long` first; narrow only if downstream consumers require `int`. Document the convention in the API XML for any helper that surfaces `object` to a caller-supplied delegate (e.g. `BlackboardGraph.AggregateUp<TAcc>(StringName, TAcc, Func<TAcc, object, TAcc>)`). Strongly-typed `TryGet<int>` works fine — Blackboard's `TryConvertVariantToValueType<int>` uses `variant.AsInt32()` to down-convert; only the generic-object path is affected.

**Concrete:** Hit 2026-05-18 in `BlackboardGraphAggregateTest` — three aggregate tests failed with `InvalidCastException` when written as `AggregateUp<int>(Health, 0, (acc, v) => acc + (int)v)`. Fix was test-side: switch to `long` accumulators and `(long)v` cast. Underlying Variant boxing behavior is Godot 4.x C# binding.
