# Authoring a test — attributes, fixtures, timing, teardown

Read while writing the test body; named from `SKILL.md` §Recipe index.

## GdUnit4 attributes

Since v5, tests run **WITHOUT the Godot runtime by default** (10x faster). Add `[RequireGodotRuntime]` only when needed.

| Attribute | Use For |
|-----------|---------|
| `[TestSuite]` | Mark test class |
| `[TestCase]` | Mark test method (default timeout: 5 min) |
| `[TestCase(Timeout = 10000)]` | Custom timeout in ms |
| `[TestCase(DoSkip = true, SkipReason = "...")]` | Skip test with reason |
| `[RequireGodotRuntime]` | GD.Load, Nodes, scenes, Vector3 |
| `[Before]`/`[After]` | Suite-level setup/teardown (once) |
| `[BeforeTest]`/`[AfterTest]` | Per-test setup/teardown (each test) |
| `[DataPoint(nameof(X))]` | Parameterized tests (C# only) |

## Assertions

```csharp
AssertThat(actual).IsEqual(expected);
AssertThat(actual).IsNotNull();
AssertThat(list).Contains(item);
// Note: No IsGreaterOrEqual() - use AssertThat(x >= y).IsTrue()
```
**Pin exact values for deterministic pure functions.** Known-constant inputs + a pure function (no randomness/state) ⇒ assert the exact result (`IsEqual(15.5f)`), not a range (`IsGreater(0f)`). Weak assertions mask constant drift silently.

## Testing Node subclasses directly

With `new NodeType()`, `_Ready()` is **NOT called** (the node never enters the scene tree).
- Initialize fields at **declaration time** where possible, not in `_Ready()`.
- Null-conditional for singletons: `EventBus.Instance?.Method()`.
- Null-coalescing for owner refs: `_owner?.Name ?? "Unknown"`.
- Orphan warnings are expected and don't affect test correctness.

## Fixtures

Base fixtures, builders, mocks, assertions live in `Tests/Framework/`. **Exception:** `ActivationTestFixture` is at `Tests/Integration/Casting/ActivationTestFixture.cs` (extends `AbilityTestFixture`).

**SceneTestFixture** (base): `LoadIngredient("Apple")` / `LoadArchetype("Watergun")`; `CraftFromIngredients(...)` / `CraftFromIngredientNames(...)`.

**AbilityTestFixture** (extends above): `Crafter` property (fresh per test); `HasEffect<T>()`, `GetEffects<T>()`, `GetPhysicsLayerType()`.

**ActivationTestFixture** (E2E spell tests):
- `LoadCasterScene()` / `GetCaster(runner)` — load scene with AbilityCasterService
- `LoadArchetype("Fireball")` / `CreateTestBlueprint(archetype, ...effects)` — create test spells
- `CountSpawnedSpells(runner)` / `GetSpawnedSpells(runner)` — count/get active spells
- **Pool isolation:** call `AbilityPoolManager.Instance?.ClearAllPools()` in `[BeforeTest]`
- **SetExportProperty helper:** prefer `#if TOOLS` test helpers (`architecture_philosophy` skill); reflection only for third-party types you can't modify

## Modular test modules

**Trigger:** create the module on the SECOND test for a system.
```
First test for HSM  → Inline setup (quick, specific)
Second test for HSM → Extract into reusable module
```

**Existing:** `AbilityTestFixture`, `ActivationTestFixture`, `ScenarioFactory`, `AbilityAssertions`, `TreeFixture`

## E2E waits

| E2E wait | Purpose |
|------|---------|
| `100ms` | Charge/initialization, physics server registration after AddChild |
| `200ms` | Collision processing (Area3D overlap) |
| `400ms` | Pool return completion |
| `500-800ms` | AbilitySpawner spawn-count assertions (heavy synchronous per-spawn work) |

**AbilitySpawner timing caveat:** `SpawnChild()` does heavy synchronous work per spawn (scene instantiation, visual loading, collision shape adoption, combat wiring), eating frame budget — use `Duration >= 0.3s`, `SpawnInterval <= 0.05s`, and generous `AwaitMillis` (500-800ms) for spawn-count assertions.

## Usage

```csharp
[TestSuite]
public partial class MyTests : AbilityTestFixture
{
    [TestCase, RequireGodotRuntime]
    public void Test_Spell_Has_Effect()
    {
        var blueprint = CraftFromIngredientNames("Apple");
        AssertThat(HasEffect<SomeEffect>(blueprint)).IsTrue();
    }
}
```

## Production resource coupling

Tests loading production `.tres`/`.tscn` from outside `Tests/` are fragile to designer rebalancing. **Preferred:** frozen test data in `Tests/Fixtures/Data/` with known stat values. When production resources are unavoidable (integration/smoke), classify assertions:

| Fragility | Example | Action |
|-----------|---------|--------|
| **FRAGILE (value)** | `DamageMultiplier == 0.5f` | Replace with `> 0f` or baseline comparison |
| **FRAGILE (config guard)** | `DoNotInherit == true` | Keep — safety net for game-breaking bugs |
| **Structural** | `IsInstanceOf<CompositeOutcome>()` | Acceptable for integration smoke tests |

**Baseline comparison pattern** (modifier tests):
```csharp
var baseline = crafter.Create(Array.Empty<Item>(), null);
var withItem = crafter.Create(new[] { compass }, null);
AssertThat(withItem.Stat).IsGreater(baseline.Stat);
```

## Seam-Injected Dependencies Need One Real-Scene Test

When a node's production dependencies are wired via scene/Inspector (`[Export]` node refs, autoload children) but tests supply them through a `#if TOOLS SetXForTesting` seam, **at least one test must load the real production scene** (`ResourceLoader.Load<PackedScene>(...).Instantiate<T>()` + `AddChild`). A suite that *only* injects via the seam never exercises production wiring — a missing scene (e.g. a script-only autoload that can't satisfy `[RequiredExport]` node refs) then passes every test while being null at runtime. Assert by behavior (the dependency does its job), not that the field is set. Sibling: `archive_godot_node_init_timing.md`.

