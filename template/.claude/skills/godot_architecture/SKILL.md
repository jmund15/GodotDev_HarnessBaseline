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

Every entity-scoped component initializes through ONE framework path: `IComponent` auto-discovery driven by `EntityNodeComponentsInitializer` (ENCI), in three phases (0 `Provision`, 1 `Initialize`, 2 `OnPostInitialize`). Bespoke `Initialize(...)` signatures are legal only in the enumerated categories. → `reference/component_initialization.md` §Component Initialization Paths — the phase contract, the required-dependency `bool` policy, auto-discovery versus `[Export]`, the justified bespoke paths and editor-time dependency visibility. Read it before writing or reviewing a component's initialization.

### Component Contract (design rules)

The initialization mechanics (`reference/component_initialization.md`) say HOW components wire; these five rules say what a well-designed component composition IS:

1. **Divergence by composition.** Per-entity variation is expressed by WHICH components/nodes an entity composes plus exported Resource data — never by structural rewiring of shared blocks.
2. **Dependencies are one-directional.** The dependent knows its dependency (hurtbox→health), never the reverse. Direct sibling-component coupling is unjustified by default; named exceptions are structural parent-child relationships (body→shape).
3. **Every inter-component dependency is COMMUNICATED, never hidden.** Exactly one of: (a) *explicit slot* — an exported node/interface reference the author must assign, so the requirement is visible in the Inspector; or (b) *auto-resolved* — the component locates its dependency itself (Blackboard/ENCI per the Discovery rule in `reference/component_initialization.md`) AND surfaces `_GetConfigurationWarnings` when it is absent AND fails loud at initialize. **House default: (b)** — it matches Blackboard DI and keeps the designer surface minimal; reserve (a) for genuinely ambiguous bindings (multiple valid candidates on one entity, per the `[Export]` exceptions there). Resolution must survive regroup — never a bare parent/sibling walk (`gotcha_godot_scene_reference_traps`).
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

Decision order (fix install order before any retry): `architecture_philosophy` §Init-Timing & Data-Source Readiness. → `reference/component_initialization.md` §Init-Timing & Data-Source Readiness — per-channel readiness, root-cause diagnostics and canonical retry patterns; read it when a read depends on when a source is ready.

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

- **Required dependency = fail loud, never a silent no-op.** Generalizes the required-dependency policy in `reference/component_initialization.md` to every system/provider/reference: absence is detected once at load/initialize with an Error or throw, never a per-use Warning (N noise lines read as N failures). Three-rung ladder (authoring warning / loud load / lint): `rules/scene_authoring.md` §Scene anatomy. *Corollary:* any `X.Instance`-style system decides its ownership seam (scene node / autoload / lazily created) at design time — a plan that introduces one names the owner.

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

Blanket `[Tool]` on every `[GlobalClass]` Resource; selective on Nodes (editor-time code or a framework convention type). → `reference/tool_attribute_policy.md` — the cascade rule, the escape hatch, enforcement layers and the restart caveat; read it before adding or removing a `[Tool]`.

## Data-Driven Design in Godot

The shape choice is `architecture_philosophy` §Data-Driven Design; in Godot, keys are `StringName` constants and authored data is a `Resource`:

| Feature | `enum` | `static class` `StringName` | `Resource` (.tres) |
| :--- | :--- | :--- | :--- |
| **Purpose** | Finite logic states | Keys / decoupled lookups | Authored data / database |
| **Workflow** | Finite state machines | Blackboard keys, registries | Records, policies, configuration |
| **Example** | `JobState.Idle` | `BB.ActiveJob` | `JobDefinition.tres` |
| **Use for** | FSMs, quality settings, directions | Decoupling systems; BB should not know about your enum | Extensible authored records and categories |
| **Avoid for** | Lists of content | Internal state logic | Simple boolean states |
