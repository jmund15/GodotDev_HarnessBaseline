---
name: Godot Architecture
description: >-
  Auto-load when designing or reviewing Godot/C# systems — node retrieval and coupling,
  IComponent/Blackboard initialization and component contracts, Resource strategy families,
  autoloads and singletons, init-timing, pooling, the [Tool] attribute policy, or Jmodot core
  tools. SKIP for language-neutral design questions (architecture_philosophy owns the
  doctrine this skill applies), mechanical edits (path-scoped rules auto-load) and Jmodot
  subsystem reference (jmodot).
---

# Godot Architecture

The Godot, C# and Jmodot form of the design doctrine in [`architecture_philosophy`](../architecture_philosophy/SKILL.md). Each section below either stands alone or names the generic principle it applies.

## Companion rule files

Godot/C# path-scoped rules (C# patterns, scene authoring, Godot data-file invariants, HSM/BT, physics, C# LSP routing) auto-load on matching file reads; don't load them manually or restate them here.

- `/structure_audit` reviews layout against `architecture_philosophy`'s `structure_rules.md`.

## Coupling & Discovery

### Node Retrieval & Coupling

**Rule:** Avoid `GetNode()` and hardcoded 'magic' paths when possible.
**Rule:** Prefer **Interface-based** or **Recursive** retrieval over direct parent/child assumptions.
**Tool:** Strongly prefer `NodeExts` extension methods (from Jmodot) for all node queries, unless there is specific good reason for not doing so.

- **Why:** They handle null checks, optional recursion (`includeSubChildren`), and Interface matching automatically.
- **Why Not:** If there are multiple nodes of the same type in a scene, `this.GetFirstChildOfType<T>()` is ambiguous and not applicable. In those cases, traditional lookups may be required, or a direct `[Export]` reference.
- **Preferred Syntax:**
    - `this.GetFirstChildOfType<T>()` instead of `GetNode<T>("Path")`
    - `this.GetChildrenOfInterface<IDamageable>()` instead of manual iteration.
    - `this.TryGetNode<T>(...)` for safe access.

### Interface Usage

Principle and example: `architecture_philosophy` §Interface Usage.

**Rule:** Nodes should interact via Interfaces, not concrete classes.

- *Implementation:* Use `IGodotNodeInterface` on components to expose the underlying `Node` when passing interfaces around.
- *Adapter Conflicts:* When interface members conflict with Godot base class (e.g., `ICharacterController3D.IsOnFloor` vs `CharacterBody3D.IsOnFloor()`), use **explicit interface implementation**: `bool ICharacterController3D.IsOnFloor => _controller.IsOnFloor;`

### Semantic Targeting Over Collision Layers

**Rule:** Prefer `IIdentifiable` and `Category` filtering over collision layer masks for targeting systems.

- *Why:* Semantic targeting is more flexible, self-documenting, and decoupled from physics configuration.
- *Pattern:* Query with `uint.MaxValue` collision mask (all layers), then filter by `IIdentifiable.GetIdentity().Categories`.
- *Example:* `TargetingCapability` filters targets by semantic categories such as "Hostile" or "Interactable" rather than checking collision layers.
- *Benefit:* Adding a new targetable type only requires assigning the correct Category, not updating the collision matrix.

### Godot Groups vs Interfaces

- **Rule:** Prefer C# Interfaces over Godot Groups for type-safe gameplay logic.
- **Acceptable uses for Groups:** Scene-wide iteration where interface traversal can't reach (e.g., engine integration, debug tooling, editor plugins).
- **Preferred:** Use C# Interfaces (`IInteractable`, `IDamageable`).
    - *Why:* Groups are stringly-typed and prone to typos. Interfaces are checked at compile time.
    - *Usage:* `if (body is IDamageable target) { target.TakeDamage(); }`

## Dependency Injection & State Ownership

### Component Initialization Paths

**Rule:** Every entity-scoped component initializes through ONE framework path — `IComponent` auto-discovery driven by `EntityNodeComponentsInitializer` (ENCI). Bespoke `Initialize(...)` signatures are legal only in the enumerated categories below.

**Three-phase contract.** ENCI scans the entity subtree once per phase; each phase completes for ALL components before the next begins.

