---
name: Architecture Philosophy
description: >-
  Auto-load when designing new systems, refactoring, reviewing architecture, or making
  coding-standards decisions in any codebase — dependency injection and state ownership,
  component contracts, capability queries, strategy hierarchies, authored-data integrity,
  the deletion test, or initialization-order concerns. SKIP for mechanical edits (path-scoped
  rules auto-load on file-type reads instead) and engine- or framework-specific pattern
  questions (the engine layer's architecture skill owns them).
---

# Architectural Philosophy and Design Principles

Design-time patterns. Read when proposing new systems, reviewing architecture, or making coding-standards decisions.

## Companion rule files

Mechanical patterns live in path-scoped rules under `.claude/rules/` that auto-load on matching file reads — the loader surfaces them when you touch matching files; don't load them manually or restate them here.

- [`structure_rules.md`](structure_rules.md) — *physical* file/folder layout, naming, framework boundary. Companion to this skill (NOT path-scoped — load when placing files).

## Coupling & Discovery

### Interface Usage

**Rule:** Components should interact via Interfaces, not concrete classes.

- *Bad:* `public ConcreteTarget Target;`
- *Good:* `public IDamageable Target;`

## Dependency Injection & State Ownership

### One Initialization Path

**Rule:** Every component in one scope initializes through ONE framework path: producers publish into a shared context (a blackboard, DI container or service registry), consumers resolve from it, and neither names the other. A bespoke initialization signature is a documented exception with a stated condition, never a second default.

- *Resolve, then wire:* dependency resolution and cross-component subscription are separate phases. Subscribe only after every sibling has resolved; wiring during resolution is an order-dependent race.
- *Required-dep policy:* a component fails loud at initialization, once, when a dependency is missing without which it serves no purpose. A dependency that gates only *part* of the behavior is soft: resolve it optionally, degrade, and keep running. *Litmus:* *"With this missing, does the component still do anything a user expects?"* No → hard. Yes → soft.
- *Idempotency:* initialization can run more than once; tear down before re-resolving.

### Component Contract

1. **Divergence by composition.** Per-entity variation is expressed by WHICH components an entity composes plus authored data — never by structural rewiring of shared blocks.
2. **Dependencies are one-directional.** The dependent knows its dependency, never the reverse.
3. **Every inter-component dependency is communicated, never hidden:** either an explicit slot the author must assign, or auto-resolution that warns at authoring time and fails loud at initialization. Resolution must survive regrouping, never a bare parent/sibling walk.
4. **The contract is more than the member list.** Which phase resolves what, which dependencies are hard versus soft, idempotency and teardown expectations are part of the interface; state them where the author reads the component. Introduction-time litmus: `rules/design_litmus.md` #7.

### Shared-Context Decoupling

**Rule:** Do not bypass the shared context with direct-reference calls, even for single-consumer optimizations. A direct push beside a published key parallel-wires the data through two channels, and every such shortcut is a coupling channel future code must reason about. Genuine late population uses a bounded retry with a cap and a Warning on timeout.

### Capability-Graded Consumption

**Rule:** A consumer reads the **narrowest layer that answers its question**. Raw substrate (perception, physics, stats, health) when it needs raw facts; the deriving component when it needs *that component's derived answer*. A derived layer is an **optional capability composed over the substrate**, never a mandatory funnel every consumer routes through.

Two obligations follow:

1. **The substrate stays consumable without the derived layer.** An entity may carry perception and no target-selection; steering and physics keep working. If removing the deriving component breaks substrate consumers, the layers are fused.
2. **Consumers resolve the deriving COMPONENT, not a value key it publishes.** Publish the component reference through the shared context and call typed members. Publishing the derived *value* to a shared key erases the layer distinction — every consumer then reads one untyped surface regardless of what it actually asked.

- *Why:* a single shared value key makes two different questions indistinguishable to the type system. Once "what is near me" and "what have I committed to" read the same key, no consumer can express which it wanted, the derived layer becomes non-optional by accident, and the substrate's own consumers inherit a dependency they never needed.
- *Litmus:* name the question each consumer is asking. Two consumers asking **different** questions reading **one** surface → the layers are fused; split them. Conversely, a consumer that only needs substrate facts but reads the derived layer is over-coupled — narrow it.
- *Relation to neighbouring rules:* *Marker Interface as Capability Query* (below) supplies the MECHANISM for expressing an optional layer (`x is ICapability`); this rule decides WHICH layer a given consumer should be reading in the first place. *Owner-Bound State over Shared Flags* decides whether the derived answer is owner-bound at all; apply it first — only owner-bound state reaches this rule.
- *Concrete:* an AI entity's perception manager is substrate; a target-selection component derives "the committed target" over it. Steering considerations asking "what is near me" read perception; attack actions asking "what have I committed to" read the target provider. Both reading one shared "current target" Blackboard key is the fused shape this rule rejects.

### Owner-Bound State over Shared Flags

**Rule:** When state has a clear owner whose **lifecycle bounds the state's existence**, store the state on the owner, not as a flag in the shared context. Shared-context entries suit genuinely cross-cutting data without a single owner.

- *Why:* a shared flag is public-field-equivalent: any system can read or write it at any time, and setters and clearers must be paired by hand, so a missing clearer is a silent desync. Owner-bound state is valid exactly while its owner lives.
- *Litmus:* *"Does this state have a meaningful owner whose lifetime IS the state's lifetime?"* Yes → owner-bound. No → shared context.
- *Corollary:* time-windowed metadata whose window matches a state's lifetime lives on that state; entry sets it and exit clears it, with no parallel tracker component.

### Marker Interface as Capability Query

**Rule:** When dispatching on N+ subtypes of a base type to extract a shared capability, prefer a **marker interface exposing that capability** over a pattern-match-switch over concrete subtypes. Consumers filter via `x is ICapability cap` and read `cap.Property`.

- *Why:* Open/Closed. Adding a new subtype that should participate requires editing every consumer with the switch approach; the marker-interface approach adds the capability at the subtype with zero consumer changes.
- *Litmus:* *"If a third subtype were added next month, how many existing files would need to change?"* Switch → all consumers. Marker → zero.
- *Framework example:* an `IVectorCarrier` capability can expose a direction and magnitude across several result types; an `IRestrictedState` capability can expose attribution across several state types. Consumers query the capability instead of naming each subtype.
- *NOT the default — it's the MIDDLE of a three-way choice.* Capability query is right only when the capability is **optional**, the consumer is **decoupled**, and absence means a **uniform graceful no-op**. The two neighbours it gets mistaken for:
  - **Polymorphic member** (virtual/interface method — NO `is` check): the behavior is **intrinsic to the object** and **total** — every variant must provide it. The tell is an `else` branch that is a *specific alternative behavior*, not a skip: `host is IKinematic k ? k.Reflect() : ApplyNative()` — `ApplyNative()` is real behavior, so all variants belong behind the member.
  - **Central semantic dispatch** (pattern-match switch in the consumer): variants are **data a central consumer interprets** — they don't act on themselves (e.g. `Damage`/`Heal`/`Stat`/`Status` effect application; pattern matching IS correct there).
  - *Litmus:* else-branch is a uniform skip → capability query; a specific alternative behavior → polymorphic member; "I'm a central interpreter of object-as-data" → dispatch.

## Init-Timing & Data-Source Readiness

**Rule:** When a component needs data that isn't immediately available, do NOT reach for a retry pattern first. Most late-availability issues are install-ordering bugs, not genuine timing problems. Diagnose the root cause first.

**Decision order:**

1. **Can I fix the install order?** Can the producer run before the consumer's `Initialize`? If yes, do that. Retry patterns hide ordering bugs.
2. **Is the data genuinely late-populated** (different installer, external system, async)? Only then select a pattern by data source.

**Anti-patterns:**

- **Unbounded silent polling.** Every retry MUST be bounded and every cap hit MUST log at Warning or above.

## Lifecycle Patterns

### Phased Lifecycle Methods

**Rule:** When initialization or teardown requires multiple ordered steps with dependencies between them, decompose into numbered phases with dedicated helper methods.

- Each phase has a single responsibility and explicit ordering rationale.
- *Convention:* Name phases numerically (`Phase0_RegisterSelf`, `Phase1_ResolveDeps`) or semantically.
- *Examples:* a runtime object may separate registration, dependency resolution, behavior setup, and teardown into named phases.
- *Why:* Makes ordering dependencies explicit and debuggable. A failure in Phase 2 immediately tells you that Phase 0-1 succeeded.

## Extensibility Patterns

### Default Value Pattern

**Rule:** When a component has a sensible project-wide default, resolve it through the project-owned registry while allowing an explicit per-instance override.

- **Pattern:** `InstanceOverride ?? ProjectDefaults.DefaultValue`
- **Why:** Keeps common authoring terse while preserving explicit customization.
- **Application:** Use only when a real project-wide default exists.
- **Framework boundary caveat:** a framework must not reach into the consuming project's namespace. Add a framework-owned configuration seam and let project startup populate it. The seam owns a reset path for test isolation.

### Lazy-Loading Registry Pattern

**Rule:** Registries that serve as lookup caches should expose paired `TryGet<T>(key, out T)` / `Get<T>(key)` methods.

- `Get` logs and throws on missing keys (fail-fast for data that must exist).
- `TryGet` returns `false` for graceful handling of optional lookups.
- Dictionary is lazy-built on first access, not at startup. Duplicate keys: warn-and-skip (first wins).
- *Example:* the project's content registry, with one lazy-loading dictionary per content axis (Identity, Category, InputAction, Attribute, …).

### Composable Configuration Resources

**Rule:** When configuration is shared across multiple effects/components, extract it as a standalone Resource.

- **Pattern:** Create an authored configuration type with its fields and behavior methods.
- **Example:** a `RetryPolicyConfig` can hold delay, cap, and backoff behavior shared by several consumers.
- **Benefits:**
    - Reusable across different consumer types
    - Designer-configurable as authored data files
    - Testable in isolation (logic methods can be unit tested)
- **When to Apply:** If 2+ effects need the same configuration options, extract to a shared Resource.

### Resource Strategy Hierarchies

**Rule:** When behavior varies by authored configuration, use an abstract authored-data base class with concrete subclasses saved as data files.

- *Shape:* Abstract base defines the contract (e.g., `abstract void Apply(...)`). Concrete subclasses implement specific behavior. Designers author one data-file instance per variant.
- *Composite variant:* When a single slot needs multiple strategies simultaneously, create a `Composite<Base>Strategy` that holds `Array<Base>` and iterates.
- *Anti-pattern:* Enums or switch statements for behavior that should be polymorphic Resources. If you're writing `switch (type) { case A: ... case B: ... }`, consider whether each case should be a Resource subclass instead.

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

### Factory→Runner State Pattern

**Rule:** Shared configuration objects must never cache per-instance mutable state. Multiple consumers sharing one authored asset will overwrite each other.

| Variant | When | Pattern | Examples |
|---------|------|---------|----------|
| **A: Struct** (preferred) | Pure computation, per-frame updates | `CreateState()` → struct. Consumer owns it. `Tick(state, delta)`, `Compute(input, state)`. | `SamplingProfile` → `SamplingState` |
| **B: Runtime object** (pragmatic) | Timers, events, engine-managed state | `CreateRunner()` → runtime object the consumer owns. Runner holds internal state. | `TimerFactory` → `TimerRunner` |

**Default to Variant A.** Use B only when the runner genuinely needs engine-managed features.

## Authored-Data Integrity

Authored data is an API surface: configuration fields and data files carry the same contract discipline as public code.

- **Derive, don't duplicate.** Every authored value has exactly one home. A second surface DERIVES it (computed property, resolved stat projection, lookup over the owning collection) — it never re-authors it; two hand-synced values are a silent desync waiting for their first edit. *Litmus:* changed in one place and shipped — what still reads the old value? Any answer but "nothing" → derive. *Anti-shapes:* a physical gate (sensor/collision radius) authored beside the stat that governs it; a parallel identity→asset dictionary beside the identity's own asset field; per-scene copies of one value.
- **Every visible export is read** — in every context an author can reach it. An inert export is a defect: delete it, or narrow the type per authoring context (base + subtype, each slot typed to the narrowest). Canon incident: `arch_rule_shared_config_resource_no_dead_exports`. *Litmus:* for every export reachable from this data file, name the code that reads it in THIS context. *Inherited-export litmus:* does this concrete type re-expose a base export its own runtime path bypasses?
- **One knob, one axis.** An export's name describes everything that changes when it flips. A bool silently selecting two concerns (mount point AND facing algorithm) is a trap: split it, or use an enum whose members name the fusion. A bool selecting *behavior* beside an existing `*Strategy` family is a strategy slot in disguise — wire the family.
- **Required dependency = fail loud, never a silent no-op.** Every system/provider/reference detects absence once at load/initialize with an Error or throw, never a per-use Warning (N noise lines read as N failures). *Corollary:* any `X.Instance`-style system decides its ownership seam (tree-owned object / global singleton / lazily created) at design time — a plan that introduces one names the owner.

## The Deletion Test (shallow-module diagnostic)

**Rule:** Before adding a new class / Component / State / Resource subclass / `*Helper`, ask: **if I deleted this and inlined its body at every caller, what would scatter?**

- **Deep module** — deleting it forces every caller to re-implement substantial logic, *and* the re-implementations would each need their own design decisions (which the module currently centralises). The interface earns its keep because it hides real complexity. Keep it.
- **Shallow module** — deleting it changes nothing meaningful. Each caller absorbs a one-liner. The module's interface lists nearly every parameter the implementation needs; it's a redirection layer, not an abstraction. Inline it.

**Heuristic:** if your interface signature lists every parameter the implementation needs, the module is shallow. Deep modules narrow the interface and absorb decisions internally.

**Signal:** a `*Helper` / `*Utils` / `*Service` / `*Manager` class with one or two static methods that forward most of their arguments to another class is usually shallow. Either:

1. **Inline at the call site** (delete the indirection), OR
2. **Deepen the module** — move more decisions inside, narrow the parameter list, take a context object instead of 6 individual parameters.

**Where this complements existing rules:**

- `structure_rules.md` R9 ("no single-file folders") and R10 ("no mixed concerns in a flat folder") detect shallow patterns at the *folder* level. The Deletion Test extends the diagnostic to *interface signatures* — a folder full of one-liner classes can pass R9 while still being shallow.
- The *Resource Strategy Hierarchies* and *Composable Configuration Resources* patterns above are the *positive* form of this rule: extension via deep abstract bases with concrete subclasses absorbing real configuration. The Deletion Test is the *negative* form: detecting indirections that don't earn their keep.

**When applied during diagnosis:** if the `debugging` skill's Phase 5 surfaces a "no correct seam exists" finding, the Deletion Test articulates *why* — the seam-less area is usually a chain of shallow modules that each pushed responsibility downstream until the seam dissolved. Use this vocabulary in the Worklog `arch | <description>` entry to scope the future architectural work.

## Data-Driven Design — choose the right shape

| Feature | `enum` | Constant keys | Authored data file |
| :--- | :--- | :--- | :--- |
| **Purpose** | Finite logic states | Keys / decoupled lookups | Authored data / database |
| **Workflow** | Finite state machines | Shared-context keys, registries | Records, policies, configuration |
| **Example** | `JobState.Idle` | `Keys.ActiveJob` | a `JobDefinition` data file |
| **Use for** | FSMs, quality settings, directions | Decoupling systems; the shared context should not know about your enum | Extensible authored records and categories |
| **Avoid for** | Lists of content | Internal state logic | Simple boolean states |
