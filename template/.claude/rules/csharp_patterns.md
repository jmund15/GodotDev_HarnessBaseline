---
paths:
  - "**/*.cs"
---

# C# & Godot Interop Patterns

Mechanical patterns for writing or editing `.cs`: lifecycle ordering, nullability, export discipline, defensive guards, test-helper conventions. Siblings and companion skill: §Touchpoints.

## Core Conventions (project-level)

- **Pure functions** wherever possible. **Control flow:** no nested if/else, early returns, ALWAYS brackets `{}`.
- **Logging:** `JmoLogger.Info/Warning/Error`, never `GD.Print`. Log STATE CHANGES, not state. `JmoLogger.Error` fails tests only in suites installing `JmoLoggerSpy` (`Tests/Framework/JmoLoggerSpy.cs`) — opt-in; engine ERROR lines never fail tests.
- **Comments default to none.** Add one when WHY is non-obvious to a cold reader (invariants, race hazards, tuning rationale). NEVER restate WHAT; never cite task/PR/"Phase X"/dates/CLAUDE.md rules; never project history (vault-only, unlinked from source). Litmus: *"If I delete this, will a maintainer 6 months out make a wrong decision?"* No → don't write it. Doc-only commits to recent code are a smell; cut over clarify. **A sanctioned WHY is 1–3 lines stating the invariant itself** — build-up, the bug it prevents and alternatives go in the commit message.
- **Trust radius — a `///` is authoritative about its own member and nothing else.** `<summary>` states that member's contract; `<remarks>` carries longer supplemental explanation. Nothing validates doc comments, so outside the radius they are unowned claims that rot silently.
- **Obligation, not observation.** *Obligation* = a constraint a caller must honour, or a guarantee the member makes: invariants, call-ordering, required configuration, failure modes, what it throws. Cross-file by nature, REQUIRED on seams (`design_litmus.md` #7); a collaborator-owned constraint (a driver's phase ordering, a base's contract) is still an obligation. *Observation* = a report on code elsewhere: who calls this, what another implementation does, whether two bodies match, what happened historically. **Litmus: must a caller honour this to use the member correctly?** Yes → obligation, write it richly. No → observation, cut it.
- **References are cref, not font.** A member named inside `///` uses `<see cref="X"/>`, compiler-resolved (CS1574 unresolved, CS0419 ambiguous — disambiguate with a signature). A **parameter** uses `<paramref name="x"/>`, a type parameter `<typeparamref>`; a cref to either emits the warning the gate blocks on. `<c>` is typographic and unchecked — correct for non-symbols (literals, snippets, expressions, pseudo-code).
- **TODO, future-tense, and notes to yourself or to agents go in `//` line comments.** Never `///` — a `<summary>` on an `[Export]` ships verbatim to the designer-facing tooltip.
- **`<summary>` on `[Export]` is softer** — it surfaces on IDE hover and as the Inspector tooltip via Jmodot's `Tools/DocTooltips/` (wired by `addons/csharp_doc_tooltips`); the engine supplies none (`CSharpScript::get_documentation()` is an empty stub at 4.7.1). Put the `///` above **ALL** the member's attributes, `[ExportGroup]` included — between them it is orphaned (CS1587), dropped from the XML sidecar, and the tooltip silently vanishes. Hovering the export's VALUE WIDGET shows the summary; its label shows nothing. `#if TOOLS` setters need no `///`.
- **Repair on sight.** Fix a false or dangling doc comment in the turn you find it (`feedback_dont_defer_immediately_addressable.md`). Enforced at commit by the `DOCS` check in `/regression_gate`; full-tree sweep: `.claude/scripts/doc_warning_check.sh`.
- **Strings:** prefer `StringName` for Godot identifiers (node paths, signal/animation names).

## Lifecycle & Constructors

**Rule:** **NEVER** put game logic in the C# constructor (`public MyClass()`) — the Godot native side is not initialized yet. Use `_EnterTree()` for initialization, `_Ready()` for node wiring.

**Rule:** Perform all node lookups (`NodeExts`) inside `_Ready()` and cache the result. Never query the scene tree inside `_Process` — hot-path scene-tree walk.

## Nullability Convention for Godot Properties

Godot has no typical C# constructor, so properties start null until `_Ready()`.

| Scenario | Annotation | Rationale |
| :--- | :--- | :--- |
| Set in `_Ready()` | `= null!` | Guaranteed after initialization |
| **Required** `[Export]` | `= null!` + `[RequiredExport]` | Fail-fast with clear error |
| **Optional** `[Export]` | `?` nullable | Genuinely might not be set |
| **Collection** `[Export]` (`GCol.Array<T>` / `Dictionary<K,V>`) | `= new()` | Editor replaces the default on load; empty default = iterable without null checks. NEVER `= new()` a *Resource-typed* export — pathless Resources serialize inline into every referencing `.tres` (use the rows above). |
| Runtime state that can be null | `?` nullable | Could legitimately be null |

**Pattern:** `[RequiredExport]` + `this.ValidateRequiredExports()` in `_Ready()`:
```csharp
[Export, RequiredExport] public SpellArchetype Archetype { get; set; } = null!;
[Export] public SpellArchetype? OptionalOverride { get; set; }  // No RequiredExport = optional

public override void _Ready()
{
    this.ValidateRequiredExports();  // One line validates ALL required exports
}
```
`= null!` suppresses IDE warnings at every access site; `[RequiredExport]` throws `NodeConfigurationException` (Nodes) / `ResourceConfigurationException` (Resources) with a clear message when the Inspector slot is empty; manual null checks instead would draw "unnecessary null check" warnings and boilerplate.

**Rule:** Every `[Export] = null!` **MUST** use `[RequiredExport]`:
- Declare: `[Export, RequiredExport] public Type Prop { get; set; } = null!;`
- Validate: `this.ValidateRequiredExports()` as first line in `_Ready()` (Nodes) or during initialization (Resources)
- **Resources:** works on `Resource` subclasses too (via `ResourceExts`, global namespace); call during initialization since Resources have no `_Ready()`.
- Enforced by `pattern_enforcer.py` — `[Export]...= null!` without `RequiredExport` is blocked.

## Defensive Patterns

### Event Initialization
**Rule:** initialize events with `= delegate { }` to avoid null checks: `public event Action SomeEvent = delegate { };`

### Nullable Default Parameters
**Rule:** parameters with `= null` default must be nullable: `void Method(StringName? reason = null)`. Fix base → all overrides.

### TryGet Null Guard
**Rule:** add `|| result == null` after `TryGet`/`TryGetFirstChildOfType` calls to satisfy nullable analysis:
```csharp
if (!bb.TryGet<T>(key, out var result) || result == null) { return; }
```

### Data-Driven Range Guard
**Rule:** when `Random.Next(min, max)` uses editor-exported values, guard with `Math.Min`/`Math.Max` — designers can set Min > Max, and `Random.Next` throws `ArgumentOutOfRangeException` when `minValue > maxValue`. Guard at the consumption site, not the data source.
```csharp
int min = Math.Min(typeData.MinSlots, typeData.MaxSlots);
int max = Math.Max(typeData.MinSlots, typeData.MaxSlots);
int result = rng.Next(min, max + 1);
```

### Float Aggregation Accumulates in `double`
**Rule:** never `Enumerable.Average()`/`Sum()` over a `float` source. Cast to `double` first, or accumulate explicitly.
```csharp
float mean = (float)values.Select(v => (double)v).Average();  // not values.Average()
```
.NET 9 vectorizes these over `float` and reduces in float lanes, so the result depends on lane count and diverges from a scalar sum. The symptom is a test failing on the ~7th decimal — reads as a tolerance problem, is a summation-order problem. Applies to any statistic derived from authored `float` data (`ScalarDistribution` means, stat aggregates, sim reports).

### Atomic Initialization
**Rule:** when a method can fail with an early return, dependent state mutations happen inside the success path, not in the caller after the call. A void return leaves the caller unable to tell success from failure, and half-initialized objects cause subtle downstream bugs.
```csharp
target.Initialize(data);     // Bad — can fail silently
target.Metadata = metadata;  //       runs even if Initialize failed

target.Initialize(data, metadata);  // Good — sets metadata only on success
```

### Fail-Closed Guards on Data-Resolved Floats
**Rule:** `x <= 0f` is not a fail-closed guard. Every comparison against NaN is false, so NaN passes the guard and reaches the math behind it. Write `!(x > 0f)`, and test `float.IsFinite` on every operand a designer can author.
```csharp
// Bad — NaN fails `<= 0f`, falls through, and Acos(Clamp(NaN)) returns NaN.
public float Degrees => Speed <= 0f ? 90f : RadToDeg(Acos(Clamp(Along / Speed, 0f, 1f)));

// Good — !(x > 0f) catches NaN, zero and negatives in one test.
public float Degrees => !(Speed > 0f) || !float.IsFinite(Along) ? 90f : ...;
```
NaN in a threshold comparison **inverts** the gate rather than breaking it — `if (value > max) { reject; }` stops rejecting entirely, so the failure presents as a gate that silently passes everything. Guard where the value is produced AND where it is consumed: any float resolved from `[Export]`/`.tres` data (mass, speed, radius, an angle cone) is not compiler-guaranteed finite.

*Litmus:* for each float guard, ask what happens when the value is NaN — if the answer is "the branch I wrote to be safe doesn't run", the test is inverted. Sibling of the range guard above.

## Signals vs Events

- **Gameplay Logic:** use **C# native events** (`public event Action`) — faster, type-safe, refactor-friendly. Do NOT use Godot Signals for game logic.
- **UI / Engine Interaction:** use **Godot Signals** (`[Signal]`, `.Connect`) — required for UI Nodes (`Button.Pressed`) and Area3D detections. Connect in `_Ready`, or via Editor if strictly visual.
- **Cross-Cutting vs Domain Events:** a centralized `EventBus` autoload carries events spanning multiple unrelated systems (UI notifications any subscriber might care about); domain registries (`PlayerRegistry`, `IngredientRegistry`) carry events scoped to one subsystem. Prefer domain registries; EventBus only for events belonging to no single domain.

## Exports & Inspector

- **Numeric Parameters:** when a value could be constant OR attribute-driven (speed, cooldown, duration), export `BaseFloatValueDefinition` rather than a raw `Attribute`, so designers pick `ConstantFloatDefinition` or `AttributeFloatDefinition` per-field without code changes. Resolve via `definition.ResolveFloatValue(statProvider)`.
- **Configuration:** use `[Export]` for values designers tweak (Speed, Damage, Prefabs).
- **References:** use `[Export]` for assigning child nodes IF the structure is rigid. *Better:* `GetNode<T>("%UniqueName")` for internal scene wiring, to avoid Inspector rot.
- **Data Types:** prefer `Godot.Collections.Array<T>` over `System.Collections.Generic.List<T>` **only** if it must be Inspector-visible; otherwise standard .NET collections. In files needing both usings, alias `using GCol = Godot.Collections;`.

## `[Tool]` Attribute — Editor-Time Type Registration

**Rule:** blanket `[Tool]` on every `[GlobalClass]` **Resource** (`[GlobalClass, Tool]`); **selective** on Nodes — a Node gets `[Tool]` only with editor-time code (`Engine.IsEditorHint`, `_ValidateProperty`, `[ExportToolButton]`) or when it extends a framework-convention Node (`State` / `BehaviorTask` / `BTState`).

- **Cascade (why blanket Resources):** if a `[Tool]` script `[Export]`s a typed Resource (or `Array<>` / `Dictionary<,>` of one), that Resource AND every concrete subclass assignable to that field MUST also be `[Tool]` — Godot's source generator does NOT inherit the attribute. A gap throws `InvalidCastException` in the **editor only** (the generated setter loads the instance as a bare `Godot.Resource` and casts). **No GdUnit4 / runtime test catches it** — at runtime every script is its real type.
- **Cost asymmetry (why selective Nodes):** `[Tool]` on a Resource is side-effect-free (the editor only runs property setters); on a Node the editor runs `_EnterTree` / `_Ready` / `_Process`, firing game logic in-editor.
- **Escape hatch:** type the `[Export]` as base `Resource` / `Node` and cast at runtime (`prop as IFoo`) to break the cascade — used when exporting a non-`[Tool]` Jmodot Resource (Jmodot is a submodule; fix its gaps in a Jmodot PR, not a {{PROJECT_NAME}} edit).
- **Every `GodotObject`-derived script class declares a PARAMETERLESS constructor**, dependencies field-init `= null!` and injected through a second ctor. On assembly reload `ScriptManagerBridge` recreates a managed instance for every script-bearing object and can only call a parameterless ctor; a constructor-injected-only class throws `MissingMemberException` there. The throw is caught and logged, never propagated — the editor takes a native access violation frames later, so the crash record names only the AV and the explaining exception exists ONLY on the editor's stdout (`godot.log` is the game's). Guard engine-invoked entry points (`_ParseEnd`, `_Parse*`) against the inert recreated instance. Editor-only like the cascade — no GdUnit4 test catches it. **Investigating any editor crash, capture stdout FIRST:** `<install>/Godot_*_console.exe --editor --path <dir> > <log> 2>&1`.
- *Enforced:* `pattern_enforcer.py` (edit-time — blocks a `[GlobalClass]` Resource without `[Tool]`) + `tool_cascade_audit.py` / `apply_blanket_tool.py` in `/regression_gate` step 1c (static graph) + headless `--import` (step 4b). Full mechanism: [`architecture_philosophy/SKILL.md`](../skills/architecture_philosophy/SKILL.md) → *`[Tool]` Attribute Policy*. Canon: `archive_tool_attribute_cascade_rules.md`.