| Phase | Scans | Calls | Contract |
|---|---|---|---|
| 0 | `IBlackboardProvider` — *independently* of `IComponent` | reads `Provision` → `bb.Set` | Publishes component refs and lazy POCOs. **`Provision` MUST be idempotent** — it may be evaluated more than once per entity lifetime, so cache lazy payloads on a field (`_x ??= new()`). A publisher may implement only this property; `IComponent` on top of it is ceremony when `Initialize` only sets `IsInitialized` and `OnPostInitialize` is empty. A component that provisions and resolves real dependencies earns both. Reusable outside ENCI via `EntityNodeComponentsInitializer.RunPhase0(Node entity, IBlackboard bb)`. Two providers on one key is a scene-authoring defect: last-writer-wins can make the entity silently inert. Fix the scene; never add a sibling with the same role. |
| 1 | `IComponent` | `Initialize(bb)` | Resolve dependencies only. Unordered — **must not** subscribe to sibling component events and must not assume any sibling is initialized. |
| 2 | components that returned `true` | `OnPostInitialize()` | Post-barrier: all siblings are initialized. **Subscriptions belong here**, and must be idempotent because repeated initialization can invoke this phase again. Carve-out: an `[Export]`-resolved dependency is a scene-load-time reference, so subscribing to it in `_Ready` is correct. |

- *Why the barrier:* Phase-1 order is scene-tree order, i.e. arbitrary. Cross-component wiring done in `Initialize` is an order-dependent race; in `OnPostInitialize` it is order-independent by construction. Producer/consumer races also dissolve by moving the published value to a Phase-0 `Provision`.
- **Never self-call `OnPostInitialize()` from inside `Initialize`** — the phase driver invokes it after the Phase-1 barrier (ENCI for scene-authored components; `ComponentInitHelper` for components that delegate their lifecycle). The house tail of `Initialize` is `IsInitialized = true; Initialized(); return true;`.
- Components silently no-op while `IsInitialized` is false — diagnostic in `rules/jmodot_utilities.md` §IComponent.
- *Why not constructor injection:* Godot manages Node instantiation; constructors run before the engine is ready.
- *Key types:* `IComponent`, `IBlackboardProvider`, `IBlackboard`, `BBDataSig` (partial class for project-specific keys), `EntityNodeComponentsInitializer`.

**Required-dep policy (the `bool` return).** Return `false` iff a dependency is missing *without which the component serves no purpose*. Dependencies that gate only *part* of the behavior are soft: `TryGet`, degrade, return `true`.

- *Litmus:* *"With this missing, does the component still do anything a designer expects?"* No → hard (`false`). Yes → soft.
- A misconfigured scene must fail LOUD. Never Warn-and-disable a component that cannot function.
- **ENCI owns the single Error** on a `false` return, and enforces the consequence rather than announcing it: it omits the component from the initialized set (Phase 2 never runs on it), calls `SetProcess(false)`/`SetPhysicsProcess(false)`, and **retracts the component's Phase-0 provision** so no sibling can resolve a rejected component and subscribe to events it can never raise.
- **The component supplies the WHICH-key detail at `JmoLogger.Debug`**, not a second Error — one failure, one Error line, uniform across every converted component. House phrasing: `"Required dependency BBDataSig.X not found"`.
- **Idempotency is part of the contract.** Prefer teardown-first at the top of `Initialize` (unsubscribe/clear before re-resolving); a `_postInitialized` flag reset by the same teardown path is the fallback where teardown-first is invasive.
- Both phases are isolated per component: an unhandled throw is caught, logged as an Error, and the remaining siblings still run. ENCI executes inside the entity root's `_Ready`, where an escaping exception is swallowed with no log line.

**Discovery: auto-discovery is the default; `[Export]` is the exception.** Publish via `IBlackboardProvider`, consume via `bb.TryGet` — producer and consumer stay mutually ignorant, and dropping a component into a scene wires it with zero edits to the entity root.

