---
paths:
  - "**/*.cs"
---

# C# & Godot Interop Patterns

**Context:** Mechanical patterns that fire when writing or editing `.cs` files — lifecycle ordering, nullability annotations, export discipline, defensive guards, test-helper conventions. Auto-loads on `.cs` reads. Companion to [`csharp_lsp.md`](csharp_lsp.md) (navigation tooling) and [`architecture_philosophy/SKILL.md`](../skills/architecture_philosophy/SKILL.md) (design philosophy).

## Lifecycle & Constructors

**Rule:** **NEVER** put game logic in the C# Constructor (`public MyClass()`).
- *Why:* The Godot Engine native side is not initialized yet.
- *Correct:* Use `_EnterTree()` for initialization or `_Ready()` for node wiring.

**Rule:** Perform all Node Lookups (`NodeExts`) inside `_Ready()` and cache the result. Never query the scene tree inside `_Process` — it's a hot-path scene-tree walk; cache in `_Ready`.

## Nullability Convention for Godot Properties

**Context:** Godot has no typical C# constructor, so properties start null until `_Ready()`.

| Scenario | Annotation | Rationale |
| :--- | :--- | :--- |
| Set in `_Ready()` | `= null!` | Guaranteed after initialization |
| **Required** `[Export]` | `= null!` + `[RequiredExport]` | Fail-fast with clear error |
| **Optional** `[Export]` | `?` nullable | Genuinely might not be set |
| Runtime state that can be null | `?` nullable | Could legitimately be null |

**Pattern:** Use `[RequiredExport]` attribute + `this.ValidateRequiredExports()` in `_Ready()`:
```csharp
[Export, RequiredExport] public SpellArchetype Archetype { get; set; } = null!;
[Export] public SpellArchetype? OptionalOverride { get; set; }  // No RequiredExport = optional

public override void _Ready()
{
    this.ValidateRequiredExports();  // One line validates ALL required exports
}
```
- *Why `= null!`:* Suppresses IDE warnings when accessing the property throughout code.
- *Why `[RequiredExport]`:* The attribute + validation method throws `NodeConfigurationException` (Nodes) or `ResourceConfigurationException` (Resources) with clear message if forgotten in Inspector.
- *Why not manual null checks:* Avoids "unnecessary null check" warnings and verbose boilerplate.

**Rule:** Every `[Export] = null!` **MUST** use `[RequiredExport]`:
- Declare: `[Export, RequiredExport] public Type Prop { get; set; } = null!;`
- Validate: `this.ValidateRequiredExports()` as first line in `_Ready()` (Nodes) or during initialization (Resources)
- **Resources:** `[RequiredExport]` + `ValidateRequiredExports()` works on `Resource` subclasses too (via `ResourceExts`, global namespace). Call during initialization since Resources don't have `_Ready()`. Throws `ResourceConfigurationException` instead of `NodeConfigurationException`.
- Enforced by `pattern_enforcer.py` hook — writing `[Export]...= null!` without `RequiredExport` is blocked.

## Defensive Patterns

### Event Initialization
**Rule:** Initialize events with `= delegate { }` to avoid null checks: `public event Action SomeEvent = delegate { };`

### Nullable Default Parameters
**Rule:** Parameters with `= null` default must be nullable: `void Method(StringName? reason = null)`. Fix base → all overrides.

### TryGet Null Guard
**Rule:** Add `|| result == null` after `TryGet`/`TryGetFirstChildOfType` calls to satisfy nullable analysis:
```csharp
if (!bb.TryGet<T>(key, out var result) || result == null) { return; }
```

### Data-Driven Range Guard
**Rule:** When `Random.Next(min, max)` uses editor-exported values, always guard with `Math.Min`/`Math.Max`:
```csharp
int min = Math.Min(typeData.MinSlots, typeData.MaxSlots);
int max = Math.Max(typeData.MinSlots, typeData.MaxSlots);
int result = rng.Next(min, max + 1);
```
*Why:* Designers can easily set Min > Max in the inspector. `Random.Next` throws `ArgumentOutOfRangeException` when `minValue > maxValue`. Guard at the consumption site, not the data source.

### Atomic Initialization
**Rule:** When a method can fail with an early return, dependent state mutations must happen inside the success path, not in the caller after the call.

