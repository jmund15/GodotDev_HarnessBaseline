---
name: Blackboard NullStorage Asymmetry
description: Jmodot Blackboard.Set<T>(key, null) for reference T stores Variant.Nil but TryGet<T>(class) returns false — null-stored class reads are indistinguishable from never-set through the public BB surface
type: gotcha
originSessionId: 890a5ddd-a5d3-4cd0-bb31-eda08fe0c542
---
# Blackboard null-storage Set/TryGet asymmetry

## The trap

`Jmodot.Implementation.AI.BB.Blackboard.Set<T>(key, null)` for a reference-type `T`:

```csharp
// Blackboard.cs:54-64
public Error Set<T>(StringName key, T val)
{
    if (val == null)
    {
        _pocoData.Remove(key);
        _variantData[key] = default; // Variant.Type.Nil
        NotifySubscribers(key, default);
        return Error.Ok;
    }
    ...
}
```

Stores `Variant.Type.Nil` and returns `Error.Ok` — looks like a successful write.

But the matching `TryGet<T>` for that key:

```csharp
// Blackboard.cs:138-162
if (_variantData.TryGetValue(key, out var variantVal))
{
    ...
    if (variantVal.Obj is T typedValue)   // Nil.Obj is null → null is T → false
    {
        value = typedValue;
        return true;
    }
    return false;  // ← falls through here for stored-null
}
```

`TryGet<Perception3DInfo>` on a stored-Nil key returns `false`, NOT `true` with `out var = null`. **Null-stored reads through `TryGet<T>` are observationally indistinguishable from never-set through the public BB surface.**

## How it bit me

Wave 3a's `TargetTrackerComponent.PublishTargetMemoryToBB(bb, memory: null)` test originally asserted only `stored == null` with a hedged comment. The session audit flagged the unasserted `found` bool as a weak contract test (F2/F4) and proposed `AssertThat(found).IsTrue()`. Both tests failed RED. Fix: revert to `AssertThat(stored).IsNull()` with a clarifying comment.

## Test-side contract

Highest-fidelity assertion available for "publish-null was called":

```csharp
TargetTrackerComponent.PublishTargetMemoryToBB(bb, memory: null);

bb.TryGet<Perception3DInfo>(BBCritterSig.CurrentTargetMemory, out var stored);
AssertThat(stored).IsNull();          // OK — captures the observable contract
// AssertThat(found).IsTrue();        // WRONG — TryGet returns false for stored-null class T
```

Do NOT assert on the `found` bool when testing null-storage of reference types — the BB doesn't surface enough to distinguish "stored Nil" from "never set" via this API.

## When it matters

- Any feature that uses BB to clear a previously-published reference (publish null = "no current value").
- Subscriber callbacks DO fire on null Set (`NotifySubscribers(key, default)`), so the side-effect path is observable, just not the round-trip.
- Value-type T (`int`, `float`, `Vector3`, etc.) goes through `TryConvertVariantToValueType<T>` and has different semantics — this gotcha is reference-type-specific.

## Cross-references

- `Jmodot/Implementation/AI/BB/Blackboard.cs:54` (Set null path)
- `Jmodot/Implementation/AI/BB/Blackboard.cs:138` (TryGet variant path)
- `Tests/Logic/NPCs/AI/TargetTrackerComponentTests.cs:45-58` (corrected null-publish test with the comment)
