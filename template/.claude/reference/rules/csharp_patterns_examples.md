# `csharp_patterns.md` — worked examples and mechanism

Read a section when [`rules/csharp_patterns.md`](../../rules/csharp_patterns.md) names it. This file does not auto-load; the rule carries the decisions, this carries the snippets, the failure mechanics behind them, and the material that only applies while writing one specific shape.

## Logging failure semantics

`JmoLogger.Error` fails a test only in suites that install a logger spy from the project's test framework — it is opt-in. Engine `ERROR` lines never fail tests.

## Comment discipline

- **Why the trust radius exists:** nothing validates doc comments, so a claim outside the member's own radius is unowned and rots silently.
- **Observation, in full:** a report on code elsewhere — who calls this, what another implementation does, whether two bodies match, what happened historically. None of it is a caller obligation, so none of it belongs in `///`.
- **cref diagnostics:** `CS1574` is an unresolved cref and `CS0419` an ambiguous one; disambiguate an ambiguous cref with a signature. A `cref` pointing at a parameter or type parameter emits the warning the `DOCS` gate blocks on — use `<paramref name="x"/>` and `<typeparamref>` instead.
- **Doc-only commits to recent code are a smell.** Cut the code over clarifying it.

## Export tooltip mechanics

A `<summary>` on an `[Export]` reaches the designer-facing Inspector tooltip through Jmodot's `Tools/DocTooltips/`, wired by `addons/csharp_doc_tooltips`. The engine supplies nothing here: `CSharpScript::get_documentation()` is an empty stub at 4.7.1.

Two consequences at the authoring site:

- Hovering the export's **value widget** shows the summary; hovering its **label** shows nothing.
- `#if TOOLS` setters need no `///` — they never reach the Inspector.

## Data-driven range guard

```csharp
int min = Math.Min(typeData.MinSlots, typeData.MaxSlots);
int max = Math.Max(typeData.MinSlots, typeData.MaxSlots);
int result = rng.Next(min, max + 1);
```

## Float aggregation

```csharp
float mean = (float)values.Select(v => (double)v).Average();  // not values.Average()
```

.NET 9 vectorizes `Average()`/`Sum()` over a `float` source and reduces in float lanes, so the result depends on lane count and diverges from a scalar sum. The symptom is a test failing on the ~7th decimal — it reads as a tolerance problem and is a summation-order problem. This applies to any statistic derived from authored `float` data: distribution means, stat aggregates, simulation reports.

## Atomic initialization

```csharp
target.Initialize(data);     // Bad — can fail silently
target.Metadata = metadata;  //       runs even if Initialize failed

target.Initialize(data, metadata);  // Good — sets metadata only on success
```

## Fail-closed float guards

```csharp
// Bad — NaN fails `<= 0f`, falls through, and Acos(Clamp(NaN)) returns NaN.
public float Degrees => Speed <= 0f ? 90f : RadToDeg(Acos(Clamp(Along / Speed, 0f, 1f)));

// Good — !(x > 0f) catches NaN, zero and negatives in one test.
public float Degrees => !(Speed > 0f) || !float.IsFinite(Along) ? 90f : ...;
```

## `[Tool]` cascade mechanics

- **Why the cascade exists:** Godot's source generator does not inherit `[Tool]`. When a `[Tool]` script `[Export]`s a typed Resource, the generated setter loads the instance as a bare `Godot.Resource` and casts it, so a subclass without `[Tool]` throws `InvalidCastException` — **in the editor only**. No GdUnit4 or runtime test catches it, because at runtime every script is its real type.
- **Why Nodes are selective and Resources are blanket:** `[Tool]` on a Resource is side-effect-free (the editor only runs property setters); on a Node the editor runs `_EnterTree` / `_Ready` / `_Process`, firing game logic in-editor.
- **Escape hatch:** type the `[Export]` as base `Resource` / `Node` and cast at runtime (`prop as IFoo`) to break the cascade. This is the route for exporting a non-`[Tool]` Jmodot Resource — Jmodot is a submodule, so fix its gaps in a Jmodot PR, not a project-side edit.
- **Enforcement chain:** `pattern_enforcer.py` blocks a `[GlobalClass]` Resource without `[Tool]` at edit time; `tool_cascade_audit.py` / `apply_blanket_tool.py` check the static graph in `/regression_gate` step 1c; headless `--import` runs in step 4b. Full mechanism: [`architecture_philosophy/SKILL.md`](../../skills/architecture_philosophy/SKILL.md) → *`[Tool]` Attribute Policy*.
- **Parameterless constructor entry points:** `_ParseEnd` is one of the engine-invoked `_Parse*` entry points that must tolerate the inert recreated instance.

## Parameterless-constructor requirement

On assembly reload, `ScriptManagerBridge` recreates a managed instance for every script-bearing object and can only call a parameterless constructor. A class with only a constructor-injected form throws `MissingMemberException` there. The throw is caught and logged, never propagated — the editor then takes a native access violation frames later, so the crash record names only the AV and the explaining exception exists **only on the editor's stdout** (`godot.log` is the game's).

Investigating any editor crash, capture stdout first:

```
<install>/Godot_*_console.exe --editor --path <dir> > <log> 2>&1
```

## Test helpers

Tests and production share one `.csproj`, so `internal` provides no access control; the `#if TOOLS` guard is what keeps test access out of the shipped surface.

- **Sanctioned shapes:** `Set<PropertyName>(...)` property setters, `_Test<Action>()` hooks, `Simulate<Action>()` event triggers, `ResetForTesting()`, and bulk `SetTestValues(...)`.
- **Why `#if TOOLS`:** Godot's `Debug` configuration (editor, `dotnet build`, `dotnet test`) defines `TOOLS`, not `DEBUG`, so guarded members exist in development and are stripped from exported builds. `#if DEBUG` code is absent under `dotnet test`.
- **Production invocations:** an unguarded production call such as `_TestOnVFXSpawnRequested?.Invoke(...)` leaves export builds with a dangling reference.
- **Why route through the production method:** direct mutation splits semantics silently. Tests see the new value without its side effects, and subscribers depending on them do not fire. The `SetForTest_BypassEvents(...)` name makes a deliberate divergence grep-visible.
- **Enforcement:** run `/audit_test_accessors` to catch unguarded members and production callers.
- **Fixture setup:** complex setup uses a fluent Builder; see §Builder pattern for test fixtures.

## Test-helper block example

```csharp
#region Test Helpers
#if TOOLS
internal void SetDamageMultiplier(float value) => DamageMultiplier = value;
internal void _TestSimulateHit(HitContext ctx) => HandleHit(ctx);
internal event Action<PackedScene, Vector3>? _TestOnVFXSpawnRequested;
#endif
#endregion
```

Public setters instead of `#if TOOLS` members would break encapsulation in the shipped API surface.

## Builder pattern for test fixtures

Complex test setup uses a fluent Builder: static `Create()` → `.With*()` → terminal `.Build()` or `.Execute()`. It removes duplicated setup and makes test intent readable at a glance.

- *Location:* the test framework's builders folder
- *Canonical shape:* `ScenarioBuilder.Create().WithPlayer(...).WithEnemies(...).Execute()`
- *Production:* a Builder in production code is rarely needed — the project's factory classes already serve this role.
