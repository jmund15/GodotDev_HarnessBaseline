---
name: Testing
description: >-
  Auto-load when writing, running, or debugging tests, or doing TDD. Triggers: "test",
  "GdUnit4", "TDD", "write a test", "test fixture", "ISceneRunner", "SpellTestFixture",
  "CastingTestFixture", "orphan", "[TestSuite]", "[RequireGodotRuntime]". SKIP for code
  reviews of test files (use `checklists:test_quality`).
---

# Testing Skill

GdUnit4Net v5.0.0 testing framework for {{PROJECT_NAME}}.

## Quick Start

```bash
# ALWAYS run by category (Windows pipe crashes on full suite)
dotnet test --settings .runsettings --verbosity quiet --filter "FullyQualifiedName~Logic"
dotnet test --settings .runsettings --verbosity quiet --filter "FullyQualifiedName~Integration"
dotnet test --settings .runsettings --verbosity quiet --filter "FullyQualifiedName~Sanity"
```

**Hang-safe runs (Windows) — prefer the wrapper:** `pwsh -NoProfile -File .claude/scripts/run_test_suite.ps1 -Filter "FullyQualifiedName~Tests.<Suite>" -Label <Suite>`. It file-redirects output and tree-kills on a hard wall-clock cap, so a `GodotRuntimeExecutor` wedge can't hang the Bash caller past its own timeout (bare `dotnet test`'s testhost→Godot grandchildren inherit + hold the caller's stdout pipe open → the read never EOFs). Returns `STATUS=DONE`/`STATUS=HANG` + the count line. The bare commands above stay valid and are the **cloud path** (`xvfb-run`, wrapper is Windows-only). `/regression_gate` already uses the wrapper. Root cause + recovery recipe: `archive_gdunit4_process_kill_and_orphans.md` (auto-memory).

**ALWAYS use `--verbosity quiet`** — Without it, the implicit rebuild produces ~50KB of compiler warnings (~337 warnings) that flood the Bash tool output, causing the agent to appear "stuck" processing noise for minutes. All test results (pass/fail counts, error messages) are fully preserved at quiet verbosity.
**NEVER use `--no-build`** — stale DLLs silently mask broken tests after branch switches/merges. `dotnet test` rebuilds automatically.
**Use `--settings .runsettings`** — provides GODOT_BIN fallback, 30min safety timeout, `TreatNoTestsAsError`, and `MaxCpuCount=1`. GODOT_BIN is also set at User env level, but .runsettings is belt-and-suspenders.
**Bash timeout: 600000** — Default 120s kills command but not Godot subprocess, creating orphans that block the named pipe.
**NEVER pipe test output through `| tail` or `| head`** — `tail` buffers the entire stream before outputting, which hangs indefinitely on long-running test processes (especially background tasks). Let full output stream through; use `2>&1` alone.

**Key Rule:** Add `[RequireGodotRuntime]` only for tests using Godot features (GD.Load, Nodes, scenes).

---

## Test Domains

Identify the domain before writing tests. For the **Logic vs Gameplay domain split**, see CLAUDE.md *Development Philosophy: Hybrid TDD*. This skill owns the workflow mechanics.

| Domain | Location | Rule | When |
|--------|----------|------|------|
| **Logic** | `Tests/Logic/` | Strict TDD (RED→GREEN→REFACTOR) | SpellArchitecture, Synergies, Data |
| **Gameplay** | `Tests/Integration/`, `Tests/Sanity/` | Automate deterministic, inspect feel | Wizard, VFX, UI, Physics |

### Logic Domain Flow
```
1. RED    → [TestCase] defines expected behavior
2. VERIFY → Run to confirm failure
3. GREEN  → Minimum code to pass
4. REFACTOR
```

### Gameplay Domain Flow
```
1. IMPLEMENT  → Write Node/_Process logic
2. INSTRUMENT → Add JmoLogger calls to state changes
3. INTEGRATE  → Write ISceneRunner test:
               - SimulateActionPressed/SimulateKeyPress
               - await AwaitInputProcessed() ← CRITICAL!
               - Assert via GetProperty<T>() or FindChild()
4. VERIFY     → Confirm JmoLogger messages in logs
5. HAND-OFF   → Manual playtest for "feel" only
```