## Mock at boundaries only

Mock at **system boundaries**, never at internal collaborators. Applies to every double — mock, stub, spy, hand-rolled fake.

| Mock | Don't mock |
|------|------------|
| External services Jmodot doesn't own | Your own classes, components, States |
| Time-of-day / wall-clock | Anything in the project's own namespaces you control |
| RNG seeds (use `JmoRng` seeding, not a mock) | Anything in `Jmodot.*` you control |
| File system reads (sometimes — prefer fixture files) | `IBlackboard`, `IComponent`, `IAbility`, etc. — use real instances or fixtures |

**Warning sign:** the test breaks when you refactor an internal collaborator though *behavior* is unchanged — you mocked too deep, and the test now pins implementation, not contract.

**The Godot runtime is not a boundary you double.** Engine APIs (nodes, scene tree, physics, `GD.Load`) run for real via `[RequireGodotRuntime]` / `ISceneRunner`; the engine-lifecycle failure class is exactly what a double hides. Where a double is unavoidable, its Godot base type must be the type the *consumer* resolves against, not merely one satisfying the physics API (`arch_rule_godot_base_type_proven_by_consumer_resolution.md`).

**Testability of system-boundary code:**
- Inject dependencies (`IRngSource` parameter) rather than `new`-ing externally inside the method.
- Prefer specific operations (one method per external call shape) over generic `Fetch(string endpoint, params...)` interfaces — each becomes independently mockable without conditional logic in the mock setup.
- For Components, lean on `IBlackboard` + fixture-driven setup (`AbilityTestFixture`, `ActivationTestFixture`, `TreeFixture`) rather than mock collaborators.

