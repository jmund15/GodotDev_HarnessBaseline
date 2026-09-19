# Choosing what to test, and judging a suite

Read before writing a suite, and when reviewing or curating one; named from `SKILL.md` §Recipe index. The entrypoint owns the domain table, the gate-coverage coupling and the regression gate.

## Gameplay Domain flow

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

## Automate vs manual

**Automate (ISceneRunner):** input → outcome (press jump → Y increases); state transitions (health=0 → death state); signal/event wiring; scene structure integrity — address nodes by type / `%UniqueName` / exported NodePath, never direct-child-by-name (`.claude/rules/test_authoring.md`).

**Manual playtest only:** "does this feel responsive?"; visual polish and juice.

**Before deferring a "fingerprint" / playtest list to the user, screen each item** — such lists routinely mix subjective items with automatable ones:
- Button/signal wiring → load the real `.tscn` + `EmitSignal(BaseButton.SignalName.Pressed)` + assert outcome (NOT the `#if TOOLS` direct-call seam — that bypasses `_Ready` wiring).
- Overlay/resource leak on close → act, then assert `GodotObject.IsInstanceValid(node) == false`.
- Focus/selection → `control.HasFocus()` after the entry hook.
- Boot / autoload init / `.tscn`-loads-without-error → Godot MCP `run_project` + `get_debug_output` smoke (catches `.tscn` parse failures and `ValidateRequiredExports` throws `dotnet build` never sees).

Only genuinely-subjective items remain deferrable: visual aesthetics, timing/juice, multi-resolution fill.

## Test Subject Selection (ask FIRST, before the domain split)

**Is this test about a reusable building block, or one composed instance?**

- **Building blocks** (components, systems, strategies, Resources with behavior) get the full three-level treatment below — coverage here protects every entity composed from them.
- **Composed entities/scenes** (a specific enemy, a specific spell scene) get (a) one **parameterized roster wiring-contract suite** over all instances (`[TestCase]` rows over a discovered set — exemplar `Tests/Integration/Enemies/NewUnitDataTests.cs`), and (b) a few **representative** full-composition E2Es — NOT one per entity. No per-entity logic suites; no per-instance resource pins.
- **Data-integrity pins are per-SCHEMA, never per-instance.** A project-wide convention (iso facing, sprite scale anchor) is pinned once over a discovered set.
- *Litmus:* if this test fails, is the defect in the block or in one instance's wiring? Block → test the block. Wiring → the roster suite already owns it.

## Test Coverage Strategy

New/complex systems need all three levels:

| Level | Scope | Example |
|-------|-------|---------|
| **Unit** | Single component | `ResponseMatcher.FindMatch()` returns correct reaction |
| **Integration** | Components together | `ResponseComponent` processes collision and fires handler |
| **E2E** | Full path | Spell collision → reaction triggers → outcome visible |

Unit tests passing ≠ system works. Cross-domain systems especially need E2E coverage.

## Primary Observable Behavior (POB) Rule

Every new system with player-observable behavior MUST include at least one E2E/integration test asserting the primary observable outcome — what a player would notice if the system broke. One POB test per system, not per behavior — the roster suite owns the rest.

| System | POB Test Assertion |
|--------|--------------------|
| Critter AI | "at least one critter position changes over 2s" |
| Spell casting | "spell instance spawns when cast input simulated" |
| Status effect | "movement speed stat reduced while effect active" |
| Drop system | "item spawns when trigger fires" |

Unit tests at integration boundaries miss the engine-lifecycle failure class (`_Ready` order, nav-map timing, signal wiring, physics-frame delivery); the POB test is the last line of defense against it.

POB is non-waivable, and unit-test coverage is not an argument against it — unit depth is a different axis.

## Test Level Philosophy

Prefer behavioral tests over implementation checks for game mechanics:
- ❌ `AssertThat(blueprint.ActiveTraits.Count == 0).IsTrue()` — tests check logic
- ✅ `AssertSpellCount(runner, 0)` — tests observable outcome