**Automate These (ISceneRunner):**
- Input → outcome (press jump → Y increases)
- State transitions (health=0 → death state)
- Signal/event wiring
- Scene structure integrity

**Manual Playtest Only:**
- "Does this feel responsive?"
- Visual polish and juice

**Before deferring a "fingerprint" / playtest list to the user, screen each item** — these lists routinely mix subjective items with automatable ones:
- Button/signal wiring → load the real `.tscn` + `EmitSignal(BaseButton.SignalName.Pressed)` + assert outcome (NOT the `#if TOOLS` direct-call seam — that bypasses `_Ready` wiring).
- Overlay/resource leak on close → act, then assert `GodotObject.IsInstanceValid(node) == false`.
- Focus/selection → `control.HasFocus()` after the entry hook.
- Boot / autoload init / `.tscn`-loads-without-error → Godot MCP `run_project` + `get_debug_output` smoke (catches `.tscn` parse failures + `ValidateRequiredExports` throws that `dotnet build` never sees).

Only genuinely-subjective items remain to defer: visual aesthetics, timing/juice, multi-resolution fill.

---

## Test Architecture Philosophy

### Test Coverage Strategy

**New/complex systems need all three levels:**

| Level | Scope | Example |
|-------|-------|---------|
| **Unit** | Single component | `ReactionMatcher.FindMatch()` returns correct reaction |
| **Integration** | Components together | `ReactionComponent` processes collision and fires handler |
| **E2E** | Full path | Spell collision → reaction triggers → outcome visible |

Unit tests passing ≠ system works. Cross-domain systems especially need E2E coverage.

### Primary Observable Behavior (POB) Rule

Every new system with player-observable behavior MUST include at least one
E2E/integration test asserting the primary observable outcome — the thing
a player would notice if the system broke.

| System | POB Test Assertion |
|--------|--------------------|
| Critter AI | "at least one critter position changes over 2s" |
| Spell casting | "spell instance spawns when cast input simulated" |
| Status effect | "movement speed stat reduced while effect active" |
| Drop system | "ingredient spawns when trigger fires" |

Unit tests at integration boundaries miss engine-lifecycle bugs
(nav map timing, _Ready order, physics frame delivery). The POB
test is the last line of defense.

**Refusal stance:** The POB requirement is non-waivable. When unit-test thoroughness is offered as a substitute ("my unit tests cover everything"), refuse the framing — unit tests at integration boundaries cannot catch the engine-lifecycle failure class POB protects against (`_Ready` order, nav-map timing, signal wiring, physics-frame delivery). The mandate stands regardless of unit-test depth. If you find yourself negotiating the rule on the basis of unit-test coverage — **STOP**. Coverage is not the axis; failure-class is.

### Test Level Philosophy

**Prefer behavioral tests over implementation checks for game mechanics:**
- ❌ `AssertThat(blueprint.ActiveTraits.Count == 0).IsTrue()` — tests check logic
- ✅ `AssertSpellCount(runner, 0)` — tests observable outcome

**Why:** A unit test on internal logic can pass while actual gameplay is broken. Test what the player would observe.

**Anti-pattern: Documentation-only tests**
```csharp
// BAD - this gives false confidence
[TestCase]
public void Test_Feature_Documentation() {
    AssertThat(true).IsTrue();  // NOT A REAL TEST!
}
```

**Anti-pattern: Constant-mirroring tests** — Tests that assert a field equals its default or constant value break when values change intentionally and can never catch a real bug. Test behavioral consequences instead (e.g., "with default config, attraction scoring is disabled" tests the scoring path, not the field). **Refusal stance:** when this anti-pattern is proposed, the action is *remove and replace*, not *augment*. A code comment on the constant may document the value, but does not justify keeping the mirror test alongside a behavioral test. If you find yourself offering the comment as a sidecar to the bad test — **STOP**. Remove the mirror test outright.

