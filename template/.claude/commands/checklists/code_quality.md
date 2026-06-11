---
description: >-
  Auto-load when reviewing code, auditing session edits, or running a compliance pass.
  Triggers: "review this PR", "code review", "any smells", "audit this code", "review my
  changes", "check compliance". SKIP for test-file-only reviews (use `checklists:test_quality`)
  or pure markdown/doc changes.
---

# Shared Code Quality Checklist
<!-- Derived from Architecture Philosophy Skill. Sync when Skill updates. -->
<!-- Used by: /review_pr (code-reviewer: C+D+S, error-hunter: R+P, type-reviewer: I), /session_audit (all sections) -->
<!-- Agents: Load the Architecture Philosophy skill as your primary compliance reference. -->

Review every changed file against each item. Skip items not applicable to the file type.

## Compliance (C)

- [ ] **Node retrieval**: `GetNode()` used instead of `NodeExts` (`.GetFirstChildOfType<T>()`, `.TryGetNode<T>()`, `.GetChildrenOfInterface<T>()`)
- [ ] **Logging**: `GD.Print` or `GD.PrintErr` used instead of `JmoLogger` (Info/Warning/Error)
- [ ] **Control flow**: Nested if/else without early returns. Missing brackets `{}` on any if/else/for/while
- [ ] **Naming**: PascalCase files/classes, snake_case directories, `IngredientTrait` naming convention
- [ ] **Nullability**: `[Export] = null!` without `[RequiredExport]`. Missing `this.ValidateRequiredExports()` in `_Ready()`
- [ ] **Events**: `public event Action X;` without `= delegate { };` initializer
- [ ] **Interfaces**: Concrete types where interfaces should be used (`IDamageable`, `IGodotNodeInterface`)
- [ ] **Lifecycle**: Game logic in C# constructor (should be `_Ready()`/`_EnterTree()`). Node queries in `_Process` (should be cached in `_Ready`)
- [ ] **Resources**: Shared `.tres` caching mutable per-instance state (Factory->Runner pattern violation)
- [ ] **Async void**: `async void` methods that should be `async Task` (exception: top-level Godot signal handlers where signature is forced)
- [ ] **Groups**: `AddToGroup`/`GetNodesInGroup` usage without clear justification — prefer C# interfaces or typed registries for type-safe lookups
- [ ] **Signals vs Events**: Godot signals used for gameplay logic (should be C# events) or C# events used for UI/engine interaction (should be Godot signals)
- [ ] **Nullable default params**: Parameters with `= null` default that are not declared as nullable type (`Type? param = null`)
- [ ] **StringName**: String literals used in lookups, `SetDeferred` calls, or signal names where `StringName`/`PropertyName.*`/`SignalName.*` constants should be preferred

## Design (D)

- [ ] **Separation of concerns**: Calculation mixed with side effects in the same method
- [ ] **Testability**: Business logic coupled to Node lifecycle that could be extracted for testability — as pure static, injectable service, or standalone class
- [ ] **Tight coupling**: Components interacting via concrete types instead of interfaces
- [ ] **Dead code / magic numbers**: Unreachable code, unnamed numeric literals, unclear variable/function names
- [ ] **God methods**: Methods doing too many things (>40 lines) — should be decomposed
- [ ] **Pattern consistency**: New code inconsistent with dependency injection, lifecycle, or architectural patterns used in neighboring files
- [ ] **Hidden dependencies**: GlobalRegistry/singleton access in constructors or methods where injection ([Export], Blackboard, or parameter) is possible
- [ ] **Duplicate logic**: Tested helper class or static method exists but production code reimplements the same calculation inline — divergence risk

## Semantics (S)

- [ ] **Misleading names**: Fields named as counts that are actually budgets (or vice versa). Methods whose behavior doesn't match their name
- [ ] **Opaque parameters**: Parameters that require reading the implementation to understand their units or valid range
- [ ] **Missing XML docs**: Public API methods without XML doc comments explaining behavior, parameters, or return values
- [ ] **Inconsistent terminology**: Same concept referred to by different names across related files
- [ ] **Unit ambiguity**: Numeric parameters named "speed", "duration", "force" without documenting units (seconds, units/sec, degrees/sec) in name, XML doc, or `[ExportRange]`
- [ ] **Bool parameter traps**: Public methods with `bool` parameters that don't communicate purpose at call site — prefer enum, named constants, or method overloads
- [ ] **Primitive obsession**: `int` constants as pseudo-enums, raw `string` as identity keys where an enum, `StringName`, or typed wrapper would be more expressive
- [ ] **Empty doc stubs**: `/// <summary> /// </summary>` with no content — worse than no doc (remove or fill in)

## Robustness (R)

- [ ] **Division by zero**: Arithmetic without zero guards
- [ ] **Nullable references**: Accessed without null checks or `?` operator
- [ ] **TryGet null guard**: Missing `|| result == null` after `TryGet`/`TryGetFirstChildOfType` calls
- [ ] **Collection safety**: Operations on potentially empty collections without guards (e.g., `[0]`, `First()`, `Single()`)
- [ ] **Input validation**: Missing validation on public API boundaries
- [ ] **Data-driven ranges**: `Random.Next(min, max)` or arithmetic using editor-exported min/max without `Math.Min`/`Math.Max` guard — designers can set Min > Max
- [ ] **Godot array null**: Godot `Collections.Array<T>` export properties accessed without null check — can be null if never assigned in inspector
- [ ] **Silent failures**: Empty catch blocks, return null/default without logging, `?.` silently skipping critical operations
- [ ] **Error propagation**: Configuration errors not throwing `NodeConfigurationException` / `ResourceConfigurationException`
- [ ] **Unhandled states**: Switch without default case, enum values not covered in pattern matching, state machines with missing transition coverage
- [ ] **Deferred init races**: Race conditions in deferred initialization (signal handlers accessing state not yet set up)
- [ ] **Event disconnect**: `_ExitTree` missing unsubscription for C# event handlers subscribed in `_Ready`/`_EnterTree` — causes leaks or callbacks on freed objects
- [ ] **IsInstanceValid**: Godot node references accessed after potential `Free()`/`QueueFree()` without `GodotObject.IsInstanceValid()` check
- [ ] **Deferred LINQ**: Storing `IOrderedEnumerable`/`IEnumerable` without materializing (`.ToList()`) — re-evaluates the query on every iteration

## Performance (P)

- [ ] **Hot-path allocations**: `new` objects or string concatenation in `_Process` / `_PhysicsProcess`
- [ ] **Repeated lookups**: Dictionary/list creation or `GetNode` calls per-frame that should be cached in `_Ready`
- [ ] **Per-call allocations**: Unnecessary dictionary/list creation per call that should be pooled or cached
- [ ] **LINQ in hot paths**: `.Where()`, `.Select()`, `.OrderBy()` in `_Process`/`_PhysicsProcess` — allocate closures and enumerators every frame

## Intuitiveness (I)

- [ ] **Unclear exports**: `[Export]` fields with unclear units or valid ranges — missing `[ExportRange]` or name that communicates what the value controls
- [ ] **Field interactions**: Non-obvious interactions between export fields (changing A invalidates B) without `[ExportGroup]` or documentation
- [ ] **Bad defaults**: Default export values that produce poor or no visual results out of the box
- [ ] **Missing ExportGroup**: Related `[Export]` properties not grouped with `[ExportGroup]` for inspector organization
- [ ] **Range hint mismatch**: `[ExportRange]` bounds that don't match actual valid values or don't include the default value
- [ ] **Complex signatures**: Methods with 5+ parameters, especially multiple of the same type or nullable overrides — consider a parameter object or builder
- [ ] **Deep nesting**: 3+ indentation levels within a method body — decompose via early returns or extracted helpers
- [ ] **Vague method names**: Methods named `Evaluate`, `Resolve`, `Process`, `Handle` without qualifier communicating what they operate on
- [ ] **Inconsistent patterns**: Same concept implemented differently across similar components without justification
- [ ] **Large files**: Files exceeding 500 lines — class likely has too many responsibilities and should be decomposed
