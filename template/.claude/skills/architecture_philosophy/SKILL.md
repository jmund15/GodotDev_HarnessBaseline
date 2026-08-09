---
name: Architecture Philosophy
description: >-
  Auto-load when designing new systems, refactoring, reviewing architecture, or making
  coding-standards decisions — pattern choices like Blackboard-based dependency injection,
  Resource-backed strategy objects, marker interfaces, typed-owned state, the deletion
  test, or initialization-order concerns. SKIP for mechanical edits (path-scoped rules
  auto-load on file-type reads instead).
---

# Architectural Philosophy and Design Principles

Design-time patterns. Read when proposing new systems, reviewing architecture, or making coding-standards decisions.

## Companion rule files

Mechanical patterns live in path-scoped rules under `.claude/rules/` that auto-load on matching file reads (C# patterns, scene authoring, Godot data-file invariants, HSM/BT, physics, C# LSP routing) — the loader surfaces them when you touch matching files; don't load them manually or restate them here.

- [`structure_rules.md`](structure_rules.md) — *physical* file/folder layout, naming, framework boundary. Companion to this skill (NOT path-scoped — load when placing files); reviewed by `/structure_audit`.

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

**Rule:** Nodes should interact via Interfaces, not concrete classes.

- *Bad:* `public WarriorEnemy Target;`
- *Good:* `public IDamageable Target;`
- *Implementation:* Use `IGodotNodeInterface` on components to expose the underlying `Node` when passing interfaces around.
- *Adapter Conflicts:* When interface members conflict with Godot base class (e.g., `ICharacterController3D.IsOnFloor` vs `CharacterBody3D.IsOnFloor()`), use **explicit interface implementation**: `bool ICharacterController3D.IsOnFloor => _controller.IsOnFloor;`

### Semantic Targeting Over Collision Layers

**Rule:** Prefer `IIdentifiable` and `Category` filtering over collision layer masks for targeting systems.

- *Why:* Semantic targeting is more flexible, self-documenting, and decoupled from physics configuration.
- *Pattern:* Query with `uint.MaxValue` collision mask (all layers), then filter by `IIdentifiable.GetIdentity().Categories`.
- *Example:* `TargetingCapability` filters targets by Category ("Wizard", "Entity") rather than checking collision layers.
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
| 0 | `IBlackboardProvider` — *independently* of `IComponent` | reads `Provision` → `bb.Set` | Publishes component refs and lazy POCOs. **`Provision` MUST be idempotent** — it is evaluated more than once per entity lifetime (spell scenes run Phase 0 at `_Ready` and again at `Initialize`; pool reuse re-runs it; the retraction step below re-reads it), so cache lazy payloads on a field (`_x ??= new()`). A publisher may implement only this property; `IComponent` on top of it is ceremony when `Initialize` just sets `IsInitialized` and `OnPostInitialize` is empty — acceptable, but not the shape to copy. A component that provisions AND resolves real dependencies is the opposite case: it earns both. Reusable outside ENCI via `EntityNodeComponentsInitializer.RunPhase0(Node entity, IBlackboard bb)` (spell/beam bootstrap paths call it). Two providers on one key is a scene-authoring DEFECT, never intentional — last-writer-wins makes the entity silently inert and ENCI currently only Warns. Fix the scene: override the template node in place; never add a sibling with the same role. |
| 1 | `IComponent` | `Initialize(bb)` | Resolve dependencies only. Unordered — **must not** subscribe to sibling component events and must not assume any sibling is initialized. |
| 2 | components that returned `true` | `OnPostInitialize()` | Post-barrier: all siblings are initialized. **Subscriptions belong here**, and must be idempotent — ENCI calls this unconditionally for every component that returned `true`, so a second init pass re-subscribes. Carve-out: an `[Export]`-resolved dependency is a scene-load-time reference not subject to the ordering race, so subscribing to it in `_Ready` is correct (canonical: `VisualEffectController.Composer`). |

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
| Spell-pipeline runners / bodies | Parameters are computed per cast; there is no pre-cast entity BB for them to live on | spell behavior runners, spell physics bodies |
| Programmatic environment helpers | Constructed in code, never scene-authored under an entity root | `Jmodot/Implementation/Environment/CentralPullForceArea.cs`, `VelocityDragForceArea.cs` |
| Run/floor-scope systems | Scope is an `IBlackboardGraph`, not one entity's BB | run/session-scope controllers and runtimes |
| Service-injection interactables | The dependency is a session/run service pushed by an installer, not an entity sibling | interactables wired by a service installer |

**Editor-time dependency visibility.** A component whose hard dependency is a *sibling node* should surface the gap in the editor via `_GetConfigurationWarnings()`. Use `ConfigWarnings.RequireEntitySibling<T>(this, message)` rather than hand-rolling `GetParent()` + a child scan — a bare parent check false-warns on any component nested below the entity root, which ENCI's descendant walk resolves fine. Concat `base._GetConfigurationWarnings() ?? []` so a future base's warnings are not swallowed. Godot only displays warnings from `[Tool]` scripts — so such a component takes `[Tool]` plus `Engine.IsEditorHint` early-returns in every lifecycle hook running game logic (`_Ready`, `_PhysicsProcess`, `ValidateRequiredExports`). This is exactly the selective-on-Nodes case in the *`[Tool]` Attribute Policy* below (editor-time code) — no policy exception. **A non-`[Tool]` `_GetConfigurationWarnings` override is dead code**; either promote the script or delete the override.

### Component Contract (design rules)

The initialization mechanics above say HOW components wire; these five rules say what a well-designed component composition IS:

1. **Divergence by composition.** Per-entity variation is expressed by WHICH components/nodes an entity composes plus exported Resource data — never by structural rewiring of shared blocks.
2. **Dependencies are one-directional.** The dependent knows its dependency (hurtbox→health), never the reverse. Direct sibling-component coupling is unjustified by default; named exceptions are structural parent-child relationships (body→shape).
3. **Every inter-component dependency is COMMUNICATED, never hidden.** Exactly one of: (a) *explicit slot* — an exported node/interface reference the author must assign, so the requirement is visible in the Inspector; or (b) *auto-resolved* — the component locates its dependency itself (Blackboard/ENCI per the Discovery rule above) AND surfaces `_GetConfigurationWarnings` when it is absent AND fails loud at initialize. **House default: (b)** — it matches Blackboard DI and keeps the designer surface minimal; reserve (a) for genuinely ambiguous bindings (multiple valid candidates on one entity, per the `[Export]` exceptions above). Resolution must survive regroup — never a bare parent/sibling walk (`gotcha_godot_scene_reference_traps`).
4. **The contract is written where the author stands.** The dependency and its resolution mode are stated in the component's `<summary>` (surfaced in the Inspector once the tooltips plugin lands).
5. **The contract is more than the member list.** A component's interface is its signatures PLUS the facts a signature cannot carry: which phase resolves what and what may not be touched before the Phase-1 barrier, which dependencies are hard (loud `false`) versus soft, the idempotency obligation on `Provision`/`OnPostInitialize`, and teardown expectations. Every one of those is a shipped defect class here — specify them at design time, in the `<summary>` per rule 4, or the seam is undesigned. Introduction-time litmus: `rules/design_litmus.md` #7.

Scene-facing mirror: `rules/scene_authoring.md` §Scene anatomy.

### Capability-Graded Consumption

**Rule:** A consumer reads the **narrowest layer that answers its question**. Raw substrate (perception, physics, stats, health) when it needs raw facts; the deriving component when it needs *that component's derived answer*. A derived layer is an **optional capability composed over the substrate**, never a mandatory funnel every consumer routes through.

Two obligations follow:

1. **The substrate stays consumable without the derived layer.** An entity may carry perception and no target-selection; steering and physics keep working. If removing the deriving component breaks substrate consumers, the layers are fused.
2. **Consumers resolve the deriving COMPONENT, not a value key it publishes.** Publish the component reference (Phase-0 `Provision`, resolved via `bb.TryGet<ICapability>`) and call typed members. Publishing the derived *value* to a Blackboard key erases the layer distinction — every consumer then reads one untyped surface regardless of what it actually asked.

- *Why:* a single shared value key makes two different questions indistinguishable to the type system. Once "what is near me" and "what have I committed to" read the same key, no consumer can express which it wanted, the derived layer becomes non-optional by accident, and the substrate's own consumers inherit a dependency they never needed.
- *Litmus:* name the question each consumer is asking. Two consumers asking **different** questions reading **one** surface → the layers are fused; split them. Conversely, a consumer that only needs substrate facts but reads the derived layer is over-coupled — narrow it.
- *Relation to neighbouring rules:* *Marker Interface as Capability Query* (below) supplies the MECHANISM for expressing an optional layer (`x is ICapability`); this rule decides WHICH layer a given consumer should be reading in the first place. *Typed-Owned State over Blackboard Flags* decides whether the derived answer is owner-bound at all; apply it first — only owner-bound state reaches this rule.
- *Concrete:* an AI entity's perception manager is substrate; a target-selection component derives "the committed target" over it. Steering considerations asking "what is near me" read perception; attack actions asking "what have I committed to" read the target provider. Both reading one shared "current target" Blackboard key is the fused shape this rule rejects.

### Blackboard Decoupling Principle

**Rule:** Do NOT bypass the Blackboard with direct-reference calls, even for single-consumer optimizations. The BB exists specifically so producers and consumers don't need direct references — producer `.Set()`s a key, consumer `.TryGet()`s it, they stay mutually ignorant.

- *Anti-pattern:* Installer calls `component.AttachThing(thing)` directly "for efficiency" after setting `BB.Set(key, thing)`. Parallel-wires data through a direct channel that duplicates BB's job. Creates inconsistency (why does X use BB but Y use direct?) and doesn't scale (every new installer must enumerate dependent components).
- *Correct pattern for late-population:* When a consumer needs a key that isn't yet on BB at Initialize time, use BB-mediated bounded-retry (polling with cap + Warning log on timeout). Preserves decoupling. Canonical example: `Crafting/IngredientCollectorComponent.cs` deferred-attach.
- *Why this matters:* Every direct-push shortcut is a coupling channel future code must reason about. The BB is the decoupling layer; bypassing it defeats its purpose.

### Typed-Owned State over Blackboard Flags

**Rule:** When state has a clear owner whose **lifecycle bounds the state's existence**, store the state on the owner — not as a `BB.IsXxx` flag. BB flags are appropriate for genuinely cross-cutting data without a single owner (e.g., `BB.CharacterController` reference, `BB.Stats`); they are inappropriate for state with a natural owner (e.g., "is this entity in control loss right now").

- *Why:* A BB flag is public-field-equivalent — any system can read or write it at any time, no scope guarantees, no lifecycle hooks. Setters and clearers must be paired by hand; missing a clearer produces silent desync. Owner-bound state (e.g., a HSM state that exists IFF the entity is in that state) has compile-time guarantees: state lifetime IS the data's validity window.
- *Litmus:* *"Does this state have a meaningful owner whose lifetime IS the state's lifetime?"* Yes → owner-bound. No (genuinely cross-cutting, no natural owner) → BB.
- *Corollary — state-bound attribution:* when chain-attribution data (or any time-windowed metadata) has a bounded window matching a state's lifetime, store it on the state, not in a parallel tracker component. State entry sets it; state exit clears it. No separate "tracker" component with parallel set/clear discipline.
- *Concrete (Jmodot):* `LaunchedState.AttributedSource` (impulse-launch chain attribution) — state lifetime IS attribution lifetime; no `ImpulseAttributionTracker` component. `IControlLossState` capability query (per *Marker Interface as Capability Query* below) replaces ad-hoc `BB.IsLaunched`/`BB.IsStunned`/`BB.IsCaptured` flags.

### Marker Interface as Capability Query

**Rule:** When dispatching on N+ subtypes of a base type to extract a shared capability, prefer a **marker interface exposing that capability** over a pattern-match-switch over concrete subtypes. Consumers filter via `x is ICapability cap` and read `cap.Property`.

- *Why:* Open/Closed. Adding a new subtype that should participate (e.g., a future `ExplosionResult` carrying force, a future `RagdollState` representing control loss) requires editing every consumer with the switch approach; the marker-interface approach is a one-line `: ICapability` addition with zero consumer changes.
- *Litmus:* *"If a third subtype were added next month, how many existing files would need to change?"* Switch → all consumers. Marker → zero.
- *Concrete (Jmodot):* `IForceCarrier { Vector3 Direction; float Force; }` implemented by `DamageResult` + `KnockbackResult`; force receivers filter via `result is IForceCarrier c && c.Force > 0`. Symmetric: `IControlLossState { Node? AttributedSource; }` implemented by `CapturedState`, `LaunchedState`, future stun/knockdown states; AI/BT/spell systems query via `bb.StateMachine.ActiveLeafState is IControlLossState`. Both surfaced from the 2026-05-04 Wind Blast brainstorm.
- *NOT the default — it's the MIDDLE of a three-way choice.* Capability query is right only when the capability is **optional**, the consumer is **decoupled**, and absence → a **uniform graceful no-op** (e.g. {{PROJECT_NAME}}'s spell-capability system: a `SpellEffect` calls `spell.GetCapability<ITargeting>()`; a body lacking it — `BeamScene.GetCapability` returns `default` for all — makes the effect skip). The two neighbours it gets mistaken for:
  - **Polymorphic member** (virtual/interface method — NO `is` check): the behavior is **intrinsic to the object** and **total** — every variant must provide it. The tell is an `else` branch that is a *specific alternative behavior*, not a skip: `host is IKinematic k ? k.Reflect() : ApplyNative()` — `ApplyNative()` is real behavior, so all variants belong behind the member (e.g. a collision host enacting its own physics: kinematic velocity-reflect vs RigidBody Jolt-defer vs beam ray-reflect).
  - **Central semantic dispatch** (pattern-match switch in the consumer): variants are **data a central consumer interprets** — they don't act on themselves (e.g. `Damage`/`Heal`/`Stat`/`Status` effect application; pattern matching IS correct there).
  - *Litmus:* else-branch is a uniform skip → capability query; a specific alternative behavior → polymorphic member; "I'm a central interpreter of object-as-data" → dispatch.

