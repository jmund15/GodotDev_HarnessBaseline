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
| `[RequireGodotRuntime]` | Any Godot type: GD.Load, Nodes, scenes, value types (`Vector2`/`Vector3`/`StringName`); GdUnit0501 enforces it |
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
**Pin exact values for deterministic pure functions.** Known-constant inputs + a pure function (no randomness/state) ⇒ assert the exact result (`IsEqual(15.5f)`), not a range (`IsGreater(0f)`). Weak assertions mask constant drift silently. A production data value is never pinned: `rules/test_authoring.md` #12 owns that boundary, and #6 owns sweeping a derivation instead of pinning its shipped value.

## Testing Node subclasses directly

With `new NodeType()`, `_Ready()` is **NOT called** (the node never enters the scene tree).
- Initialize fields at **declaration time** where possible, not in `_Ready()`.
- Null-conditional for singletons: `EventBus.Instance?.Method()`.
- Null-coalescing for owner refs: `_owner?.Name ?? "Unknown"`.
- Free the out-of-tree nodes you create (teardown table below); an engine orphan warning does not fail the test.

## Fixtures

Fixtures, builders, mocks and assertions live under `Tests/Framework/`; read each fixture's source for its API — an inventory here drifts. **Exception:** `ActivationTestFixture` is at `Tests/Integration/Casting/ActivationTestFixture.cs` (extends `AbilityTestFixture`).

- **Pool isolation:** call `AbilityPoolManager.Instance?.ClearAllPools()` in `[BeforeTest]`
- **Setting an export from a test:** `rules/csharp_patterns.md` §Test Helper Setters owns the `#if TOOLS` helper preference over reflection.

## Modular test modules

**Trigger:** a second test repeats the setup — extract it into a shared module. `rules/test_authoring.md` #2 owns a different trigger, reuse of a double or fixture that already exists.

## E2E waits

Wait on an observable condition where one exists; where a fixed delay is unavoidable, use the shortest that passes stably. `rules/test_authoring.md` #9 owns which wait to choose.

**AbilitySpawner timing caveat:** `SpawnChild()` does heavy synchronous work per spawn (scene instantiation, visual loading, collision shape adoption, combat wiring), eating frame budget. A spawn-count assertion therefore needs headroom on `Duration` and `SpawnInterval`, and waits on the count as a condition, not on a padded fixed delay.

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
| **FRAGILE (config guard)** | `DoNotInherit == true` | Assert the guard's effect, not its value; `rules/test_authoring.md` #12 forbids pinning the value |
| **Structural** | `IsInstanceOf<CompositeOutcome>()` | Acceptable for integration smoke tests |

**Baseline comparison pattern** (modifier tests):
```csharp
var baseline = crafter.Create(Array.Empty<Item>(), null);
var withItem = crafter.Create(new[] { compass }, null);
AssertThat(withItem.Stat).IsGreater(baseline.Stat);
```

## Mock at boundaries only

Mock at **system boundaries**, never at internal collaborators. Applies to every double — mock, stub, spy, hand-rolled fake.

| Mock | Don't mock |
|------|------------|
| External services Jmodot doesn't own | Your own classes, components, States |
| Time-of-day / wall-clock | Anything in the project's own namespaces you control |
| RNG seeds (use `JmoRng` seeding, not a mock) | Anything in `Jmodot.*` you control |
| File system reads (sometimes — prefer fixture files) | `IBlackboard`, `IComponent`, `IAbility`, etc. — use real instances or fixtures |

**Warning sign:** the test breaks when you refactor an internal collaborator though *behavior* is unchanged — you mocked too deep, and the test now pins implementation, not contract.

**The Godot runtime is not a boundary you double.** Engine APIs (nodes, scene tree, physics, `GD.Load`) run for real via `[RequireGodotRuntime]` / `ISceneRunner`; the engine-lifecycle failure class is exactly what a double hides. Where a double is unavoidable, `rules/test_authoring.md` #11 owns its Godot base type.

**Testability of system-boundary code:**
- Inject dependencies (`IRngSource` parameter) rather than `new`-ing externally inside the method.
- Prefer specific operations (one method per external call shape) over generic `Fetch(string endpoint, params...)` interfaces — each becomes independently mockable without conditional logic in the mock setup.
- For Components, lean on `IBlackboard` + fixture-driven setup (`AbilityTestFixture`, `ActivationTestFixture`, `TreeFixture`) rather than mock collaborators.

The Don't Mock column is a boundary constraint, not a cost/benefit tradeoff — setup cost is not a counterweight. Name the fixture and proceed.


## Godot timing gotchas

**A `SetDeferred` result is not ready in the frame you set it.** `SetDeferred` queues the property change for idle-time processing, and `ProcessFrame` can fire before the queue flushes. The sample below shows the flaky and the reliable shape:
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
- An `Initialize()` call using `SetDeferred` (Monitorable, etc.), and a bare `SetDeferred` write, need the node in the scene tree and the channel timing `godot_architecture` §Init-Timing & Data-Source Readiness names.

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

At process exit Godot prints one engine ERROR per leaked Node (`Cannot get path of node...` in the ObjectDB leak dump), so `TestResults/godot_test.log` error counts scale with orphan count, not bug count — and exit code `-1073740791` correlates with accumulation. The same dump prints one `Leaked instance:` line per leaked object, and the engine emits that ERROR text itself, so no code site exists to hunt; `/analyze_godot_logs` parses the log.

```csharp
// 1. Use 'using' with ISceneRunner (auto-cleanup)
using ISceneRunner runner = ISceneRunner.Load("res://scene.tscn");

// 2. Or parent nodes (freed with parent)
parentNode.AddChild(newNode);

// 3. Or manual cleanup in [After]
[After]
public void TearDown() => _node?.QueueFree();
```
