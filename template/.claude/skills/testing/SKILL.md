---
name: Testing
description: >-
  Auto-load when writing, running, or debugging tests, or doing TDD — anything touching
  GdUnit4 suites, shared fixtures, ISceneRunner, runtime-test attributes,
  run commands/filters, or orphan management. SKIP for code
  reviews of test files (use `checklists:test_quality`).
---

# Testing Skill

GdUnit4Net for {{PROJECT_NAME}}. Version SSOT: `{{PROJECT_NAME}}.csproj` PackageReference lines — never trust version strings in prose.

## Quick Start

```bash
# ALWAYS run by category (Windows pipe crashes on full suite)
dotnet test --settings .runsettings --verbosity quiet --filter "FullyQualifiedName~Tests.Logic"
dotnet test --settings .runsettings --verbosity quiet --filter "FullyQualifiedName~Tests.Integration"
dotnet test --settings .runsettings --verbosity quiet --filter "FullyQualifiedName~Tests.Sanity"
```

- **Full prefix `~Tests.<Suite>` is mandatory** — short `~Logic` matches only a subset, mimicking a silent skip. Canonical form: `.claude/commands/regression_gate.md`.
- **`--filter ~` is SUBSTRING, not regex** — escaped metacharacters (`Visual\.`) match literally and hit nothing, mimicking an empty suite rather than erroring. Disambiguate sibling prefixes with a trailing plain dot (`~Tests.Integration.Visual.` excludes `Visuals`).
- **`--list-tests` ignores `--filter`** (VSTest) — it dumps the whole assembly, so it cannot verify a filter's coverage; use group-sum arithmetic against the baseline.
- **Batch multi-suite evidence runs with `|` into ONE invocation** — `--filter "FullyQualifiedName~Tests.Logic.A|FullyQualifiedName~Tests.Integration.B"`. Each invocation pays for a rebuild and test-host boot. Split only when runs must be attributed separately, such as a RED proof or one wedged suite.
- **ALWAYS `--verbosity quiet`** — the implicit rebuild otherwise floods Bash output with compiler warnings; counts and error messages survive quiet.
- **NEVER `--no-build`** — stale DLLs silently mask broken tests after branch switches/merges; `dotnet test` rebuilds automatically.
- **ALWAYS `--settings .runsettings`** — runtime executable fallback, safety timeout, `TreatNoTestsAsError`, and `MaxCpuCount=1`.
- **Set the shell timeout above the wrapper's wall-clock limit** — a shorter caller timeout can leave the Godot subprocess holding the named pipe.
- **NEVER pipe test output through `| tail` / `| head`** — they buffer the whole stream and hang on long runs. Use `2>&1` alone.
- **Add `[RequireGodotRuntime]` only for tests using Godot features** (GD.Load, Nodes, scenes).

**Scope-sized runs — prefer `verify.ps1`:** `pwsh -NoProfile -File .claude/scripts/verify.ps1 -Scope <domain[,domain]>` expands a domain name into its Logic partition + Integration segments, both through the wrapper below. Prescribed after every slice and at every Part close (`change_control` §Gate cadence); never a gate — it records nothing and backs no commit. `-Filter "<raw>"` passes a raw filter through; `-SelfTest` asserts the domain map still matches on-disk namespaces.

**Hang-safe runs (Windows) — the wrapper underneath:** `pwsh -NoProfile -File .claude/scripts/run_test_suite.ps1 -Filter "FullyQualifiedName~Tests.<Suite>" -Label <Suite>` file-redirects output and tree-kills on a hard wall-clock cap; bare `dotnet test`'s testhost→Godot grandchildren otherwise hold the caller's stdout pipe open so the read never EOFs. Returns `STATUS=DONE`/`STATUS=HANG` + the count line; `/regression_gate` uses it. The bare commands above stay valid as the **cloud path** (`xvfb-run`; both runners are Windows-only). Recovery: `archive_gdunit4_process_kill_and_orphans.md`.