## Init-Timing & Data-Source Readiness

**Rule:** When a component needs data that isn't immediately available, do NOT reach for a retry pattern first. Most late-availability issues are install-ordering bugs, not genuine timing problems. Diagnose the root cause first.

**Decision order:**

1. **Can I fix the install order?** Can the producer run before the consumer's `Initialize`? If yes, do that. Retry patterns hide ordering bugs.
2. **Is the data genuinely late-populated** (different installer, external system, async)? Only then select a pattern by data source.

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

- **Unbounded silent polling.** Every retry MUST be bounded (counter + cap) AND every cap-hit MUST log (Warning at minimum). Historical offenders: pre-2026-04-19 `IngredientCollectorComponent`, `NavigationServer3D` nav-map waits.
- **Direct push when BB mediation exists.** Parallel-wires data through two channels. See *Blackboard Decoupling Principle*.
- **Registry / autoload access in constructor.** Godot native side not ready yet.

**Canonical in-codebase examples:**

- BB late-population bounded retry: `Crafting/IngredientCollectorComponent.cs` `_PhysicsProcess` (2026-04-19) — match-level installer genuinely can't write per-entity BB key at Wizard `_Ready` time. (Path matters: an unrelated `NPCs/AI/IngredientCollectorComponent.cs` shares the class name.)
- Physics broadphase bounded retry: `HitboxComponent3D._pendingOverlapRetries` (small cap, const `PendingOverlapRetryFrames`) — silent miss acceptable when "miss" manifests as a missed hit, not missed state.
- Known single-frame `CallDeferred`: `MatchController.PostSpawnSetup`, `CraftingInstaller.Install` (docblock).
- `SetDeferred` property sync await: spell pool activation (`archive_pooling_spawn_sibling_gotchas.md`, auto-memory).