## Async & Tasks

- **Rule:** avoid `async void`. Use `async Task`, or `async void` ONLY for top-level event handlers (Button pressed).
- **Rule:** do not touch Godot Nodes from a background `Task.Run` thread. Use `CallDeferred` to return to the main thread.

## Test Helper Setters

Tests and production share one `.csproj`, so `internal` provides no access control.

**Rule:** ALL `internal` methods added for test access MUST be wrapped in `#if TOOLS` / `#endif` within a `#region Test Helpers` block:
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
- *Why `#if TOOLS`:* Godot's `Debug` configuration (editor, `dotnet build`, `dotnet test`) defines `TOOLS`, NOT `DEBUG`, so those members are available in development and stripped from exported builds. **Do NOT use `#if DEBUG`** — it is NOT defined during `dotnet test` in Godot. Public setters instead would break encapsulation in the API surface.
- *Production invocations:* a production site invoking a test-hook event (`_TestOnVFXSpawnRequested?.Invoke(...)`) MUST also be wrapped in `#if TOOLS`, or export builds get a dangling reference.
- *Production usage:* a "test helper" setter called from production code is NOT a test helper — move it out of `#region Test Helpers` into the regular API.
- *Route observable-state setters through the production pathway:* when production mutates a property via a method that fires events/signals (`StartPhase(p)` → `RunPhaseChanged`), the helper calls that method rather than assigning directly. Direct mutation splits semantics silently — tests see the new value without the side-effects, and subscribers depending on them don't fire. Either route through the production method, or rename to `SetForTest_BypassEvents(...)` so the divergence is intentional and grep-visible.
- *Enforcement:* run `/audit_test_accessors` periodically to catch unguarded methods and dangerous production callers.