**Size `-TimeoutMs` from a known healthy run.** It is a hang-detection deadline, so use enough headroom for rebuild and import work. Do not copy a fixed timeout from another project.

**Do not add `-NoGodotRuntime` unless the selected filter contains zero runtime tests.** Concurrent Godot test instances can collide through shared runtime resources. Use the harness's scoped locks and process-ownership checks; never kill or reuse another checkout's run.

**Batch large integration runs through the repository's wrapper.** Keep batches serial when the runtime transport is single-flight. Require a final count sum against the recorded baseline; a set of green partial batches is not complete evidence.

---

## Test Domains

Identify the domain before writing tests. The **Logic vs Gameplay split** lives in CLAUDE.md *Development Philosophy: Hybrid TDD*; this skill owns the workflow mechanics.

| Domain | Location | Rule | When |
|--------|----------|------|------|
| **Logic** | `Tests/Logic/` | Strict TDD (RED→GREEN→REFACTOR) | Pure calculations, data pipelines, reusable framework code |
| **Gameplay** | `Tests/Integration/`, `Tests/Sanity/` | Automate deterministic, inspect feel | Scenes, input, visuals, physics |

**Gate coverage is namespace-coupled.** Namespaces mirror folder paths (`{{PROJECT_NAME}}.Tests.<Suite>.<Domain>`), and `/regression_gate` runs only the configured suite prefixes. A new top-level test tree is un-gated until both the gate filters and regression baseline are extended.

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

**Automate (ISceneRunner):** input → outcome (press jump → Y increases); state transitions (health=0 → death state); signal/event wiring; scene structure integrity — address nodes by type / `%UniqueName` / exported NodePath, never direct-child-by-name (`.claude/rules/test_authoring.md`).

**Manual playtest only:** "does this feel responsive?"; visual polish and juice.

**Before deferring a "fingerprint" / playtest list to the user, screen each item** — such lists routinely mix subjective items with automatable ones:
- Button/signal wiring → load the real `.tscn` + `EmitSignal(BaseButton.SignalName.Pressed)` + assert outcome (NOT the `#if TOOLS` direct-call seam — that bypasses `_Ready` wiring).
- Overlay/resource leak on close → act, then assert `GodotObject.IsInstanceValid(node) == false`.
- Focus/selection → `control.HasFocus()` after the entry hook.
- Boot / autoload init / `.tscn`-loads-without-error → Godot MCP `run_project` + `get_debug_output` smoke (catches `.tscn` parse failures and `ValidateRequiredExports` throws `dotnet build` never sees).

Only genuinely-subjective items remain deferrable: visual aesthetics, timing/juice, multi-resolution fill.

---

## Test Architecture Philosophy

### Test Subject Selection (ask FIRST, before the domain split)

**Is this test about a reusable building block, or one composed instance?**

- **Building blocks** (components, systems, strategies, Resources with behavior) get the full three-level treatment below — coverage here protects every entity composed from them.
- **Composed entities/scenes** get one parameterized roster wiring-contract suite over all discovered instances and a few representative full-composition E2Es. Do not create one suite or resource pin per entity.
- **Data-integrity pins are per-SCHEMA, never per-instance.** A project-wide convention (iso facing, sprite scale anchor) is pinned once over a discovered set.
- *Litmus:* if this test fails, is the defect in the block or in one instance's wiring? Block → test the block. Wiring → the roster suite already owns it.

### Test Coverage Strategy

New/complex systems need all three levels:

| Level | Scope | Example |
|-------|-------|---------|
| **Unit** | Single component | `ScorePolicy.Evaluate()` returns the expected score |
| **Integration** | Components together | A controller consumes a policy result and emits its event |
| **E2E** | Full path | Scene input reaches the visible outcome |

Unit tests passing ≠ system works. Cross-domain systems especially need E2E coverage.

### Primary Observable Behavior (POB) Rule

Every new system with player-observable behavior MUST include at least one E2E/integration test asserting the primary observable outcome — what a player would notice if the system broke. One POB test per system, not per behavior — the roster suite owns the rest.