## Lifecycle Patterns

### Phased Lifecycle Methods

**Rule:** When initialization or teardown requires multiple ordered steps with dependencies between them, decompose into numbered phases with dedicated helper methods.

- Each phase has a single responsibility and explicit ordering rationale.
- *Convention:* Name phases numerically (`Phase0_RegisterSelf`, `Phase1_ResolveDeps`) or semantically.
- *Examples:* `SpellBehavior` (9-phase Init, 6-phase Destroy), `EntityBootstrapper` (5-phase init).
- *Why:* Makes ordering dependencies explicit and debuggable. A failure in Phase 2 immediately tells you that Phase 0-1 succeeded.

**Phase-2 visibility gotcha (SpellBehavior):** `HealthDamageCouplingEffect.OnInitialize` runs at SpellBehavior Phase 2 and can only see `DamageEffect`s already in `Behavior.BaseCombatEffects`. DamageEffects added later (by other SpellEffect.OnCast hooks, trait-tier effects, or runtime composition) are NOT visible to HDC and won't be coupled to health. By design — HDC owns a snapshot, not a subscription — but trait-injected DamageEffects must either land in `BaseCombatEffects` ahead of HDC's Initialize or be wired through a separate scaling path. Concrete invariant: if a coupling target is added after Phase 2, the addition is silent and unscaled.

