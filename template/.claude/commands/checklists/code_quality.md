---
description: Review code, audit session edits, or run a compliance pass; use `checklists:test_quality` for test-file-only reviews.
---

# Shared Code Quality Checklist

Review every changed file against each item.

When the godot layer is installed, the companion of this file (same name plus `_godot`) holds engine items. Read it too: each `## Godot <Name> (X)` section extends the section with letter `X` below. Without it, only the items below apply.

## Compliance (C)

- [ ] **Control flow**: Nested if/else without early returns. Missing brackets `{}` on any if/else/for/while
- [ ] **Events**: `public event Action X;` without `= delegate { };` initializer
- [ ] **Interfaces**: Concrete types where interfaces should be used
- [ ] **Async void**: `async void` methods that should be `async Task` (exception: top-level event handlers where the signature is forced)
- [ ] **Nullable default params**: Parameters with `= null` default that are not declared as nullable type (`Type? param = null`)

## Design (D)

- [ ] **Separation of concerns**: Calculation mixed with side effects in the same method
- [ ] **Testability**: Business logic coupled to framework lifecycle that could be extracted for testability — as pure static, injectable service, or standalone class
- [ ] **Dead code / magic numbers**: Unreachable code, unnamed numeric literals
- [ ] **God methods**: Cannot name the method's one job in a clause without `and`, or extracting a step would let a test target it — decompose
- [ ] **Pattern consistency**: New code inconsistent with dependency injection, lifecycle, or architectural patterns used in neighboring files
- [ ] **Hidden dependencies**: registry/singleton access in constructors or methods where injection is possible
- [ ] **Duplicate logic**: Tested helper class or static method exists but production code reimplements the same calculation inline — divergence risk
- [ ] **Bypassed family**: A new exposed bool/enum selects behavior in a file whose folder or base carries an abstract `*Strategy`/`*Config` sibling — the flag should be the strategy slot (`reference/rules/design_litmus.md` #1)
- [ ] **Neutered seam**: A call site passes literal `null`/default into a parameter typed as an existing strategy/config abstraction, hardcoding away a live extension point
- [ ] **Temporal-coupling protocol**: A caller must do two things in order for the change to work: set a flag then call a method, `Init` then `Start`, register then activate. One call, or doing the work where every input already exists, would remove the requirement. Both half-implementations compile, and the default value picks one of them (`reference/rules/design_litmus.md` #7)
- [ ] **Dual-concern knob**: A single exposed setting or bool selects more than one axis of behavior — its name can't describe everything that flips (`architecture_philosophy` §Authored-Data Integrity)
- [ ] **Orthogonal-axis rung**: A new subclass adds an axis that doesn't nest inside the base hierarchy's axis — belongs as a composable strategy or config on the base (`architecture_philosophy` §Orthogonal axis)
- [ ] **Re-authored value**: A new setting or field authors a value another surface already authors (stat, identity field, sibling definition) instead of deriving it
- [ ] **Feature envy**: A method reads or writes another type's data more than its own — belongs on the data it envies
- [ ] **Data clump**: Fields that change together and always travel as a group — a caller cannot pass one without the others, so extract a type
- [ ] **Shotgun surgery**: One logical change forced scattered edits across many files — the concern has no owning home
- [ ] **Divergent change**: One file changed in this diff for two unrelated reasons — split so each module changes for one reason
- [ ] **Speculative generality**: Abstraction, parameter, or hook added for a need neither the plan nor a user-stated evolution direction names (CLAUDE.coding.md *Modular when direction is known* — a *stated* direction is not speculative)
- [ ] **Message chain**: `a.B().C().D()` navigation coupling the caller to structure beyond its direct collaborator — hide the walk behind one method
- [ ] **Middle man**: A type or method that mostly forwards to one collaborator — deleting it would scatter nothing (`architecture_philosophy` §The Deletion Test)
- [ ] **Switch-on-type**: A new case would force edits at every `switch`/`if`-cascade on the same type or enum discriminator — use polymorphism or one shared map, unless the set is provably closed

## Semantics (S)

- [ ] **Misleading names**: Fields named as counts that are actually budgets (or vice versa). Methods whose behavior doesn't match their name
- [ ] **Missing XML docs**: Public API methods without XML doc comments explaining behavior, parameters, or return values
- [ ] **Inconsistent terminology**: Same concept referred to by different names across related files
- [ ] **Unit ambiguity**: Numeric parameters named "speed", "duration", "force" without documenting units (seconds, units/sec, degrees/sec) or valid range in name, XML doc, or a range attribute
- [ ] **Bool parameter traps**: Public methods with `bool` parameters that don't communicate purpose at call site — prefer enum, named constants, or method overloads
- [ ] **Primitive obsession**: `int` constants as pseudo-enums, raw `string` as identity keys where an enum or typed wrapper would be more expressive
- [ ] **Empty doc stubs**: `/// <summary> /// </summary>` with no content — worse than no doc (remove or fill in)
- [ ] **Comment as deodorant**: A comment explaining what a confusingly-named or overlong block does, where a rename or extraction would remove the need for it

## Robustness (R)

- [ ] **Division by zero**: Arithmetic without zero guards
- [ ] **Nullable references**: Accessed without null checks or `?` operator
- [ ] **TryGet null guard**: Missing `|| result == null` after `TryGet*` calls
- [ ] **Collection safety**: Operations on potentially empty collections without guards (e.g., `[0]`, `First()`, `Single()`)
- [ ] **Input validation**: Missing validation on public API boundaries
- [ ] **Data-driven ranges**: `Random.Next(min, max)` or arithmetic using authored min/max without `Math.Min`/`Math.Max` guard — designers can set Min > Max
- [ ] **Silent failures**: Empty catch blocks, return null/default without logging, `?.` silently skipping critical operations
- [ ] **Unhandled states**: Switch without default case, enum values not covered in pattern matching, state machines with missing transition coverage
- [ ] **Deferred init races**: Race conditions in deferred initialization (callbacks accessing state not yet set up)
- [ ] **Event disconnect**: C# event handlers subscribed during initialization without unsubscription in teardown — causes leaks or callbacks on freed objects
- [ ] **Deferred LINQ**: Storing `IOrderedEnumerable`/`IEnumerable` without materializing (`.ToList()`) — re-evaluates the query on every iteration

## Performance (P)

- [ ] **Hot-path allocations**: `new` objects or string concatenation in per-frame or per-tick callbacks
- [ ] **Per-frame lookups**: Dictionary/list creation or lookups per frame that should be cached at initialization
- [ ] **Per-call allocations**: Unnecessary dictionary/list creation per call that should be pooled or cached
- [ ] **LINQ in hot paths**: `.Where()`, `.Select()`, `.OrderBy()` in per-frame or per-tick callbacks — allocate closures and enumerators every frame

## Intuitiveness (I)

- [ ] **Unclear options**: Exposed settings or fields with unclear units or valid ranges, or a name that does not say what the value controls
- [ ] **Field interactions**: Non-obvious interactions between exposed fields (changing A invalidates B) without grouping or documentation
- [ ] **Bad defaults**: Default values that produce poor or no results out of the box
- [ ] **Complex signatures**: A call site needs comments or names to tell its arguments apart, or its arguments share a type — introduce a parameter object
- [ ] **Deep nesting**: A nested block whose purpose cannot be named without an extracted helper or early return — decompose
- [ ] **Vague method names**: Methods named `Evaluate`, `Resolve`, `Process`, `Handle` without qualifier communicating what they operate on