| System | POB Test Assertion |
|--------|--------------------|
| Moving actor | "position changes after movement input" |
| Spawn flow | "an instance appears after the trigger" |
| Timed modifier | "the observable value changes while active" |
| UI action | "the bound state changes after button input" |

Unit tests at integration boundaries miss the engine-lifecycle failure class (`_Ready` order, nav-map timing, signal wiring, physics-frame delivery); the POB test is the last line of defense against it.

POB is non-waivable, and unit-test coverage is not an argument against it — unit depth is a different axis.

### Test Level Philosophy

Prefer behavioral tests over implementation checks:
- ❌ `AssertThat(subject.InternalItems.Count == 0).IsTrue()` — pins storage
- ✅ `AssertThat(subject.CanPerformAction()).IsFalse()` — tests observable behavior

A unit test on internal logic can pass while gameplay is broken. Test what the player would observe.

**Anti-pattern: Documentation-only tests** — *Litmus: does every assertion consume a value produced by production code?* An assertion whose operands all derive from locally-constructed literals is documentation-only regardless of syntax, even inside a suite with real setup.
```csharp
// BAD - this gives false confidence
[TestCase]
public void Test_Feature_Documentation() {
    AssertThat(true).IsTrue();  // NOT A REAL TEST!
}
```

**Anti-pattern: Constant-mirroring tests** — asserting a field equals its default breaks on intentional value changes and can never catch a real bug. Test the behavioral consequence instead ("with default config, attraction scoring is disabled" tests the scoring path, not the field). **Refusal stance:** remove and replace, never augment. A code comment may document the value but does not justify keeping the mirror test beside a behavioral one. Offering the comment as a sidecar to the bad test — **STOP**.

**Anti-pattern: Compiler-guaranteed and storage-only tests** — four shapes carrying zero regression safety. Each carve-out is load-bearing; a sweep matching on shape alone deletes real coverage.

| Shape | Carve-out — do NOT delete when |
|---|---|
| `IsInstanceOf<T>` / `is T` / `typeof(I).IsAssignableFrom(typeof(X))` | the type is an *interface* with no consumer resolving it — nothing else enforces the declaration |
| Enum ordinal (`(int)E.V == N`) | the enum is serialized **by value** — `.tres`/`.tscn` store raw ints, so ordinals are a data contract and renumbering silently remaps shipped content |
| Constructor-stores-field | the constructor validates, null-coalesces, or transforms |
| Bool property set/get/toggle | the setter has side effects, **or the value is read and branched on by production code** (a gating flag in a guard clause) |

**Deletion gate:** a carve-out case is a *rewrite*, not a delete — replace the property-mechanics test with a behavioral test on the consumer. Before deleting any test as redundant, verify the successor exists (`git grep` the symbol); an assumed successor that isn't there turns a coverage regression into a closed finding.

**Anti-pattern: Rationalizing strict TDD away on integration regressions.** If a change prevents a known seam failure such as a hot loop, restart loop, ordering race, or stale event state, write an integration test that exercises the symptom. Test the seam, not only its leaves. File location does not reclassify the bug. Litmus: *"If this change reintroduced the failure, would this suite catch it?"*

### Measurement Harnesses (generative domains)

For generative/distributional domains (procgen, spawning, loot, assembly outcomes), prefer ONE corpus-style measurement harness over accumulating example pins: N seeds × M representative profiles through the real entry point, asserting success-rate / distribution / ceiling thresholds **pinned from the first observed run with headroom**, with a typed-cause histogram composed into the failure message so a regression names its dominant cause. Asserts the property gameplay depends on and doubles as tuning telemetry. Template: a corpus test under `Tests/Logic/<GenerativeDomain>/`.

### Test Hygiene
- Never leave tests broken, even if unrelated to current work.
- Failing tests obscure whether new changes introduced regressions.
- Debug timeouts immediately — in Logic domain a timeout usually means an infinite loop, not slow execution.