*Bad:*
```csharp
target.Initialize(data);    // Can fail silently
target.Metadata = metadata;  // Runs even if Initialize failed
```
*Good:*
```csharp
target.Initialize(data, metadata);  // Sets metadata only on success
```
*Why:* Half-initialized objects cause subtle downstream bugs. The caller cannot distinguish success from failure when the method returns void.

## Signals vs Events

- **Gameplay Logic:** Use **C# Native Events** (`public event Action`).
    - *Why:* Faster, type-safe, refactor-friendly, easier to analyze and debug.
    - *Rule:* Do NOT use Godot Signals for game logic.
- **UI / Engine Interaction:** Use **Godot Signals** (`[Signal]`, `.Connect`).
    - *Why:* Required for UI Nodes (`Button.Pressed`) or Area3D detections.
    - *Rule:* Connect these in `_Ready` or via Editor if strictly visual.
- **Cross-Cutting vs Domain Events (Hybrid Architecture):**
    - *Cross-cutting events:* Use a centralized `EventBus` (autoload singleton) for events that span multiple unrelated systems (e.g., UI notifications any subscriber might care about).
    - *Domain-specific events:* Use domain registries (`PlayerRegistry`, `IngredientRegistry`) for events scoped to a single subsystem.
    - *Rule:* Prefer domain registries. Use EventBus only for truly cross-cutting events that don't belong to any single domain.

## Exports & Inspector

- **Numeric Parameters:** When a value could be constant OR attribute-driven (e.g., speed, cooldown, duration), export as `BaseFloatValueDefinition` rather than a raw `Attribute`. This lets designers choose `ConstantFloatDefinition` or `AttributeFloatDefinition` per-field without code changes. Resolve via `definition.ResolveFloatValue(statProvider)`.
- **Configuration:** Use `[Export]` for values designers (you) need to tweak (Speed, Damage, Prefabs).
- **References:** Use `[Export]` for assigning child nodes IF the structure is rigid.
    - *Better:* Use `GetNode<T>("%UniqueName")` for internal scene wiring to avoid Inspector rot.
- **Data Types:**
    - Prefer `Godot.Collections.Array<T>` over `System.Collections.Generic.List<T>` **only** if it must be visible in the Inspector.
    - Otherwise, use standard .NET Collections.
    - In files needing usings for `Godot.Collections` AND `System.Collections`, alias Godot as `using GCol = Godot.Collections;`.

## `[Tool]` Attribute — Editor-Time Type Registration

**Rule:** Blanket `[Tool]` on every `[GlobalClass]` **Resource** (`[GlobalClass, Tool]`); **selective** on Nodes — a Node gets `[Tool]` only if it has editor-time code (`Engine.IsEditorHint`, `_ValidateProperty`, `[ExportToolButton]`) or extends a framework-convention Node (`State` / `BehaviorTask` / `BTState`).

- **Cascade (why blanket Resources):** if a `[Tool]` script `[Export]`s a typed Resource (or `Array<>` / `Dictionary<,>` of one), that Resource AND every concrete subclass assignable to that field MUST also be `[Tool]` — Godot's source generator does NOT inherit the attribute. A gap throws `InvalidCastException` in the **editor only** (the generated setter loads the instance as a bare `Godot.Resource` and casts). **No GdUnit4 / runtime test can catch it** — at runtime every script is its real type.
- **Cost asymmetry (why selective Nodes):** `[Tool]` on a Resource is side-effect-free (editor only runs property setters); on a Node it makes the editor run `_EnterTree` / `_Ready` / `_Process`, firing game logic in-editor.
- **Escape hatch:** type the `[Export]` as base `Resource` / `Node` and cast at runtime (`prop as IFoo`) to break the cascade — used when exporting a non-`[Tool]` Jmodot Resource (Jmodot is a submodule; fix its gaps in a Jmodot PR, not a PP edit).
- *Enforced:* `pattern_enforcer.py` (edit-time — blocks a `[GlobalClass]` Resource without `[Tool]`) + `tool_cascade_audit.py` / `apply_blanket_tool.py` in `/regression_gate` step 1c (static graph) + headless `--import` (step 4b). Full mechanism + verified-empirically details: [`architecture_philosophy/SKILL.md`](../skills/architecture_philosophy/SKILL.md) → *`[Tool]` Attribute Policy*. Canon: `archive_tool_attribute_cascade_rules.md`.