A unit test on internal logic can pass while gameplay is broken. Test what the player would observe.

## Anti-pattern: Documentation-only tests

*Litmus: does every assertion consume a value produced by production code?* An assertion whose operands all derive from locally-constructed literals is documentation-only regardless of syntax, even inside a suite with real setup.
```csharp
// BAD - this gives false confidence
[TestCase]
public void Test_Feature_Documentation() {
    AssertThat(true).IsTrue();  // NOT A REAL TEST!
}
```

## Anti-pattern: Constant-mirroring tests

Asserting a field equals its default breaks on intentional value changes and can never catch a real bug. Test the behavioral consequence instead ("with default config, attraction scoring is disabled" tests the scoring path, not the field). **Refusal stance:** remove and replace, never augment. A code comment may document the value but does not justify keeping the mirror test beside a behavioral one. Offering the comment as a sidecar to the bad test — **STOP**.

## Anti-pattern: Compiler-guaranteed and storage-only tests

Four shapes carrying zero regression safety. Each carve-out is load-bearing; a sweep matching on shape alone deletes real coverage.

| Shape | Carve-out — do NOT delete when |
|---|---|
| `IsInstanceOf<T>` / `is T` / `typeof(I).IsAssignableFrom(typeof(X))` | the type is an *interface* with no consumer resolving it — nothing else enforces the declaration |
| Enum ordinal (`(int)E.V == N`) | the enum is serialized **by value** — `.tres`/`.tscn` store raw ints, so ordinals are a data contract and renumbering silently remaps shipped content |
| Constructor-stores-field | the constructor validates, null-coalesces, or transforms |
| Bool property set/get/toggle | the setter has side effects, **or the value is read and branched on by production code** (a gating flag in a guard clause) |

**Deletion gate:** a carve-out case is a *rewrite*, not a delete — replace the property-mechanics test with a behavioral test on the consumer. Before deleting any test as redundant, verify the successor exists (`git grep` the symbol); an assumed successor that isn't there turns a coverage regression into a closed finding.

## Anti-pattern: Rationalizing strict TDD away on integration regressions

When the change IS the prevention of a memorialized integration regression class (hot-loop, restart-loop, process-ordering race, BB-flag-soup, perception-staleness), an integration test exercising the symptom is mandatory however the diff splits across `.cs`/`.tscn` and however trivial the C# looks. Hot-loop and ordering bugs live at the SEAM between layers (BT+BTState, BehaviorTree+RestartPolicy switch, HSM+child-state lifecycle, Pool+Spawn callback) — test the seam, not the leaves. Logic Domain tests CAN exercise seams when the participants are framework primitives instantiable in code. Litmus: *"if this change re-introduced the bug it claims to fix, would my suite catch it?"* Answer "manual playtest" ⇒ write the seam test first. Template: `Tests/Logic/AI/RestartPolicyCases.cs`. **Refusal stance:** reclassify the *bug*, not the *file* — the modified file's domain is irrelevant. Accepting "Logic Domain file → unit test sufficient" as the axis — **STOP**.

## Measurement Harnesses (generative domains)

For generative/distributional domains (procgen, spawning, loot, crafting outcomes), prefer ONE corpus-style measurement harness over accumulating example pins: N seeds × M representative profiles through the real entry point, asserting success-rate / distribution / ceiling thresholds **pinned from the first observed run with headroom**, with a typed-cause histogram composed into the failure message so a regression names its dominant cause. Asserts the property gameplay depends on and doubles as tuning telemetry. Template: a corpus test under `Tests/Logic/<GenerativeDomain>/`.

## Retention & Curation Policy

Keep all *building-block behavior* tests; manage execution time with filtering. Curation is an obligation: **a change touching a suite retires that suite's redundant, documentation-only, or duplicate-double coverage in the same change.**

**Delete when:** feature removed; redundant with block-level coverage; checks implementation details, not behavior; documentation-only (litmus above); the suite is a per-entity/per-instance duplicate of a roster suite (§Test Subject Selection). Suite growth is not free — every test is maintenance surface and refactor drag.