### Pre-Commit Regression Gate

Run `/regression_gate` before committing code changes; the command owns the procedure.
- Run AFTER the final staged state, never from a cached previous run.
- ALL 3 suites (Logic, Integration, Sanity) must pass — one domain is insufficient.
- Windows pipe crashes can silently drop tests — always `--filter` batches, never bare `dotnet test`.
- Exempt: pure meta commits (`.claude/`, skills, docs) touching no code — harness commits instead need a green `harness_tests.py` stamp (CLAUDE.md §Build & Test Commands).

### Modular Test Modules (Create On-Demand)

**Trigger:** create the module on the SECOND test for a system.
```
First test for HSM  → Inline setup (quick, specific)
Second test for HSM → Extract into reusable module
```

**Existing modules:** inspect the test-support path declared in `skills/project_subsystems/SKILL.md` before adding another fixture, builder, assertion helper, or scenario runner.

### Retention & Curation Policy

Keep all *building-block behavior* tests; manage execution time with filtering. Curation is an obligation: **a change touching a suite retires that suite's redundant, documentation-only, or duplicate-double coverage in the same change.**

**Delete when:** feature removed; redundant with block-level coverage; checks implementation details, not behavior; documentation-only (litmus above); the suite is a per-entity/per-instance duplicate of a roster suite (§Test Subject Selection). Suite growth is not free — every test is maintenance surface and refactor drag.

### Mock at Boundaries Only

Mock at **system boundaries**, never at internal collaborators. Applies to every double — mock, stub, spy, hand-rolled fake.