## Async & Tasks

- **Rule:** Avoid `async void`. Use `async Task` or `async void` ONLY for top-level event handlers (e.g., Button pressed).
- **Rule:** Do not touch Godot Nodes from a background `Task.Run` thread. Use `CallDeferred` if returning to the main thread.

## Test Helper Setters

**Context:** The project uses a single assembly (tests and production in one `.csproj`), so `internal` provides no access control.

**Rule:** ALL `internal` methods added for test access MUST be wrapped in `#if TOOLS` / `#endif` within a `#region Test Helpers` block. This includes:
- **Property setters:** `Set<PropertyName>(<type> value)` — bypass private setters for test configuration
- **Test-prefixed methods:** `_Test<Action>()` — test hooks, simulation helpers, wiring checks
- **Simulation helpers:** `Simulate<Action>()` — trigger internal events/signals from tests
- **Reset methods:** `ResetForTesting()` — restore singleton/static state between test cases
- **SetTestValues:** `SetTestValues(...)` — bulk-set multiple properties for test scenarios

```csharp
#region Test Helpers
#if TOOLS
internal void SetDamageMultiplier(float value) => DamageMultiplier = value;
internal void _TestSimulateHit(ReactionContext ctx) => HandleHit(ctx);
internal event Action<PackedScene, Vector3>? _TestOnVFXSpawnRequested;
#endif
#endregion
```
- *Why `#if TOOLS`:* Godot's `Debug` configuration (used by editor, `dotnet build`, `dotnet test`) defines `TOOLS`, NOT `DEBUG`. Methods guarded by `#if TOOLS` are available during development and testing, but stripped from all exported builds. **Do NOT use `#if DEBUG`** — it is NOT defined during `dotnet test` in Godot.
- *Why not public setters:* Preserves encapsulation in the API surface.
- *Production invocations:* If production code invokes a test hook event (e.g., `_TestOnVFXSpawnRequested?.Invoke(...)`), the invocation site MUST also be wrapped in `#if TOOLS`. Otherwise export builds get a dangling reference.
- *Production usage:* If a "test helper" setter is called from production code, it is NOT a test helper — move it out of the `#region Test Helpers` block into the regular API.
- *Route observable-state setters through the production pathway:* if production mutates a property via a method that fires events/signals (`StartPhase(p)` → `RunPhaseChanged`), the test helper should call that method, not assign the property directly. Direct mutation creates a silent semantic split — tests see the new value without the side-effects, and subscribers that depend on the side-effect don't fire. Either route through the production method, or rename the helper to `SetForTest_BypassEvents(...)` so the divergence is intentional and grep-visible.
- *Enforcement:* Run `/audit_test_accessors` periodically to catch unguarded methods and dangerous production callers.

## Builder Pattern for Test Fixtures

**Rule:** Complex test setup should use a fluent Builder pattern: static `Create()` → `.With*()` → terminal `.Build()` or `.Execute()`.
- Eliminates duplicated setup code and makes test intent readable at a glance.
- *Location:* `Tests/Framework/Builders/`
- *Example:* `GameplayScenarioBuilder.Create().WithIngredients(...).WithSynergies(...).CraftSpell()`
- *Note:* Builder in production code is rarely needed — `SpellCrafter` and factory classes already serve this role.

## Touchpoints

- `pattern_enforcer.py` — hook enforcing the `[Export] = null!` + `[RequiredExport]` pairing.
- `Tests/Framework/Builders/GameplayScenarioBuilder` — canonical Builder example.
- Sibling rules on `**/*.cs`: [`csharp_lsp.md`](csharp_lsp.md) for symbol navigation; [`jmodot_utilities.md`](jmodot_utilities.md) for Jmodot framework utilities (NodeExts, JmoRng, JmoMath, Map, configuration exceptions, IComponent gotcha).
- Sibling rule on `Jmodot/**/*.cs` only: [`jmodot_framework_authoring.md`](jmodot_framework_authoring.md) for 2D/3D parity, framework boundary, static seam pattern.
- Companion skill: [`architecture_philosophy/SKILL.md`](../skills/architecture_philosophy/SKILL.md) for design-time decisions (Resource Strategy Hierarchies, DI, Marker Interfaces).
