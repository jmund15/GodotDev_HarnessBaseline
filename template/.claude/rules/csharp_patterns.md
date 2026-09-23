---
paths:
  - "**/*.cs"
---

# C# & Godot Interop Patterns

Mechanical patterns for writing or editing `.cs`: lifecycle ordering, nullability, export discipline, defensive guards, test-helper conventions. Siblings and companion skill: §Touchpoints. Snippets, failure mechanics and shape-specific material: [`reference/rules/csharp_patterns_examples.md`](../reference/rules/csharp_patterns_examples.md), cited below at the sentence that needs it.

## Core Conventions

- **Pure functions** wherever possible. **Control flow:** no nested if/else, early returns, ALWAYS brackets `{}`.
- **Logging:** `JmoLogger.Info/Warning/Error`, never `GD.Print`. Log STATE CHANGES, not state. Which suites an `Error` actually fails: `reference/rules/csharp_patterns_examples.md` §Logging failure semantics.
- **Comments default to none.** Add one when WHY is non-obvious to a cold reader (invariants, race hazards, tuning rationale). NEVER restate WHAT; never cite task/PR/"Phase X"/dates/CLAUDE.md rules; never project history (vault-only, unlinked from source). Litmus: *"If I delete this, will a maintainer 6 months out make a wrong decision?"* No → don't write it. **A sanctioned WHY is 1–3 lines stating the invariant itself** — build-up, the bug it prevents and alternatives go in the commit message. Doc-only commits to recent code are a smell — cut the code over clarifying it (`reference/rules/csharp_patterns_examples.md` §Comment discipline).
- **Trust radius — a `///` is authoritative about its own member and nothing else.** `<summary>` states that member's contract; `<remarks>` carries longer supplemental explanation.
- **Obligation, not observation.** An *obligation* is a constraint a caller must honour or a guarantee the member makes — invariants, call-ordering, required configuration, failure modes, what it throws — and it is REQUIRED on seams (`design_litmus.md` #7); a collaborator-owned constraint (a driver's phase ordering, a base's contract) is still an obligation. **Litmus: must a caller honour this to use the member correctly?** Yes → write it richly. No → it is an observation about other code; cut it. Both definitions in full: reference §Comment discipline.
- **References are cref, not font.** A member named inside `///` uses `<see cref="X"/>`; a **parameter** uses `<paramref name="x"/>` and a type parameter `<typeparamref>`. `<c>` is typographic and unchecked — correct for non-symbols (literals, snippets, expressions, pseudo-code). cref diagnostics and the gate they trip: reference §Comment discipline.
- **TODO, future-tense, and notes to yourself or to agents go in `//` line comments.** Never `///` — a `<summary>` on an `[Export]` ships verbatim to the designer-facing Inspector tooltip. That `<summary>` is softer than the trust-radius rule: a designer-facing description is sanctioned there even when it states no caller obligation.
- **Put the `///` above ALL of the member's attributes**, `[ExportGroup]` included. Between them it is orphaned (CS1587), dropped from the XML sidecar, and the tooltip silently vanishes with no build failure. How the tooltip is wired and what hovering shows: reference §Export tooltip mechanics.
- **Repair on sight.** Fix a false or dangling doc comment in the turn you find it (`feedback_dont_defer_immediately_addressable.md`). Enforced at commit by the `DOCS` check in `/regression_gate`; full-tree sweep: `.claude/scripts/doc_warning_check.sh`.
- **Strings:** prefer `StringName` for Godot identifiers (node paths, signal/animation names).
- **A helper a second consumer needs moves to the family home** (`NodeExts`, `JmoMath`), never copied privately; match that family's conventions and migrate the existing hand-rolled call sites.
- **Name a nullable-returning helper `Find*` or `Try*`, never `Get*`.** `Get*` reads as guaranteed resolution and callers skim past the compiler warning; the name is the cheapest enforcement of the contract, at the read site (`feedback_nullable_return_naming`).

## Lifecycle & Constructors

**Rule:** **NEVER** put game logic in the C# constructor (`public MyClass()`) — the Godot native side is not initialized yet. Use `_EnterTree()` for initialization, `_Ready()` for node wiring.

**Rule:** Perform all node lookups (`NodeExts`) inside `_Ready()` and cache the result. Never query the scene tree inside `_Process` — hot-path scene-tree walk.

**Rule:** A frame delay is owned by a Node: a field naming the pending work, consumed in that node's `_PhysicsProcess` (physics-dependent, e.g. a probe against bodies added this frame) or `_Process`. Never `GetTree().Connect(PhysicsFrame, lambda, OneShot)`, `await ToSignal(tree, PhysicsFrame)`, or a static helper scheduling its own retry: the continuation is invisible, uncancellable, outlives its caller and hides the caller's timing defect. `CallDeferred` is end-of-idle-frame work, not a proven physics wait. A mechanism shared by 2+ consumers gets ONE owning node (`DeferredWorkQueue`), never a per-consumer copy (`design_litmus` #1).
<!-- retire-when: review-by: 2027-03-15 -->

## Nullability Convention for Godot Properties

Godot has no typical C# constructor, so properties start null until `_Ready()`.

| Scenario | Annotation | Rationale |
| :--- | :--- | :--- |
| Set in `_Ready()` | `= null!` | Guaranteed after initialization |
| **Required** `[Export]` | `= null!` + `[RequiredExport]` | Fail-fast with clear error |
| **Optional** `[Export]` | `?` nullable | Genuinely might not be set |
| **Collection** `[Export]` (`GCol.Array<T>` / `Dictionary<K,V>`) | `= new()` | Editor replaces the default on load; empty default = iterable without null checks. NEVER `= new()` a *Resource-typed* export — pathless Resources serialize inline into every referencing `.tres` (use the rows above). |
| Runtime state that can be null | `?` nullable | Could legitimately be null |

```csharp
[Export, RequiredExport] public AbilityArchetype Archetype { get; set; } = null!;
[Export] public AbilityArchetype? OptionalOverride { get; set; }  // No RequiredExport = optional

public override void _Ready()
{
    this.ValidateRequiredExports();  // One line validates ALL required exports
}
```

**Rule:** every `[Export] … = null!` pairs with `[RequiredExport]` and one `this.ValidateRequiredExports()` — first line of `_Ready()` for Nodes, during initialization for Resources (which have no `_Ready()`; `ResourceExts`, global namespace). `pattern_enforcer.py` blocks the unpaired form at edit time. `= null!` suppresses IDE warnings at every access site and `[RequiredExport]` throws `NodeConfigurationException` / `ResourceConfigurationException` when the Inspector slot is empty; hand-written null checks instead would draw "unnecessary null check" warnings and boilerplate.

## Defensive Patterns

- **Event initialization.** Initialize events with `= delegate { }` to avoid null checks: `public event Action SomeEvent = delegate { };`
- **Nullable default parameters.** A parameter with an `= null` default must be nullable: `void Method(StringName? reason = null)`. Fix base → all overrides.
- **TryGet null guard.** Add `|| result == null` after `TryGet` / `TryGetFirstChildOfType`: `if (!bb.TryGet<T>(key, out var result) || result == null) { return; }`
- **Data-driven range guard.** When `Random.Next(min, max)` uses editor-exported values, guard with `Math.Min`/`Math.Max` **at the consumption site, not the data source** — designers can set Min > Max, and `Random.Next` throws `ArgumentOutOfRangeException` when `minValue > maxValue`. Snippet: `reference/rules/csharp_patterns_examples.md` §Data-driven range guard.
- **Float aggregation accumulates in `double`.** Never `Enumerable.Average()`/`Sum()` over a `float` source; cast to `double` first, or accumulate explicitly. Why vectorized float aggregation depends on lane count: reference §Float aggregation.
- **Atomic initialization.** When a method can fail with an early return, dependent state mutations happen inside the success path, not in the caller after the call. A void return leaves the caller unable to tell success from failure, and half-initialized objects cause subtle downstream bugs. Snippet: reference §Atomic initialization.
- **Fail-closed guards on data-resolved floats.** `x <= 0f` is not a fail-closed guard: every comparison against NaN is false, so NaN passes the guard and reaches the math behind it. Write `!(x > 0f)`, and test `float.IsFinite` on every operand a designer can author. Snippet: reference §Fail-closed float guards.
- **A NaN threshold inverts the gate rather than breaking it** — `if (value > max) { reject; }` stops rejecting entirely, so the failure presents as a gate that silently passes everything. Guard where the value is produced AND where it is consumed: any float resolved from `[Export]`/`.tres` data (mass, speed, radius, an angle cone) is not compiler-guaranteed finite. *Litmus:* for each float guard, ask what happens when the value is NaN — if the answer is "the branch I wrote to be safe doesn't run", the test is inverted.
- **Guard symmetry across siblings.** A guard added to one of N parallel siblings goes on all of them or none. The unguarded sibling degrades to a quiet wrong state instead of a loud error; grep the sibling set for the same entry shape and replicate.

## Signals vs Events

- **Gameplay Logic:** use **C# native events** (`public event Action`) — faster, type-safe, refactor-friendly. Do NOT use Godot Signals for game logic.
- **UI / Engine Interaction:** use **Godot Signals** (`[Signal]`, `.Connect`) — required for UI Nodes (`Button.Pressed`) and Area3D detections. Connect in `_Ready`, or via Editor if strictly visual.
- **Cross-Cutting vs Domain Events:** a centralized `EventBus` autoload carries events spanning multiple unrelated systems (UI notifications any subscriber might care about); domain registries (`ActorRegistry`, `ItemRegistry`) carry events scoped to one subsystem. Prefer domain registries; EventBus only for events belonging to no single domain.

## Exports & Inspector

- **Numeric Parameters:** when a value could be constant OR attribute-driven (speed, cooldown, duration), export `BaseFloatValueDefinition` rather than a raw `Attribute`, so designers pick `ConstantFloatDefinition` or `AttributeFloatDefinition` per-field without code changes. Resolve via `definition.ResolveFloatValue(statProvider)`.
- **Configuration:** use `[Export]` for values designers tweak (Speed, Damage, Prefabs).
- **References:** use `[Export]` for assigning child nodes IF the structure is rigid. *Better:* `GetNode<T>("%UniqueName")` for internal scene wiring, to avoid Inspector rot.
- **Data Types:** prefer `Godot.Collections.Array<T>` over `System.Collections.Generic.List<T>` **only** if it must be Inspector-visible; otherwise standard .NET collections. In files needing both usings, alias `using GCol = Godot.Collections;`.

## `[Tool]` Attribute — Editor-Time Type Registration

**Rule:** blanket `[Tool]` on every `[GlobalClass]` **Resource** (`[GlobalClass, Tool]`); **selective** on Nodes — a Node gets `[Tool]` only with editor-time code (`Engine.IsEditorHint`, `_ValidateProperty`, `[ExportToolButton]`) or when it extends a framework-convention Node (`State` / `BehaviorTask` / `BTState`).

- **Cascade:** when a `[Tool]` script `[Export]`s a typed Resource (or an `Array<>` / `Dictionary<,>` of one), that Resource and every concrete subclass assignable to the field MUST also be `[Tool]`. A gap fails only in the editor, so no test catches it.
- **Every `GodotObject`-derived script class declares a PARAMETERLESS constructor**; inject dependencies through a second ctor into `= null!` fields, and guard engine-invoked entry points (`_Parse*`) against the inert instance the editor recreates.
- *Enforced* by `pattern_enforcer.py` at edit time and `/regression_gate` step 1c. Cascade mechanism, escape hatch, enforcement chain and crash capture: `reference/rules/csharp_patterns_examples.md` §`[Tool]` cascade mechanics and §Parameterless-constructor requirement.

## Async & Tasks

- **Rule:** avoid `async void`. Use `async Task`, or `async void` ONLY for top-level event handlers (Button pressed).
- **Rule:** do not touch Godot Nodes from a background `Task.Run` thread. Use `CallDeferred` to return to the main thread.

## Test Helper Setters

**Rule:** every `internal` member added for test access sits inside `#region Test Helpers` and `#if TOOLS` / `#endif`. **Never `#if DEBUG`** — `dotnet test` defines `TOOLS`, not `DEBUG`. A production site invoking a test-hook event takes the same `#if TOOLS` guard.

- A setter that production code calls is not a test helper; it belongs in the regular API.
- When production changes a property through an event-firing method (`StartPhase(p)` → `RunPhaseChanged`), the helper calls that method, or is named `SetForTest_BypassEvents(...)`.

Sanctioned shapes, block example, the reasons behind each clause, `/audit_test_accessors` and fixture Builders: `reference/rules/csharp_patterns_examples.md` §Test helpers.

## Touchpoints

- [`reference/rules/csharp_patterns_examples.md`](../reference/rules/csharp_patterns_examples.md) — every snippet and failure mechanism this rule cites.
- Sibling rules on `**/*.cs`: [`csharp_lsp.md`](csharp_lsp.md) for symbol navigation; [`jmodot_utilities.md`](jmodot_utilities.md) for Jmodot utilities (NodeExts, JmoRng, JmoMath, Map, configuration exceptions, IComponent gotcha).
- Sibling rule on `Jmodot/**/*.cs` only: [`jmodot_framework_authoring.md`](jmodot_framework_authoring.md) for 2D/3D parity, framework boundary, static seam pattern.
- Companion skill: [`architecture_philosophy/SKILL.md`](../skills/architecture_philosophy/SKILL.md) for design-time decisions (Resource Strategy Hierarchies, DI, Marker Interfaces).

<!-- retire-when: review-by: 2027-03-12 -->