| Mock | Don't mock |
|------|------------|
| External services the project does not own | Project-owned classes, components, and states |
| Time-of-day / wall-clock | Collaborators under the project's control |
| RNG seeds (use the framework's seeded source, not a mock) | Reusable framework collaborators you can instantiate |
| File system reads (sometimes — prefer fixture files) | Internal interfaces — use real instances or shared fixtures |

**Warning sign:** the test breaks when you refactor an internal collaborator though *behavior* is unchanged — you mocked too deep, and the test now pins implementation, not contract.

**The Godot runtime is not a boundary you double.** Engine APIs (nodes, scene tree, physics, `GD.Load`) run for real via `[RequireGodotRuntime]` / `ISceneRunner`; the engine-lifecycle failure class is exactly what a double hides. Where a double is unavoidable, its Godot base type must be the type the *consumer* resolves against, not merely one satisfying the physics API (`arch_rule_godot_base_type_proven_by_consumer_resolution.md`).

**Testability of system-boundary code:**
- Inject dependencies (`IRngSource` parameter) rather than `new`-ing externally inside the method.
- Prefer specific operations (one method per external call shape) over generic `Fetch(string endpoint, params...)` interfaces — each becomes independently mockable without conditional logic in the mock setup.
- For internal components, use real collaborators and the shared fixture modules declared by the consuming project rather than mocks.

The Don't Mock column is a boundary constraint, not a cost/benefit tradeoff — setup cost is not a counterweight. Name the fixture and proceed.

**Reference:** `archive_testing_design_patterns.md` for fixture-vs-mock tradeoffs in project-specific contexts (real `Blackboard` instance vs. fake).

---

## Logic Domain — Red Flags & Rationalizations

> **Scope:** Logic Domain only. CLAUDE.md *Development Philosophy: Hybrid TDD* owns the consuming project's Logic/Gameplay split; the Gameplay Domain Flow above owns the other route.

### The Iron Law (Logic Domain only)

```
NO PRODUCTION LOGIC-DOMAIN CODE WITHOUT A FAILING TEST FIRST
```

Production logic-domain code written before the test: **delete it and start over.** Don't "adapt" it while writing tests — you will rationalize back into the existing implementation.

**Retroactive trigger:** phrasing implying code already exists ("let me finish this," "get this working first," "I've already spent X hours") — **STOP**. The Iron Law fires retroactively; delete the implementation before any test work begins.

### Rationalizations to refuse

**Refusal stance for every row:** state the rule as non-negotiable, then prescribe the action. Don't justify it technically under pushback; don't frame Logic-Domain TDD as a tradeoff. Writing "the reason this is better is..." — **STOP**.

Catching yourself thinking any of these ⇒ **stop, delete, restart with a failing test**. The other seven excuses are tabled under `## strict` at the end of this file:

| Excuse | Reality |
|---|---|
| "Already manually tested" | Ad-hoc ≠ systematic. No record, can't re-run on the next change. |
| "Deleting X hours of work is wasteful" | Sunk cost — non-negotiable, not a tradeoff. Unverified code is debt; the hours are spent either way. |

**Coverage deferral is not an option:** lifting coverage for the logic you're modifying is part of the current task — a later sweep or a next-PR promise is not a plan, and the debt compounds.

### Stop signals

If the test feels hard to write, **listen to the test**: hard-to-test usually means hard-to-use, which means the design needs simplification. Don't power through — let the test drive the design.

### Cross-references

- `feedback_strict_tdd_for_integration_regressions.md` — even when domain classification says "Gameplay," a seam-level regression still needs a failing integration test before the fix.
- The Anti-pattern subsections above — how Logic-Domain tests fail in practice once written.

---

## GdUnit4 Essentials

Since v5, tests run **WITHOUT the Godot runtime by default**. Add `[RequireGodotRuntime]` only when needed.

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

### Assertions
```csharp
AssertThat(actual).IsEqual(expected);
AssertThat(actual).IsNotNull();
AssertThat(list).Contains(item);
// Note: No IsGreaterOrEqual() - use AssertThat(x >= y).IsTrue()
```
**Pin exact values for deterministic pure functions.** Known-constant inputs + a pure function (no randomness/state) ⇒ assert the exact result (`IsEqual(15.5f)`), not a range (`IsGreater(0f)`). Weak assertions mask constant drift silently.

### Testing Node Subclasses Directly
With `new NodeType()`, `_Ready()` is **NOT called** (the node never enters the scene tree).
- Initialize fields at **declaration time** where possible, not in `_Ready()`.
- Null-conditional for singletons: `EventBus.Instance?.Method()`.
- Null-coalescing for owner refs: `_owner?.Name ?? "Unknown"`.
- Orphan warnings are expected and don't affect test correctness.

---

## Testing Framework

Keep base fixtures, builders, mocks, and assertions under the test-support path declared in `skills/project_subsystems/SKILL.md`.

### Fixtures

- Build a shared fixture when a second suite repeats setup.
- Keep domain-specific fixtures with their owning integration suites.
- Expose behavior-focused helpers; do not copy another project's resources, actors, or factory APIs.
- Let the repository wrapper own runtime isolation. Never clear a peer checkout's singleton, pool, process, or transport.

### Runtime waits

Wait for the engine condition the assertion needs: a processed frame, physics frame, signal, or node state. Avoid copied millisecond delays. If no event is available, derive a bounded deadline from a healthy run and keep the wait local to the fixture.

### Production Resource Coupling

Tests loading production `.tres`/`.tscn` from outside `Tests/` are fragile to designer rebalancing. **Preferred:** frozen test data in `Tests/Fixtures/Data/` with known stat values. When production resources are unavoidable (integration/smoke), classify assertions:

| Fragility | Example | Action |
|-----------|---------|--------|
| **FRAGILE (value)** | `DamageMultiplier == 0.5f` | Replace with `> 0f` or baseline comparison |
| **FRAGILE (config guard)** | `DoNotInherit == true` | Keep — safety net for game-breaking bugs |
| **Structural** | `IsInstanceOf<CompositeOutcome>()` | Acceptable for integration smoke tests |

**Baseline comparison pattern** (modifier tests):
```csharp
var baseline = subject.Evaluate(baseInput);
var modified = subject.Evaluate(inputWithModifier);
AssertThat(modified.Score).IsGreater(baseline.Score);
```

### Seam-Injected Dependencies Need One Real-Scene Test

When a node's production dependencies are wired via scene/Inspector (`[Export]` node refs, autoload children) but tests supply them through a `#if TOOLS SetXForTesting` seam, **at least one test must load the real production scene** (`ResourceLoader.Load<PackedScene>(...).Instantiate<T>()` + `AddChild`). A suite that *only* injects via the seam never exercises production wiring — a missing scene (e.g. a script-only autoload that can't satisfy `[RequiredExport]` node refs) then passes every test while being null at runtime. Assert by behavior (the dependency does its job), not that the field is set. Sibling: `archive_godot_node_init_timing.md`.