- `[Export]` component refs are reserved for: **multiple same-type siblings** (auto-discovery is ambiguous), **cross-subtree references** (outside ENCI's scan), and **wiring-as-design-intent** (a designer choosing WHICH of several candidates).
- An entity root's hand-wiring block should hold only genuinely entity-specific work. A `bb.Set` that merely republishes a child component under its canonical key is redundant with Phase 0 — delete it and add the `Provision`.

**Justified bespoke initialization paths** — documented exceptions, not migration debt. Anything entity-scoped outside these uses the `IComponent` path:

| Category | Condition | Examples |
|---|---|---|
| Invocation-scoped runners / transient bodies | Parameters are computed per operation; there is no persistent entity BB for them to live on | behavior runners, transient physics bodies |
| Programmatic environment helpers | Constructed in code, never scene-authored under an entity root | `Jmodot/Implementation/Environment/CentralPullForceArea.cs`, `VelocityDragForceArea.cs` |
| Graph-scoped services | Scope is an `IBlackboardGraph`, not one entity's BB | session- or world-scope controllers and runtimes |
| Service-injected consumers | The dependency is a scoped service pushed by an installer, not an entity sibling | consumers wired by a service installer |

**Editor-time dependency visibility.** A component whose hard dependency is a *sibling node* should surface the gap in the editor via `_GetConfigurationWarnings()`. Use `ConfigWarnings.RequireEntitySibling<T>(this, message)` rather than hand-rolling `GetParent()` + a child scan — a bare parent check false-warns on any component nested below the entity root, which ENCI's descendant walk resolves fine. Concat `base._GetConfigurationWarnings() ?? []` so a future base's warnings are not swallowed. Godot only displays warnings from `[Tool]` scripts — so such a component takes `[Tool]` plus `Engine.IsEditorHint` early-returns in every lifecycle hook running game logic (`_Ready`, `_PhysicsProcess`, `ValidateRequiredExports`). This is exactly the selective-on-Nodes case in the *`[Tool]` Attribute Policy* below (editor-time code) — no policy exception. **A non-`[Tool]` `_GetConfigurationWarnings` override is dead code**; either promote the script or delete the override.

### Component Contract (design rules)

The initialization mechanics above say HOW components wire; these five rules say what a well-designed component composition IS:

1. **Divergence by composition.** Per-entity variation is expressed by WHICH components/nodes an entity composes plus exported Resource data — never by structural rewiring of shared blocks.
2. **Dependencies are one-directional.** The dependent knows its dependency (hurtbox→health), never the reverse. Direct sibling-component coupling is unjustified by default; named exceptions are structural parent-child relationships (body→shape).
3. **Every inter-component dependency is COMMUNICATED, never hidden.** Exactly one of: (a) *explicit slot* — an exported node/interface reference the author must assign, so the requirement is visible in the Inspector; or (b) *auto-resolved* — the component locates its dependency itself (Blackboard/ENCI per the Discovery rule above) AND surfaces `_GetConfigurationWarnings` when it is absent AND fails loud at initialize. **House default: (b)** — it matches Blackboard DI and keeps the designer surface minimal; reserve (a) for genuinely ambiguous bindings (multiple valid candidates on one entity, per the `[Export]` exceptions above). Resolution must survive regroup — never a bare parent/sibling walk (`gotcha_godot_scene_reference_traps`).
4. **The contract is written where the author stands.** The dependency and its resolution mode are stated in the component's `<summary>` (surfaced in the Inspector once the tooltips plugin lands).
5. **The contract is more than the member list.** A component's interface is its signatures PLUS the facts a signature cannot carry: which phase resolves what and what may not be touched before the Phase-1 barrier, which dependencies are hard (loud `false`) versus soft, the idempotency obligation on `Provision`/`OnPostInitialize`, and teardown expectations. Every one of those is a shipped defect class here — specify them at design time, in the `<summary>` per rule 4, or the seam is undesigned. Introduction-time litmus: `rules/design_litmus.md` #7.

Scene-facing mirror: `rules/scene_authoring.md` §Scene anatomy.

### Capability-Graded Consumption (Jmodot form)

Principle: `architecture_philosophy` §Capability-Graded Consumption. Publish the deriving component's reference through a Phase-0 `Provision`, resolve it via `bb.TryGet<ICapability>` and call typed members; publishing the derived *value* to a Blackboard key is the fused shape that rule rejects.

### Blackboard Decoupling Principle

**Rule:** Do NOT bypass the Blackboard with direct-reference calls, even for single-consumer optimizations. The BB exists specifically so producers and consumers don't need direct references — producer `.Set()`s a key, consumer `.TryGet()`s it, they stay mutually ignorant.

- *Anti-pattern:* Installer calls `component.AttachThing(thing)` directly "for efficiency" after setting `BB.Set(key, thing)`. Parallel-wires data through a direct channel that duplicates BB's job. Creates inconsistency (why does X use BB but Y use direct?) and doesn't scale (every new installer must enumerate dependent components).
- *Correct pattern for genuine late population:* use Blackboard-mediated bounded retry with a cap and a Warning on timeout. This preserves decoupling without hiding an unbounded wait.
- *Why this matters:* Every direct-push shortcut is a coupling channel future code must reason about. The BB is the decoupling layer; bypassing it defeats its purpose.

### Typed-Owned State over Blackboard Flags

**Rule:** When state has a clear owner whose **lifecycle bounds the state's existence**, store the state on the owner — not as a `BB.IsXxx` flag. BB flags are appropriate for genuinely cross-cutting data without a single owner (e.g., `BB.CharacterController` reference, `BB.Stats`); they are inappropriate for state with a natural owner (e.g., "is this entity in control loss right now").

- *Why:* A BB flag is public-field-equivalent — any system can read or write it at any time, no scope guarantees, no lifecycle hooks. Setters and clearers must be paired by hand; missing a clearer produces silent desync. Owner-bound state (e.g., a HSM state that exists IFF the entity is in that state) has compile-time guarantees: state lifetime IS the data's validity window.
- *Litmus:* *"Does this state have a meaningful owner whose lifetime IS the state's lifetime?"* Yes → owner-bound. No (genuinely cross-cutting, no natural owner) → BB.
- *Corollary — state-bound attribution:* when chain-attribution data (or any time-windowed metadata) has a bounded window matching a state's lifetime, store it on the state, not in a parallel tracker component. State entry sets it; state exit clears it. No separate "tracker" component with parallel set/clear discipline.
- *Concrete shape:* a response state's `AttributedSource` field exists only while that state is active; no parallel attribution-tracker component. A marker capability query (per *Marker Interface as Capability Query* below) replaces ad-hoc Blackboard flags for each concrete response state.

### Marker Interface as Capability Query (Godot example)

Rule and three-way choice: `architecture_philosophy` §Marker Interface as Capability Query. Polymorphic-member example: a collision host enacting its own physics — kinematic velocity-reflect vs RigidBody Jolt-defer vs beam ray-reflect.

## Init-Timing & Data-Source Readiness

Decision order (fix install order before any retry): `architecture_philosophy` §Init-Timing & Data-Source Readiness.

**Data-source readiness characteristics:**

| Channel | Ready at... | Typical failure mode | Pattern if genuinely late |
|---|---|---|---|
| Blackboard (`IBlackboard` via `IComponent`) | Phase 1 Initialize, after Phase 0 Provisions | Installer writes key post-Initialize | Bounded retry (~300 frames / 5s) + Warning; preserves BB decoupling |
| Sibling component events (`IComponent`) | Phase 2 `OnPostInitialize`, after ALL Phase-1 Initialize | Subscribing inside `Initialize` — order-dependent race | None needed; subscribe in `OnPostInitialize` |
| Scene tree (`GetFirstChildOfType`) | `_Ready` of both nodes | Querying in `_EnterTree` or constructor | Use `_Ready`, not earlier; `CallDeferred` if genuinely mid-frame |
| Autoloads / singletons | Always, before any non-autoload `_Ready` | Touching in autoload's own constructor | Move to `_EnterTree` or `_Ready` |
| Data files (`[Export]` Resource refs) | `_Ready`, if Inspector-wired | Missing Inspector wiring | `[RequiredExport]` + `ValidateRequiredExports()` — fail-fast, not retry |
| Static registries (lazy-built) | First access | Access during class-init or constructor | Move access to `_Ready`; registries handle the lazy-build themselves |
| Signals / events | `_Ready` (subscribe) → `_ExitTree` (unsubscribe) | Callback fires on freed object | `IsInstanceValid` guard (`archive_godot_disposal_gotchas.md`, auto-memory) |
| Physics broadphase (`Monitoring=true`) | 2–3 frames after set | Querying overlaps same frame | Bounded retry with SMALL cap (2-3), silent miss OK |
| `SetDeferred` property sync | 2 ProcessFrames | Awaiting only 1 frame | `await ToSignal(ProcessFrame)` twice |
| Async / network / asset loading | Unbounded | Assuming sync | Event/signal subscription (requires architectural support) |

**Root-cause diagnostics (try these FIRST):**

- **"BB key not populated at Initialize"** → Is an installer running after `EntityNodeComponentsInitializer`? Can the installer's writes move into Phase 0 via `IBlackboardProvider` instead? Only if structurally impossible (e.g., match-level installer writing per-entity key) → bounded retry.
- **"Child not found by `GetFirstChildOfType`"** → Is the child scene-authored (available at `_Ready`) or programmatically added later? Prefer scene authoring; use `CallDeferred` if genuinely late.
- **"Export is null"** → Missing `[RequiredExport]` + `ValidateRequiredExports`. Don't work around — fail fast.

**Anti-patterns:**

- **Direct push when BB mediation exists.** Parallel-wires data through two channels. See *Blackboard Decoupling Principle*.
- **Registry / autoload access in constructor.** Godot native side not ready yet.

**Canonical patterns:**

- Blackboard late population: bounded retry only when install order cannot make the value ready.
- Physics broadphase: a small bounded retry may be valid because monitoring changes take a few frames.
- `CallDeferred`: use for a known one-frame scene-tree ordering boundary.
- `SetDeferred`: await the documented number of process frames before reading the property.

## Lifecycle Patterns

### Static Bootstrapper Pattern

**Rule:** When multiple Node types need identical initialization but cannot share a base class (C# single-inheritance + different Godot physics body types), extract shared logic into a static bootstrapper.

- *Pattern:* `DomainBootstrapper.Initialize(Node target, ...)` — takes the root node as parameter.
- *Example:* an entity bootstrapper handles init for `CharacterBody3D`, `RigidBody3D`, and `StaticBody3D` environment entities.
- *Why not interfaces with default methods:* C# interfaces cannot access Godot scene tree APIs.

### Singleton Autoload Pattern

**Rule:** Autoload singletons follow a standard shape:

```csharp
public static T Instance { get; private set; }

public override void _EnterTree()
{
    if (Instance != null) { QueueFree(); return; }
    Instance = (T)this;
}

public override void _ExitTree()
{
    if (Instance == this) { Instance = null!; }
}
```

- *Two variants:*
    - **Node autoloads** (registered in `project.godot`): Use `_EnterTree`/`_ExitTree` lifecycle with `QueueFree()` guard.
    - **Static lazy singletons** (no scene tree): `Instance ??= new T()` with thread-safe lock. Use when the singleton doesn't need Node features.
- *`[RequiredExport]` autoloads need a `.tscn` wrapper:* a Node autoload with an Inspector-wired `[Export]` cannot be a bare `.cs` autoload. Register a scene containing the node, script, and wired exports.
- *Test isolation:* Include `internal static void ResetForTesting()` — autoloads persist across test cases. Without it, state leaks between tests.

## Extensibility Patterns

The generic patterns live in `architecture_philosophy` §Extensibility Patterns; these are their Godot, C# and Jmodot forms.

- **Default Value Pattern — framework boundary caveat:** Inside Jmodot, the framework must not reach into `{{PROJECT_NAME}}.*`. Add a framework-owned configuration seam and let project startup populate it. The seam owns a reset path for test isolation.
- **Lazy-Loading Registry Pattern:**
    - `Get` throws via `JmoLogger.LogAndRethrow` on missing keys (fail-fast for data that must exist).
    - *Example:* the project's content registry autoload, with one lazy-loading dictionary per content axis (Identity, Category, InputAction, Attribute, …).
- **Composable Configuration Resources:**
    - **Pattern:** Create a `[GlobalClass] Resource` subclass with `[Export]` properties and behavior methods.
    - Designer-configurable via `.tres` files
### Resource Strategy Hierarchies

**Rule:** When behavior varies by authored configuration, use an abstract `[GlobalClass] Resource` base class with concrete subclasses saved as `.tres` files.

- *Shape:* Abstract base defines the contract (e.g., `abstract void Apply(...)`). Concrete subclasses implement specific behavior. Designers create `.tres` instances per variant.
- *Examples:* `DestroyStrategy`, `SpawnDirectionStrategy`, `SpawnScheme`, `EnvironmentEffect`, `HookStrategy` — plus whatever content-effect families the consuming project defines.
- *Live inventory:* the Examples above are illustrative, not complete — the generated manifest at `.claude/generated/abstraction_families.md` is the complete, current family list (regenerated by /reindex_search).

### ConditionalWeakTable for Per-Instance Caching

**Rule:** When extension methods or static helpers need per-instance mutable state for objects with dynamic lifetimes, use `ConditionalWeakTable<TKey, TValue>` instead of `Dictionary`.

- Entries are automatically removed when the key is garbage-collected.
- *Anti-pattern:* `Dictionary<Node, CachedData>` leaks entries for freed nodes unless manually cleaned up.

### Factory→Runner State Pattern

**Rule:** Shared Resources must never cache per-instance mutable state. Multiple instances sharing the same `.tres` will overwrite each other. Variant A (struct) is in `architecture_philosophy` §Factory→Runner State Pattern; the Node variant is Godot's:

| Variant | When | Pattern | Examples |
|---------|------|---------|----------|
| **B: Node** (pragmatic) | Timers, Tweens, signals, physics | `CreateRunner()` → Node. Consumer adds to tree. Runner holds internal state. | `TimerFactory` → `TimerRunner` |

**Default to Variant A.** Use B only when the runner genuinely needs Node features.

## Authored-Data Integrity

The Inspector is an API surface: exports and `.tres` fields carry the same contract discipline as public code. Scene-facing mirror (auto-loads on `.tscn`/`.tres`): `rules/scene_authoring.md` §Scene anatomy.

- **Required dependency = fail loud, never a silent no-op.** Generalizes the IComponent rule above to every system/provider/reference: absence is detected once at load/initialize with an Error or throw, never a per-use Warning (N noise lines read as N failures). Three-rung ladder (authoring warning / loud load / lint): `rules/scene_authoring.md` §Scene anatomy. *Corollary:* any `X.Instance`-style system decides its ownership seam (scene node / autoload / lazily created) at design time — a plan that introduces one names the owner.

## Lifecycle Contracts (Pooling / Cleanup)

### DestroyStrategy Contract

**Rule:** Every `DestroyStrategy` implementation MUST invoke the `onFinished` callback exactly once.

- Skipping the callback stalls the cleanup chain — the object never returns to its pool and the instance leaks.
- *Common mistake:* Early-return paths that skip the callback.
- *Testing:* Assert that `onFinished` is invoked in all code paths (success, failure, edge cases).

### IPoolResetable Convention

**Rule:** Components holding transient runtime state in pooled objects must implement `IPoolResetable.OnPoolReset()`.

- Auto-discovered by the parent entity via `GetChildrenOfInterface<IPoolResetable>()` — open for extension without modification.
- **Clear in OnPoolReset:** Event subscriptions, cached external references, tracking sets, runtime flags.
- **Do NOT reset:** `[Export]` configuration values, signal connections wired in `_Ready()`, child component state (children implement their own `IPoolResetable`).
- Pool reset restores the object to a "just-spawned" state, not a "just-constructed" state.

## Jmodot Core Tools

- **Data Structures:** Use `Map<T1, T2>` for bidirectional lookups.
- **State Management:** Use `IRuntimeCopyable<T>` for data objects that need deep copy / cloning.
- **Interfaces:** When designing new components, use `IGodotNodeInterface` or `IGodotResourceInterface` for easy reference of the underlying node/resource.
- **Exceptions:** Always throw configuration exceptions when a configuration error is encountered. Pass the actual object as the second argument: `new NodeConfigurationException("message", this)` or `new ResourceConfigurationException("message", this)`. The constructor extracts the name automatically — do not pass a string.
- **Utilities:** Leverage `JmoRng` for randomness, `JmoMath` for geometry, and the extensions in `NodeExts` and `MovementExtensions`.
- *Logging:* See `rules/csharp_patterns.md` *Core Conventions* (`JmoLogger` is the only allowed logging mechanism; rules + failure semantics live there).
- *For details on specific Jmodot utilities, see the [Jmodot Skill](../jmodot/SKILL.md).*
- *Deletion Test in C#:* deepening a shallow module often means taking an `IBlackboard` instead of 6 individual parameters (`architecture_philosophy` §The Deletion Test).

## `[Tool]` Attribute Policy

**Rule (chosen in the `Tool Attribute Audit` charter):** **Blanket `[Tool]` on every `[GlobalClass]` Resource; selective on Nodes.** A Node carries `[Tool]` only if it has editor-time code (`Engine.IsEditorHint`, `_ValidateProperty`, an editor plugin / `[ExportToolButton]`) **or** extends a framework convention Node type (Jmodot `State` / `BehaviorTask` / `BTState` / …). `[Tool]` is NOT required on every `[GlobalClass]`.

**The cascade rule (why):** if a `[Tool]` script has `[Export] TypedResource Foo` (or `Array<TypedResource>` / `Dictionary<_, TypedResource>`), then `TypedResource` AND every concrete subclass that can appear under that field MUST also be `[Tool]`. Otherwise the editor loads the instance as a bare `Godot.Resource` and the auto-generated setter throws `InvalidCastException` at load. Godot's C# source generator does NOT honor attribute inheritance — each concrete subclass needs its own `[Tool]`.

**What actually triggers it (verified empirically):** `[Tool]` gates whether a script's C# type is *instantiated in the editor at all*. A non-`[Tool]` Resource loads as a bare `Godot.Resource` in-editor **regardless of inline `[sub_resource]` vs external `[ext_resource]` reference** — so any `[Tool]` parent whose typed setter assigns it throws. External-ref does NOT avoid the cast (a tempting but false intuition). Only two things avoid it: the child being `[Tool]`, or the parent typing the field as base `Resource` (the escape hatch below). Caveat: `godot --headless --import` only fully deserializes `.tres` reachable from the import graph, so not every *latent* gap throws on import — but the ones that do are real, and a data edit can promote a latent gap to a live one at any time.

**Cost asymmetry — why blanket Resources but not Nodes:** `[Tool]` on a Resource is side-effect-free (no lifecycle; the editor only runs property setters). `[Tool]` on a Node makes the editor RUN its lifecycle (`_EnterTree`/`_Ready`/`_Process`) while a scene is open — unguarded game logic then fires in-editor (null-refs, churn, crashes). Blanket the free side (Resources), stay precise on the costly side (Nodes).

**Editor-only failure:** the cast fires in the EDITOR process; at runtime every script is its real type. **No GdUnit4 / runtime test can catch a cascade gap** — detection is static (the type graph) or headless-editor import.

**Escape hatch (typed-as-base):** type the `[Export]` as base `Resource`/`Node` and cast at runtime (`prop as ISomeInterface`). This breaks the cascade at the cost of Inspector drag-and-drop type hints. Static analysis cannot follow this path; the blanket-on-Resources policy and headless gate cover it.

**Jmodot is a black box to the consumer:** the framework blankets `[Tool]` across some families but not every Resource family. Jmodot-side gaps need a paired Jmodot-repo change, not a consumer edit. When a consumer `[Tool]` Resource must export a non-`[Tool]` Jmodot Resource, type the field as base `Resource` and cast at runtime; an external reference does not avoid the cast.

**Enforcement (three layers — the cascade is editor-only, so these replace the test that can't exist):**
- **Edit-time:** `pattern_enforcer.py` blocks writing a `[GlobalClass]` Resource without `[Tool]` and uses `tool_resource_classes.txt` to recognize indirect Resource bases.
- **Static gate:** `.claude/hooks/tool_cascade_audit.py` in `/regression_gate` builds the typed-`[Export]` graph, fails on any consumer `[GlobalClass]` Resource missing `[Tool]`, and emits `logs/tool_audit_inventory.md`. `apply_blanket_tool.py` fixes all flagged at once.
- **Headless gate:** `godot --headless --import` in `/regression_gate` (step 2b) — surfaces the actual `InvalidCastException`; catches Node, escape-hatch, and Jmodot-side gaps the static graph can't see.

**After any `[Tool]` edit, fully restart the editor** before concluding a gap is real — hot-reload can leave a stale BiMap script registration that MIMICS a cascade gap (`archive_godot_build_gotchas.md`, auto-memory).

## Data-Driven Design in Godot

The shape choice is `architecture_philosophy` §Data-Driven Design; in Godot, keys are `StringName` constants and authored data is a `Resource`:

| Feature | `enum` | `static class` `StringName` | `Resource` (.tres) |
| :--- | :--- | :--- | :--- |
| **Purpose** | Finite logic states | Keys / decoupled lookups | Authored data / database |
| **Workflow** | Finite state machines | Blackboard keys, registries | Records, policies, configuration |
| **Example** | `JobState.Idle` | `BB.ActiveJob` | `JobDefinition.tres` |
| **Use for** | FSMs, quality settings, directions | Decoupling systems; BB should not know about your enum | Extensible authored records and categories |
| **Avoid for** | Lists of content | Internal state logic | Simple boolean states |