**Anti-pattern: Rationalizing strict TDD away on integration regressions** — When the change being made IS the prevention of a memorialized integration regression class (hot-loop, restart-loop, process-ordering race, BB-flag-soup, perception-staleness), an integration test exercising the symptom is mandatory regardless of how the diff splits across `.cs`/`.tscn` boundaries or how trivial the C# looks. Domain classification (Logic vs Gameplay) does NOT excuse skipping when the bug class is itself an integration phenomenon. Hot-loop and ordering bugs live at the SEAM between layers (BT+BTState, BehaviorTree+RestartPolicy switch, HSM+child-state lifecycle, Pool+Spawn callback) — test the seam, not just the leaves. Logic Domain tests CAN exercise seams when the participants are framework primitives instantiable in code. Litmus: "If this change accidentally re-introduced the bug it claims to fix, would my test suite catch it?" If the answer is "manual playtest," write the seam test first. See the Wave-2 hot-loop case study (Cross-references below); `Tests/Logic/AI/BehaviorTreeRestartPolicyTests.cs` for the BT+BTState seam test template. **Refusal stance:** When a user argues "this file is Logic Domain therefore a unit test suffices," reclassify the *bug*, not the *file*. The domain of the modified `.cs` file is irrelevant — what matters is the domain of the bug class being prevented. A hot-loop bug, a process-ordering race, or a BB-flag-soup bug is a Seam bug regardless of which file the fix touches. If you find yourself accepting "Logic Domain file → unit test sufficient" as the operative axis — **STOP**. The axis is the bug class, not the file path.

### Measurement Harnesses (generative domains)

For generative/distributional domains (procgen, spawning, loot, crafting outcomes), prefer ONE corpus-style measurement harness over accumulating example pins: N seeds x M representative profiles through the real entry point, asserting success-rate / distribution / ceiling thresholds **pinned from the first observed run with headroom**, with a typed-cause histogram composed into the failure message so a regression names its dominant cause. Asserts the property gameplay depends on AND doubles as tuning telemetry. Template: a corpus test under `Tests/Logic/<GenerativeDomain>/`.

### Test Hygiene
- Never leave tests in a broken state, even if unrelated to current work
- Failing tests obscure whether new changes introduced regressions
- Debug timeouts immediately - in Logic domain, timeout often means infinite loop, not slow execution

### Pre-Commit Regression Gate

Use `/regression_gate` before committing code changes. See the command for full procedure.

**Key rules (why this exists):**
- Tests must be run AFTER the final staged state, not from a cached previous run
- ALL 3 suites (Logic, Integration, Sanity) must pass — running just one domain is insufficient
- Windows pipe crashes can silently drop tests — always use `--filter` batches, never bare `dotnet test`
- Exempt: pure meta commits (`.claude/`, skills, docs) that don't touch code

### Modular Test Modules (Create On-Demand)

Build reusable test modules for gameplay systems when a pattern emerges:

**Trigger:** Create module on the SECOND test for a system.
```
First test for HSM  → Inline setup (quick, specific)
Second test for HSM → Extract into reusable module
```

**Existing Modules:** `SpellTestFixture`, `CastingTestFixture`, `GameplayScenarioBuilder`, `SpellAssertions`, `BehaviorTreeTestFixture`

**Candidate Modules (create when needed):**
| System | Module | Purpose |
|--------|--------|---------|
| HSM | `HSMTestRunner` | Simulate state transitions |
| Movement | `MovementTestHarness` | Spawn, apply forces, assert positions |
| Combat | `CombatLogAssertions` | Assert combat events logged |

### Test Retention Policy

**Rule:** Keep all behavior tests. Manage execution time with filtering, not deletion.

**Delete only when:** Feature removed, test is redundant, or test checks implementation details (not behavior).

### Mock at Boundaries Only

**Rule:** Mock at **system boundaries**, never at internal collaborators.

| Mock | Don't mock |
|------|------------|
| External services Jmodot doesn't own | Your own classes, components, States |
| Time-of-day / wall-clock | Anything in `{{PROJECT_NAME}}.*` you control |
| RNG seeds (use `JmoRng` seeding, not a mock) | Anything in `Jmodot.*` you control |
| File system reads (sometimes — prefer fixture files) | `IBlackboard`, `IComponent`, `ISpell`, etc. — use real instances or fixtures |

