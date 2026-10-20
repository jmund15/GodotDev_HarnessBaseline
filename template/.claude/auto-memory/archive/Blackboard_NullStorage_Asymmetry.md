---
name: Blackboard NullStorage Asymmetry
description: Jmodot Blackboard.Set<T>(key, null) stores Nil, but TryGet<T> returns false for reference types.
type: gotcha
---

# Blackboard null-storage asymmetry

`Blackboard.Set<T>(key, null)` removes POCO storage, writes `Variant.Nil`, notifies subscribers, and
returns `Error.Ok`. `TryGet<T>` then returns `false` because `Nil.Obj is T` is false for a reference
type.

A stored null and a never-set key are therefore indistinguishable through `TryGet<T>`.

For a null-clear contract:
- assert the returned value is null;
- do not assert that `TryGet<T>` returned true;
- observe the subscriber callback if the test must prove that the write occurred.

Value types use a different conversion path.

Library anchors: `Jmodot/Implementation/AI/BB/Blackboard.cs`, in the null branch of `Set<T>` and the
Variant branch of `TryGet<T>`.