### Static Bootstrapper Pattern

**Rule:** When multiple Node types need identical initialization but cannot share a base class (C# single-inheritance + different Godot physics body types), extract shared logic into a static bootstrapper.

- *Pattern:* `DomainBootstrapper.Initialize(Node target, ...)` — takes the root node as parameter.
- *Example:* `EntityBootstrapper` handles init for `CharacterBody3D`, `RigidBody3D`, and `StaticBody3D` environment entities.
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
- *`[RequiredExport]` autoloads need a `.tscn` wrapper:* a Node autoload with an Inspector-wired `[Export]` cannot be a bare `.cs` autoload (no scene to hold the export value). Register a `<name>.tscn` (node + script + wired exports) as the autoload instead — see `overlay_stack.tscn`, `transition_orchestrator.tscn`, `settings_repository.tscn`.
- *Test isolation:* Include `internal static void ResetForTesting()` — autoloads persist across test cases. Without it, state leaks between tests.
- *Examples:* `GlobalRegistry`, `EventBus`, `PlayerRegistry`, `SpellPoolManager`, `SpellCollisionCoordinator`.

## Extensibility Patterns

### Default Value Pattern

**Rule:** When designing configurable components, default to the global registry for quick iteration, allow override for modular customization.

- **Pattern:** `ConfigOverride ?? GlobalRegistry.DB.DefaultAttribute`
- **Example:** `var sizeAttr = EffectSizeOverride ?? GlobalRegistry.DB.ProjectileSizeAttr;`
- **Why:** Enables rapid prototyping (no configuration needed) while preserving flexibility for special cases.
- **Application:** Use this pattern for any attribute/stat that has a sensible project-wide default but may need per-instance customization.
- **Framework boundary caveat:** This pattern applies INSIDE the consuming project only. Inside Jmodot (framework) it inverts — Jmodot code MUST NOT reach into `{{PROJECT_NAME}}.Global.*`. Instead introduce a framework-agnostic static seam class in `Jmodot.Core.*` (example: `CombatFactoryDefaults` with nullable static fields) and have the game's autoload forward values into it at `_EnterTree`. The seam owns its own `Reset()` for test isolation so Jmodot-only tests don't depend on the consuming project's reset path.

### Lazy-Loading Registry Pattern

**Rule:** Registries that serve as lookup caches should expose paired `TryGet<T>(key, out T)` / `Get<T>(key)` methods.

- `Get` throws via `JmoLogger.LogAndRethrow` on missing keys (fail-fast for data that must exist).
- `TryGet` returns `false` for graceful handling of optional lookups.
- Dictionary is lazy-built on first access, not at startup. Duplicate keys: warn-and-skip (first wins).
- *Example:* the project's content registry autoload, with one lazy-loading dictionary per content axis (Identity, Category, InputAction, Attribute, …).

### ConditionalWeakTable for Per-Instance Caching

**Rule:** When extension methods or static helpers need per-instance mutable state for objects with dynamic lifetimes (spells, enemies), use `ConditionalWeakTable<TKey, TValue>` instead of `Dictionary`.

- Entries are automatically removed when the key is garbage-collected — no memory leaks.
- *Anti-pattern:* `Dictionary<ISpell, CachedData>` leaks entries for freed spells unless manually cleaned up.
- *Example:* `SpellExtensions` uses `ConditionalWeakTable<ISpell, EffectSnapshotCache>`.

### Composable Configuration Resources

**Rule:** When configuration is shared across multiple effects/components, extract it as a standalone Resource.

- **Pattern:** Create a `[GlobalClass] Resource` subclass with `[Export]` properties and behavior methods.
- **Example:** `SiblingCollisionConfig` encapsulates collision mode + grace period + `ApplyCollisionExceptions()` method.
- **Benefits:**
    - Reusable across different effect types (SpawnEffect, MultiShotEffect)
    - Designer-configurable via `.tres` files
    - Testable in isolation (logic methods can be unit tested)
- **When to Apply:** If 2+ effects need the same configuration options, extract to a shared Resource.

### Resource Strategy Hierarchies

**Rule:** When behavior varies by configuration, use an abstract `[GlobalClass] Resource` base class with concrete subclasses saved as `.tres` files. This is the project's **dominant extensibility pattern** (10+ hierarchies).

- *Shape:* Abstract base defines the contract (e.g., `abstract void Apply(...)`). Concrete subclasses implement specific behavior. Designers create `.tres` instances per variant.
- *Composite variant:* When a single slot needs multiple strategies simultaneously, create a `Composite<Base>Strategy` that holds `Array<Base>` and iterates.
- *Anti-pattern:* Enums or switch statements for behavior that should be polymorphic Resources. If you're writing `switch (type) { case A: ... case B: ... }`, consider whether each case should be a Resource subclass instead.
- *Examples:* `DestroyStrategy`, `SpawnDirectionStrategy`, `SpawnScheme`, `EnvironmentEffect`, `HookStrategy` — plus whatever content-effect families the consuming project defines.
- *Live inventory:* the Examples above are illustrative, not complete — the generated manifest at `.claude/generated/abstraction_families.md` is the complete, current family list (regenerated by /reindex_search).

### Orthogonal axis → composition, not a rung

**Rule:** Before adding a subclass, name the axis the base hierarchy already varies on. If the new behavior varies on a DIFFERENT axis, it is an optional composable Resource/config slot on the base (precedent: an optional spread-config Resource hanging off a runner base) — not an inheritance rung. Rungs that fuse orthogonal axes force a leaf class per combination (canonical violation shape: four rungs each fusing has-hitbox / has-variants / is-anchored — axes that don't nest).

- *Litmus:* could a sibling that lacks the base's defining feature still want this behavior? Yes → compose.
- *Second litmus:* would a new combination of existing behaviors require a new leaf class? Yes → the axes are already fused; unwind before extending.
- Applies to bolt-on inheritors too: two base classes each growing a subclass for the same concept (e.g. resistance-scaling added independently to two sibling runner bases) is the same defect — the concept is one composable config on the shared base.

### Closed-abstraction: refactor over fork

**Rule:** When a new feature can't reuse a core abstraction because it's *closed* (enum/switch dispatch, a fixed-stage pipeline, a framework enum the consumer can't extend), the non-extensibility is a signal to **refactor the abstraction into an extensible/data-driven form** — making it the reusable default — NOT to build a parallel self-contained system beside it.

- *Gate:* the abstraction must be *intended* as the canonical mechanism for that class of behavior. If it isn't, a parallel type may be correct.
- *Scope:* the refactor's blast radius is usually owned by an `/architecture_brainstorm`, not the feature that exposed the gap — split the feature so it ships on a stable seam and isn't blocked by the refactor.
- *Litmus:* "Is this abstraction supposed to be how we always do X?" Yes → refactor it to fit. No → parallel may be fine.
- *Distinction:* this is the third path beyond "extend the family" (works when the abstraction is already open) and "fork a parallel type" (the anti-pattern) — it applies precisely when extension is blocked by closedness.

### Reaction & Status Responsibility Boundaries

**Rule:** Two-axis division of post-impact effect logic. **Statuses** own *state on the affected entity* (tags + stat modifiers + lifecycle hooks bounded by their own duration). **Reactions** own *interactions between two specific elements / situations* ("X meets Y" → consequence). Choosing the wrong axis silently scatters logic — a "status that fires on collision with fire" smells like a reaction; a "reaction that lingers for 5s" smells like a status.

- *Stacking semantics on the new path:* when multiple Reactions match the same event, **all matches fire** and any numeric multipliers compose **multiplicatively** (not additively, not first-match-wins). A trait granting 2x burn damage + a synergy granting 1.5x burn damage produces 3x, not 2.5x and not 2x.
- *Collision responses as stat-driven dispatch:* prefer a `StatChainResponse` shape over per-response hardcoded behavior. Archetypes declare the **possibility space** of responses (which response types CAN fire); traits select WHICH responses fire via **stat amps** (a trait amping `BurnChance` from 0 to 1 enables the burn response). Data-flow is archetype → declared space → trait amps → realised dispatch. Adding a new response type requires authoring the response Resource + amping its enabling stat in the relevant trait, not editing the archetype's switch statement.
- *Source:* 2026-04-30 Icicle plan derivations.

### Factory→Runner State Pattern

**Rule:** Shared Resources must never cache per-instance mutable state. Multiple instances sharing the same `.tres` will overwrite each other.

| Variant | When | Pattern | Examples |
|---------|------|---------|----------|
| **A: Struct** (preferred) | Pure computation, per-frame updates | `CreateState()` → struct. Consumer owns it. `Tick(state, delta)`, `Compute(input, state)`. | `AnimSpeedProfile`→`AnimSpeedState`, `SpellRotationProfile`→`RotationState` |
| **B: Node** (pragmatic) | Timers, Tweens, signals, physics | `CreateRunner()` → Node. Consumer adds to tree. Runner holds internal state. | `LifetimeFactory`→`LifetimeRunner`, `CollisionFactory`→`CollisionRunner` |

**Default to Variant A.** Use B only when the runner genuinely needs Node features.

## Authored-Data Integrity

The Inspector is an API surface: exports and `.tres` fields carry the same contract discipline as public code. Scene-facing mirror (auto-loads on `.tscn`/`.tres`): `rules/scene_authoring.md` §Scene anatomy.

- **Derive, don't duplicate.** Every authored value has exactly one home. A second surface DERIVES it (computed property, resolved stat projection, lookup over the owning collection) — it never re-authors it; two hand-synced values are a silent desync waiting for their first edit. *Litmus:* changed in one place and shipped — what still reads the old value? Any answer but "nothing" → derive. *Anti-shapes:* a physical gate (sensor/collision radius) authored beside the stat that governs it; a parallel identity→asset dictionary beside the identity's own asset field; per-scene copies of one value.
- **Every visible export is read** — in every context an author can reach it. An inert export is a defect: delete it, or narrow the type per authoring context (base + subtype, each slot typed to the narrowest). Canon incident: `arch_rule_shared_config_resource_no_dead_exports`. *Litmus:* for every export reachable from this `.tres`, name the code that reads it in THIS context. *Inherited-export litmus:* does this concrete type re-expose a base export its own runtime path bypasses?
- **One knob, one axis.** An export's name describes everything that changes when it flips. A bool silently selecting two concerns (mount point AND facing algorithm) is a trap: split it, or use an enum whose members name the fusion. A bool selecting *behavior* beside an existing `*Strategy` family is a strategy slot in disguise — wire the family.
- **Required dependency = fail loud, never a silent no-op.** Generalizes the IComponent rule above to every system/provider/reference: absence is detected once at load/initialize with an Error or throw, never a per-use Warning (N noise lines read as N failures). Three-rung ladder (authoring warning / loud load / lint): `rules/scene_authoring.md` §Scene anatomy. *Corollary:* any `X.Instance`-style system decides its ownership seam (scene node / autoload / lazily created) at design time — a plan that introduces one names the owner.

## Lifecycle Contracts (Pooling / Cleanup)

### DestroyStrategy Contract

**Rule:** Every `DestroyStrategy` implementation MUST invoke the `onFinished` callback exactly once.

- Skipping the callback stalls the cleanup chain — the spell never returns to pool and the instance leaks.
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

## The Deletion Test (shallow-module diagnostic)

**Rule:** Before adding a new class / Component / State / Resource subclass / `*Helper`, ask: **if I deleted this and inlined its body at every caller, what would scatter?**

- **Deep module** — deleting it forces every caller to re-implement substantial logic, *and* the re-implementations would each need their own design decisions (which the module currently centralises). The interface earns its keep because it hides real complexity. Keep it.
- **Shallow module** — deleting it changes nothing meaningful. Each caller absorbs a one-liner. The module's interface lists nearly every parameter the implementation needs; it's a redirection layer, not an abstraction. Inline it.

**Heuristic:** if your interface signature lists every parameter the implementation needs, the module is shallow. Deep modules narrow the interface and absorb decisions internally.

**{{PROJECT_NAME}}-specific signal:** a `*Helper` / `*Utils` / `*Service` / `*Manager` class with one or two static methods that each forward 90%+ of their arguments to a different class is almost always shallow. Either:

1. **Inline at the call site** (delete the indirection), OR
2. **Deepen the module** — move more decisions inside, narrow the parameter list, take an `IBlackboard` / context object instead of 6 individual parameters.

**Where this complements existing rules:**

- `structure_rules.md` R9 ("no single-file folders") and R10 ("no mixed concerns in a flat folder") detect shallow patterns at the *folder* level. The Deletion Test extends the diagnostic to *interface signatures* — a folder full of one-liner classes can pass R9 while still being shallow.
- The *Resource Strategy Hierarchies* and *Composable Configuration Resources* patterns above are the *positive* form of this rule: extension via deep abstract bases with concrete subclasses absorbing real configuration. The Deletion Test is the *negative* form: detecting indirections that don't earn their keep.

**When applied during diagnosis:** if the `debugging` skill's Phase 5 surfaces a "no correct seam exists" finding, the Deletion Test articulates *why* — the seam-less area is usually a chain of shallow modules that each pushed responsibility downstream until the seam dissolved. Use this vocabulary in the Worklog `arch | <description>` entry to scope the future architectural work.

## `[Tool]` Attribute Policy

**Rule (chosen in the `Tool Attribute Audit` charter):** **Blanket `[Tool]` on every `[GlobalClass]` Resource; selective on Nodes.** A Node carries `[Tool]` only if it has editor-time code (`Engine.IsEditorHint`, `_ValidateProperty`, an editor plugin / `[ExportToolButton]`) **or** extends a framework convention Node type (Jmodot `State` / `BehaviorTask` / `BTState` / …). `[Tool]` is NOT required on every `[GlobalClass]`.

**The cascade rule (why):** if a `[Tool]` script has `[Export] TypedResource Foo` (or `Array<TypedResource>` / `Dictionary<_, TypedResource>`), then `TypedResource` AND every concrete subclass that can appear under that field MUST also be `[Tool]`. Otherwise the editor loads the instance as a bare `Godot.Resource` and the auto-generated setter throws `InvalidCastException` at load. Godot's C# source generator does NOT honor attribute inheritance — each concrete subclass needs its own `[Tool]`.

**What actually triggers it (verified empirically):** `[Tool]` gates whether a script's C# type is *instantiated in the editor at all*. A non-`[Tool]` Resource loads as a bare `Godot.Resource` in-editor **regardless of inline `[sub_resource]` vs external `[ext_resource]` reference** — so any `[Tool]` parent whose typed setter assigns it throws. External-ref does NOT avoid the cast (a tempting but false intuition). Only two things avoid it: the child being `[Tool]`, or the parent typing the field as base `Resource` (the escape hatch below). Caveat: `godot --headless --import` only fully deserializes `.tres` reachable from the import graph, so not every *latent* gap throws on import — but the ones that do are real, and a data edit can promote a latent gap to a live one at any time.

**Cost asymmetry — why blanket Resources but not Nodes:** `[Tool]` on a Resource is side-effect-free (no lifecycle; the editor only runs property setters). `[Tool]` on a Node makes the editor RUN its lifecycle (`_EnterTree`/`_Ready`/`_Process`) while a scene is open — unguarded game logic then fires in-editor (null-refs, churn, crashes). Blanket the free side (Resources), stay precise on the costly side (Nodes).

**Editor-only failure:** the cast fires in the EDITOR process; at runtime every script is its real type. **No GdUnit4 / runtime test can catch a cascade gap** — detection is static (the type graph) or headless-editor import.

**Escape hatch (typed-as-base):** type the `[Export]` as base `Resource`/`Node` and cast at runtime (`prop as ISomeInterface`). Breaks the cascade at the cost of Inspector drag-drop type hints. Example: `StatusPlayerEffect.Factory` (`SpellArchitecture/PlayerEffects/StatusPlayerEffect.cs` — `[Export] Resource`, runtime-cast to `CombatEffectFactory`; full rationale in the *black-box* paragraph below). Static analysis can't follow this — the blanket-on-Resources policy + headless gate cover it.

**Jmodot is black-box (submodule):** the framework blankets `[Tool]` across its AI families but NOT everywhere (e.g. the `CombatEffectFactory` family is `[GlobalClass]` without `[Tool]`). Jmodot is a git submodule — its `[Tool]` gaps need a paired Jmodot-repo PR, not a {{PROJECT_NAME}} edit. When a {{PROJECT_NAME}} `[Tool]` Resource must `[Export]` a non-`[Tool]` Jmodot Resource, apply the **escape hatch** — type the field as base `Resource` and cast at runtime (external-ref does NOT help). Precedent: `StatusPlayerEffect.Factory` is typed `Resource` and cast to `CombatEffectFactory` at its use site, so the `[Tool]` setter never casts the (non-`[Tool]`) Jmodot `TickEffectFactory` it holds.

**Enforcement (three layers — the cascade is editor-only, so these replace the test that can't exist):**
- **Edit-time:** `pattern_enforcer.py` blocks writing a `[GlobalClass]` Resource without `[Tool]` (uses the `tool_resource_classes.txt` allowlist to recognize indirect Resource bases like `: SpellEffect`).
- **Static gate:** `.claude/hooks/tool_cascade_audit.py` in `/regression_gate` (step 1c) — builds the typed-`[Export]` graph, fails on any {{PROJECT_NAME}} `[GlobalClass]` Resource missing `[Tool]`; emits `logs/tool_audit_inventory.md`. `apply_blanket_tool.py` fixes all flagged at once.
- **Headless gate:** `godot --headless --import` in `/regression_gate` (step 2b) — surfaces the actual `InvalidCastException`; catches Node, escape-hatch, and Jmodot-side gaps the static graph can't see.

**After any `[Tool]` edit, fully restart the editor** before concluding a gap is real — hot-reload can leave a stale BiMap script registration that MIMICS a cascade gap (`archive_godot_build_gotchas.md`, auto-memory).

## Data-Driven Design — choose the right shape

| Feature | `enum` | `static class` `StringName` | `Resource` (.tres) |
| :--- | :--- | :--- | :--- |
| **Purpose** | Finite logic states | Keys / decoupled lookups | Game content / database |
| **Workflow** | Finite state machines | Blackboard keys, registries | Items, spells, stats |
| **Example** | `PlayerState.Idle` | `BB.CurrentTarget` | `Fireball.tres` |
| **Use for** | FSMs, quality settings, directions | Decoupling systems; BB shouldn't know about your enum | Items, archetypes, categories, spells |
| **Avoid for** | Lists of content | Internal state logic | Simple boolean states |