**The warning sign:** your test breaks when you refactor an internal collaborator but the *behavior* hasn't changed. That's the signal you mocked too deep — the test now tests *implementation*, not *contract*.

**For testability of system-boundary code:**
- Prefer dependency injection (`IRngSource` parameter) over `new`-ing externally inside the method.
- Prefer specific operations (one method per external call shape) over generic `Fetch(string endpoint, params...)` interfaces — each operation becomes independently mockable without conditional logic in the mock setup.
- For Components, lean on `IBlackboard` + fixture-driven setup (see `SpellTestFixture`, `CastingTestFixture`, `BehaviorTreeTestFixture`) rather than mock collaborators.

**Refusal stance:** The Don't Mock list is a boundary constraint, not a cost/benefit tradeoff. When a user argues "mocking X is simpler" for any item in the Don't Mock column, do not acknowledge setup cost as a counterweight — the cost-benefit weighing already happened when the rule was established. If you find yourself writing "the setup cost is real, but..." — **STOP**. Direct the user to the appropriate fixture (`SpellTestFixture`, `CastingTestFixture`, `BehaviorTreeTestFixture`) and proceed.

**Reference:** `archive_testing_design_patterns.md` (auto-memory) for additional fixture-vs-mock tradeoffs in PP-specific contexts (e.g. when to substitute a real `Blackboard` instance vs. a fake).

---

## Logic Domain — Red Flags & Rationalizations

> **Scope reminder:** This section applies ONLY to the Logic Domain
> (`SpellArchitecture`, `Synergies`, `Jmodot.Core`, `Inventory`, `Math/Parsing`,
> `Data Structures`). The Gameplay Domain has its own evaluation rubric — see
> the Gameplay Domain Flow above and CLAUDE.md *Development Philosophy: Hybrid TDD* (which owns the domain split).

### The Iron Law (Logic Domain only)

```
NO PRODUCTION LOGIC-DOMAIN CODE WITHOUT A FAILING TEST FIRST
```

If you wrote production logic-domain code before the test, **delete it and start over** — don't "adapt" it while writing tests. You will rationalize back into the existing implementation, and that's not TDD. The Gameplay Domain has different rules; this Iron Law fires only inside Logic Domain scope.

**Retroactive trigger:** If the user's phrasing implies code already exists ("let me finish this," "get this working first," "I've already spent X hours") — **STOP**. The Iron Law fires retroactively. Delete the implementation before any test work begins; do not adapt tests to existing code.

### Rationalizations to refuse

**Refusal stance for every row below:** Do not justify the rule with technical reasoning when the user pushes back. Do not frame Logic-Domain TDD as a tradeoff to weigh. State the rule as non-negotiable, then prescribe the action. If you find yourself writing "the reason this is better is..." — **STOP**. The rule is the rule; the explanation is for *you*, not for the user mid-pushback.

When you catch yourself thinking any of these, **stop, delete, and restart with a failing test**:

| Excuse | Reality |
|---|---|
| "Too simple to test" | Simple code breaks. The test takes 30 seconds. |
| "I'll test after" | Tests written after pass immediately — that proves nothing. If code already exists, delete it before any test work; do not adapt. |
| "Already manually tested" | Ad-hoc ≠ systematic. No record, can't re-run on the next change. |
| "Deleting X hours of work is wasteful" | Sunk cost — non-negotiable, not a tradeoff. Keeping unverified code is technical debt; the hours are spent regardless of whether the code stays. |
| "Keep as reference, write tests first" | You'll adapt it. That's testing-after with extra steps. |
| "Tests after achieve the same goals" | The end state is *not* the same. Tests-after document existing behavior including bugs; tests-first specify intended behavior. A passing test suite of the first kind is descriptive, not specificational. |
| "TDD will slow me down" | TDD is faster than debugging. The shortcut is the long way around. |
| "Existing code has no tests" | You're changing it — tests for the logic you're modifying ship in the same change, not in a future coverage sweep. Deferral is not an option. |
| "It's a small refactor" | If logic changes, behavior changes. Test the change. |