## Builder Pattern for Test Fixtures

**Rule:** complex test setup uses a fluent Builder: static `Create()` → `.With*()` → terminal `.Build()` or `.Execute()`.
- Eliminates duplicated setup code and makes test intent readable at a glance.
- *Location:* `Tests/Framework/Builders/`
- *Example:* `GameplayScenarioBuilder.Create().WithIngredients(...).WithSynergies(...).CraftSpell()`
- *Note:* a Builder in production code is rarely needed — `SpellCrafter` and factory classes already serve this role.

## Touchpoints

- `pattern_enforcer.py` — hook enforcing the `[Export] = null!` + `[RequiredExport]` pairing.
- `Tests/Framework/Builders/GameplayScenarioBuilder` — canonical Builder example.
- Sibling rules on `**/*.cs`: [`csharp_lsp.md`](csharp_lsp.md) for symbol navigation; [`jmodot_utilities.md`](jmodot_utilities.md) for Jmodot utilities (NodeExts, JmoRng, JmoMath, Map, configuration exceptions, IComponent gotcha).
- Sibling rule on `Jmodot/**/*.cs` only: [`jmodot_framework_authoring.md`](jmodot_framework_authoring.md) for 2D/3D parity, framework boundary, static seam pattern.
- Companion skill: [`architecture_philosophy/SKILL.md`](../skills/architecture_philosophy/SKILL.md) for design-time decisions (Resource Strategy Hierarchies, DI, Marker Interfaces).