The Don't Mock column is a boundary constraint, not a cost/benefit tradeoff — setup cost is not a counterweight. Name the fixture and proceed.

**Reference:** `archive_testing_design_patterns.md` for fixture-vs-mock tradeoffs in project-specific contexts (real `Blackboard` instance vs. fake).

## Godot timing gotchas

**SetDeferred + ProcessFrame is non-deterministic with 1 frame** — `SetDeferred` queues property changes for idle-time processing, but `ProcessFrame` can fire BEFORE the queue flushes. **Always await 2 frames** when asserting `SetDeferred` results:
```csharp
shape.SetDeferred(CollisionShape3D.PropertyName.Disabled, true);
// BAD: flaky, especially with nested nodes
await tree.ToSignal(tree, SceneTree.SignalName.ProcessFrame);
// GOOD: reliable at all nesting depths
await tree.ToSignal(tree, SceneTree.SignalName.ProcessFrame);
await tree.ToSignal(tree, SceneTree.SignalName.ProcessFrame);
```

**Programmatic nodes need explicit setup:**
- `AddChild()` does NOT set `Owner` — set `hurtbox.Owner = target` explicitly.
- `Initialize()` calls using `SetDeferred` (Monitorable, etc.) need scene tree + 2 frames.
- Wait 100ms after `AddChild(target)` for the physics server to register Area3D nodes.
- `SetDeferred` properties don't take effect on nodes outside the scene tree.

**Float accumulation in duration tests** — testing time-based BT actions or timers, avoid accumulating small deltas (`60 × 1f/60f ≠ 1.0f`, IEEE 754). Use a single large delta:
```csharp
// BAD — accumulated error means elapsed never precisely hits threshold
for (int i = 0; i < 60; i++) action.ProcessPhysics(1f / 60f);
// GOOD — single delta reliably crosses the threshold
action.ProcessPhysics(duration + 0.1f);
```

**BB Subscribe callbacks receive boxed Variant, not raw types** — `Blackboard.Subscribe` fires `Action<object>` whose value is a `Variant` boxed as `object`, so `value is true` **silently fails**. Affects ALL BB subscription handlers:
```csharp
// BAD — silently never matches
bool flag = value is true;
// GOOD — unwrap the Variant first
bool flag = value is Variant v && v.AsBool();
```

## Teardown Doctrine & Orphan Prevention

**`Free()`/`QueueFree()` are for Nodes ONLY.** `Resource`/`RefCounted`-derived objects are reference-counted — NEVER `Free()` them in teardown; drop the references (null the field, clear the tracking list) and let refcounting collect. Freeing a Resource throws paired engine errors per call (`Can't free a RefCounted object.` + `Invalid call. Nonexistent function 'free' in base '<T>'`) — thousands per full-suite run in `TestResults/godot_test.log`.

| Object being torn down | Correct cleanup |
|---|---|
| Node in scene tree | `using ISceneRunner` (auto), or parent it (freed with parent), or `QueueFree()` |
| Out-of-tree Node (`new NodeType()`) | `Free()` in `[AfterTest]`/`[After]` |
| Resource / RefCounted (loaded `.tres`, `new SomeResource()`) | Drop references — no Free/QueueFree call at all |

**No numeric orphan/leak ceiling exists today.** At process exit Godot prints one engine ERROR per leaked Node (`Cannot get path of node...` in the ObjectDB leak dump), so `TestResults/godot_test.log` error counts scale with orphan count, not bug count — and exit code `-1073740791` correlates with accumulation. Leak-dump math and log interpretation: `diagnostics_toolkit` skill.

```csharp
// 1. Use 'using' with ISceneRunner (auto-cleanup)
using ISceneRunner runner = ISceneRunner.Load("res://scene.tscn");

// 2. Or parent nodes (freed with parent)
parentNode.AddChild(newNode);

// 3. Or manual cleanup in [After]
[After]
public void TearDown() => _node?.QueueFree();
```