**Coverage deferral is not an option:** When you encounter untested code in the course of a change, lifting coverage for the logic you're modifying is part of the current task — not a follow-up item. If you find yourself writing "I'll do a coverage sweep later" or "I'll add tests in the next PR" — **STOP**. Tests for what you're changing ship with what you're changing. Deferred sweeps don't happen; the debt compounds.

### Stop signals

If the test feels hard to write, **listen to the test**: hard-to-test usually means hard-to-use, which usually means the design needs simplification. Don't power through — let the test drive the design.

### Cross-references

- `feedback_strict_tdd_for_integration_regressions.md` — even when domain classification says "Gameplay," if the bug class IS the integration (hot-loop, race, BB-flag-soup, perception-staleness), write the seam-level integration test BEFORE shipping.
- Wave-2 hot-loop case study — domain misclassification let an integration regression slip past Logic-Domain TDD (a 3-line revert passed the full Logic suite but froze the game on grunt spawn). The old `TDD_Category_Error_Wave2_Hot_Loop` memory entity was retired; this line and the `debugging` skill Phase 5 are the surviving record.
- The Anti-pattern subsections above (Documentation-only tests, Constant-mirroring tests, Rationalizing strict TDD away on integration regressions) — sibling content describing how Logic-Domain tests fail in practice once written.

---

## GdUnit4 v5.0.0 Essentials

### Key Change
Tests run **WITHOUT Godot runtime by default** (10x faster). Add `[RequireGodotRuntime]` only when needed.

### Attributes

