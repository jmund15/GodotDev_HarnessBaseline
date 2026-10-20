---
disable-model-invocation: true
---

# Godot Code Quality Checklist

Godot and Jmodot items for [`code_quality.md`](code_quality.md), which reads this file when it exists. Each `## Godot <Name> (X)` section extends the section with letter `X` there; apply it wherever that section applies.

## Godot Compliance (C)

- [ ] **Node retrieval**: `GetNode()` used instead of `NodeExts` (`.GetFirstChildOfType<T>()`, `.TryGetNode<T>()`, `.GetChildrenOfInterface<T>()`)
- [ ] **Logging**: `GD.Print` or `GD.PrintErr` used instead of `JmoLogger` (Info/Warning/Error)
- [ ] **Godot naming**: PascalCase files/classes, snake_case directories, `MaterialTrait` naming convention
- [ ] **Nullability**: `[Export] = null!` without `[RequiredExport]`. Missing `this.ValidateRequiredExports()` in `_Ready()`
- [ ] **Lifecycle**: Game logic in C# constructor (should be `_Ready()`/`_EnterTree()`). Node queries in `_Process` (should be cached in `_Ready`)
- [ ] **Resources**: Shared `.tres` caching mutable per-instance state (Factory->Runner pattern violation)
- [ ] **Groups**: `AddToGroup`/`GetNodesInGroup` usage without clear justification — prefer C# interfaces or typed registries for type-safe lookups
- [ ] **Signals vs Events**: Godot signals used for gameplay logic (should be C# events) or C# events used for UI/engine interaction (should be Godot signals)
- [ ] **StringName**: String literals used in lookups, `SetDeferred` calls, or signal names where `StringName`/`PropertyName.*`/`SignalName.*` constants should be preferred

## Godot Design (D)

- [ ] **Godot dependencies**: registry/singleton access where an `[Export]`, a Blackboard value or a parameter can carry the dependency

## Godot Robustness (R)

- [ ] **Godot array null**: Godot `Collections.Array<T>` export properties accessed without null check — can be null if never assigned in inspector
- [ ] **Error propagation**: Configuration errors not throwing `NodeConfigurationException` / `ResourceConfigurationException`
- [ ] **Event teardown**: `_ExitTree` missing unsubscription for C# event handlers subscribed in `_Ready`/`_EnterTree`
- [ ] **IsInstanceValid**: Godot node references accessed after potential `Free()`/`QueueFree()` without `GodotObject.IsInstanceValid()` check

## Godot Performance (P)

- [ ] **Frame callbacks**: `new` objects, string concatenation, `GetNode` calls or LINQ in `_Process`/`_PhysicsProcess` that belong in `_Ready`

## Godot Intuitiveness (I)

- [ ] **Unclear exports**: `[Export]` fields with unclear units or valid ranges — missing `[ExportRange]` or name that communicates what the value controls
- [ ] **Missing ExportGroup**: Related `[Export]` properties not grouped with `[ExportGroup]` for inspector organization
- [ ] **Range hint mismatch**: `[ExportRange]` bounds that don't match actual valid values or don't include the default value