### Godot Timing Gotchas for Tests

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
- After `AddChild(target)`, await the physics frames or signal required for server registration; do not copy a fixed sleep from another suite.
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

---

## Teardown Doctrine & Orphan Prevention

**`Free()`/`QueueFree()` are for Nodes ONLY.** `Resource`/`RefCounted`-derived objects are reference-counted — NEVER `Free()` them in teardown; drop the references (null the field, clear the tracking list) and let refcounting collect. Freeing a Resource throws paired engine errors per call (`Can't free a RefCounted object.` + `Invalid call. Nonexistent function 'free' in base '<T>'`) — thousands per full-suite run in `TestResults/godot_test.log`.

| Object being torn down | Correct cleanup |
|---|---|
| Node in scene tree | `using ISceneRunner` (auto), or parent it (freed with parent), or `QueueFree()` |
| Out-of-tree Node (`new NodeType()`) | `Free()` in `[AfterTest]`/`[After]` |
| Resource / RefCounted (loaded `.tres`, `new SomeResource()`) | Drop references — no Free/QueueFree call at all |

**Do not infer a universal orphan ceiling from one project's logs.** Godot can emit one engine error per leaked Node at process exit, so error volume scales with leaked objects rather than distinct defects. Use the consuming project's recorded sentinel and the `diagnostics_toolkit` skill to interpret the run.

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

Treat the repository wrapper's structured status and test counts as authoritative. A process exit code alone can conflate assertion failures, runtime transport failure, timeout, and engine crash.

### Runtime executor failure

A `GodotRuntimeExecutor failed` or `Connection timeout` message invalidates every apparent pass from runtime-required tests.

- Compare the executed count with the project-owned suite floor or silent-skip sentinel. Do not copy counts from another project.
- Use the wrapper's scoped ownership checks to clear only proven orphan processes. Never kill an editor, playtest, or peer-owned runner.
- Verify the runtime executable through the project's environment bootstrap and `.runsettings`; do not publish a machine path.
- Re-run the affected suite and require both a valid count and no runtime-transport failure signature.

**More gotchas:** search auto-memory for "GdUnit4" or "Godot C# test gotchas".

---

## See Also

| Reference | Contents |
|-----------|----------|
| [scene_runner.md](scene_runner.md) | ISceneRunner API: accessors, input simulation, frame control (vendored GdUnit4 docs) |
| [advanced.md](advanced.md) | Lifecycle hooks, parameterized tests, utilities, FAQ (vendored GdUnit4 docs) |

## strict

Read this section only if your session tier line says `strict`.

### Rationalizations to refuse — the remaining rows

| Excuse | Reality |
|---|---|
| "Too simple to test" | Simple code breaks. Write the small test first. |
| "I'll test after" | Tests written after pass immediately — that proves nothing. If code exists, delete it before any test work; do not adapt. |
| "Keep as reference, write tests first" | You'll adapt it. That's testing-after with extra steps. |
| "Tests after achieve the same goals" | Tests-after document existing behavior including bugs; tests-first specify intended behavior. Descriptive, not specificational. |
| "TDD will slow me down" | TDD is faster than debugging. The shortcut is the long way around. |
| "Existing code has no tests" | You're changing it — tests for the logic you modify ship in the same change, not a future sweep. |
| "It's a small refactor" | If logic changes, behavior changes. Test the change. |