| Attribute | Use For |
|-----------|---------|
| `[TestSuite]` | Mark test class |
| `[TestCase]` | Mark test method (default timeout: 5 min) |
| `[TestCase(Timeout = 10000)]` | Custom timeout in ms |
| `[TestCase(DoSkip = true, SkipReason = "...")]` | Skip test with reason |
| `[RequireGodotRuntime]` | GD.Load, Nodes, scenes, Vector3 |
| `[Before]`/`[After]` | Suite-level setup/teardown (once) |
| `[BeforeTest]`/`[AfterTest]` | Per-test setup/teardown (each test) |
| `[DataPoint(nameof(X))]` | Parameterized tests (C# only)

### Assertions
```csharp
AssertThat(actual).IsEqual(expected);
AssertThat(actual).IsNotNull();
AssertThat(list).Contains(item);
// Note: No IsGreaterOrEqual() - use AssertThat(x >= y).IsTrue()
```
**Pin exact values for deterministic pure functions.** When inputs are known constants and the function is pure (no randomness/state), assert the exact computed result (`IsEqual(15.5f)`) rather than a range (`IsGreater(0f)`). Weak assertions mask constant drift silently.

### Testing Node Subclasses Directly
When using `new NodeType()` in tests, `_Ready()` is **NOT called** (node never enters scene tree).

**For testability:**
- Initialize fields at **declaration time** when possible, not in `_Ready()`
- Use null-conditional for singletons: `EventBus.Instance?.Method()`
- Use null-coalescing for owner refs: `_owner?.Name ?? "Unknown"`
- Orphan warnings are expected and don't affect test correctness

---

## Testing Framework

Located in `Tests/Framework/`. Provides fixtures for spell testing.

### Fixtures

**GameplayTestFixture** (base):
- `LoadIngredient("Apple")` / `LoadArchetype("Watergun")`
- `CraftFromIngredients(...)` / `CraftFromIngredientNames(...)`

**SpellTestFixture** (extends above):
- `Crafter` property (fresh per test)
- `HasEffect<T>()`, `GetEffects<T>()`, `GetCollisionSystemType()`

**CastingTestFixture** (E2E spell tests):
- `LoadCasterScene()` / `GetCaster(runner)` - Load scene with SpellCasterService
- `LoadArchetype("Fireball")` / `CreateTestBlueprint(archetype, ...effects)` - Create test spells
- `CountSpawnedSpells(runner)` / `GetSpawnedSpells(runner)` - Count/get active spells
- **Pool Isolation:** Call `SpellPoolManager.Instance?.ClearAllPools()` in `[BeforeTest]`
- **SetExportProperty helper:** Prefer `#if TOOLS` test helpers (see Architecture Philosophy SKILL). Use reflection only for third-party types you can't modify.

**E2E Timing Constants:**
| Wait | Purpose |
|------|---------|
| `100ms` | Charge/initialization, physics server registration after AddChild |
| `200ms` | Collision processing (Area3D overlap) |
| `400ms` | Pool return completion |
| `500-800ms` | SpellSpawner spawn-count assertions (heavy synchronous per-spawn work) |

**SpellSpawner timing caveat:** `SpawnChild()` performs heavy synchronous work per spawn (scene instantiation, visual loading, collision shape adoption, combat wiring). This eats frame budget — use `Duration >= 0.3s` and `SpawnInterval <= 0.05s` with generous `AwaitMillis` (500-800ms) for spawn-count-dependent assertions.

### Production Resource Coupling

Tests loading production `.tres`/`.tscn` files from outside `Tests/` are fragile to designer rebalancing.

**Preferred pattern:** Use frozen test data in `Tests/Fixtures/Data/` with known stat values.

**When production resources are unavoidable** (integration/smoke tests), classify assertions:

| Fragility | Example | Action |
|-----------|---------|--------|
| **FRAGILE (value)** | `DamageMultiplier == 0.5f` | Replace with `> 0f` or baseline comparison |
| **FRAGILE (config guard)** | `DoNotInherit == true` | Keep — safety net for game-breaking bugs |
| **Structural** | `IsInstanceOf<CompositeOutcome>()` | Acceptable for integration smoke tests |

**Baseline comparison pattern** (for modifier tests):
```csharp
var baseline = crafter.Create(Array.Empty<Ingredient>(), null);
var withIngredient = crafter.Create(new[] { compass }, null);
AssertThat(withIngredient.Stat).IsGreater(baseline.Stat);
```

### Seam-Injected Dependencies Need One Real-Scene Test

When a node's production dependencies are wired via scene/Inspector (`[Export]` node refs, autoload children) but tests supply them through a `#if TOOLS SetXForTesting` seam, **at least one test must load the real production scene** (`ResourceLoader.Load<PackedScene>(...).Instantiate<T>()` + `AddChild`). A suite that *only* injects via the seam never exercises the production wiring — so a missing scene (e.g. a script-only autoload that can't satisfy `[RequiredExport]` node refs) passes every test while being null at runtime. Assert by behavior (the dependency actually does its job), not that the field is set. Sibling: the Godot-wiring half of this is `archive_godot_node_init_timing.md` (auto-memory).

### Godot Timing Gotchas for Tests

**SetDeferred + ProcessFrame is non-deterministic with 1 frame:**
`SetDeferred` queues property changes for idle-time processing, but `ProcessFrame` can fire BEFORE the deferred queue is fully flushed. **Always await 2 frames** when asserting `SetDeferred` results:
```csharp
shape.SetDeferred(CollisionShape3D.PropertyName.Disabled, true);
// BAD: flaky, especially with nested nodes
await tree.ToSignal(tree, SceneTree.SignalName.ProcessFrame);
// GOOD: reliable at all nesting depths
await tree.ToSignal(tree, SceneTree.SignalName.ProcessFrame);
await tree.ToSignal(tree, SceneTree.SignalName.ProcessFrame);
```

**Programmatic nodes need explicit setup:**
- `AddChild()` does NOT set `Owner` — set `hurtbox.Owner = target` explicitly
- `Initialize()` calls using `SetDeferred` (Monitorable, etc.) need scene tree + 2 frames
- Wait 100ms after `AddChild(target)` for physics server to register Area3D nodes
- `SetDeferred` properties don't take effect on nodes not in the scene tree

**Float accumulation in duration tests:**
When testing time-based BT actions or timers, avoid accumulating small deltas (e.g., `60 × 1f/60f ≠ 1.0f` due to IEEE 754). Use single large delta values:
```csharp
// BAD — accumulated error means elapsed never precisely hits threshold
for (int i = 0; i < 60; i++) action.ProcessPhysics(1f / 60f);
// GOOD — single delta reliably crosses the threshold
action.ProcessPhysics(duration + 0.1f);
```

**BB Subscribe callbacks receive boxed Variant, not raw types:**
`Blackboard.Subscribe` fires `Action<object>` where the value is a `Variant` boxed as `object`. Pattern matching like `value is true` **silently fails** (the boxed object is a Variant struct, not a bool). This affects ALL BB subscription handlers:
```csharp
// BAD — silently never matches
bool flag = value is true;
// GOOD — unwrap the Variant first
bool flag = value is Variant v && v.AsBool();
```

### Usage
```csharp
[TestSuite]
public partial class MyTests : SpellTestFixture
{
    [TestCase, RequireGodotRuntime]
    public void Test_Spell_Has_Effect()
    {
        var blueprint = CraftFromIngredientNames("Apple");
        AssertThat(HasEffect<SomeEffect>(blueprint)).IsTrue();
    }
}
```

---

## Orphan Node Prevention

Orphans cause memory leaks and crashes. Prevent with:

```csharp
// 1. Use 'using' with ISceneRunner (auto-cleanup)
using ISceneRunner runner = ISceneRunner.Load("res://scene.tscn");

// 2. Or parent nodes (freed with parent)
parentNode.AddChild(newNode);

// 3. Or manual cleanup in [After]
[After]
public void TearDown() => _node?.QueueFree();
```

---

## Exit Codes & Troubleshooting

| Code | Meaning | Action |
|------|---------|--------|
| `0` | Pass | ✓ |
| `100` | Failures OR executor timeout | Check test count - may be cosmetic |
| `101` | Warnings | Review orphan warnings |
| `-1073740791` | Godot crash (orphan accumulation) | Run in batches |

### Full Suite Crashes?
ALWAYS run in batches (see Quick Start). Never run the full suite unfiltered.

### "GodotRuntimeExecutor timed out"
**CRITICAL: This is a SILENT TEST SKIP.** When this message appears, ALL `[RequireGodotRuntime]` tests are reported as "Passed" but **never actually ran**. The regression gate is INVALID.
- Logic domain: ~388 pass WITHOUT runtime. The current committed baseline for WITH-runtime runs lives in `Tests/regression_baseline.json` and auto-updates on green runs via `/regression_gate`. The ~388 sentinel is architecturally stable and does NOT drift with test growth — if you see ~388, runtime tests were silently skipped.
- **Pre-test checklist:** Kill orphaned Godot processes BEFORE running (editor-safe):
  ```bash
  powershell.exe -Command "Get-Process -Name 'Godot*' | Where-Object { \$_.MainWindowTitle -notlike '*Godot Engine*' } | Stop-Process -Force -ErrorAction SilentlyContinue"
  ```
  This kills headless test runners (empty MainWindowTitle) while preserving the user's editor (title contains "Godot Engine").
- **Post-test validation:** Scan output for `GodotRuntimeExecutor failed` or `Connection timeout`. If present, results are invalid — fix and re-run.
1. Check for orphaned Godot processes (editor-safe — see pre-test checklist above)
2. Verify `GODOT_BIN` is set: `setx GODOT_BIN "C:\path\to\godot.exe"` (User env var) or pass `--settings .runsettings`
3. If test count is low (~388 for Logic), GODOT_BIN isn't reaching the test adapter — check both the env var and .runsettings. The ~388 signature is the non-runtime test count and is architecturally stable; the current WITH-runtime baseline lives in `Tests/regression_baseline.json` and is maintained automatically by `/regression_gate`.

### More Gotchas
Search auto-memory (semantic-search) for "GdUnit4" or "Godot C# test gotchas" for additional troubleshooting.

---

## See Also

| Reference | Contents |
|-----------|----------|
| [scene_runner.md](scene_runner.md) | ISceneRunner API: accessors, input simulation, frame control |
| [advanced.md](advanced.md) | Lifecycle hooks, parameterized tests, utilities, FAQ |
