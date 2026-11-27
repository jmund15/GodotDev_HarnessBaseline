# Component initialization and init-timing

Read from SKILL.md §Component Initialization Paths and §Init-Timing & Data-Source Readiness when writing or reviewing an `IComponent`, an `IBlackboardProvider`, a hand-wired `Initialize`, or a timing-dependent read.

## Component Initialization Paths

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

**Editor-time dependency visibility.** A component whose hard dependency is a *sibling node* should surface the gap in the editor via `_GetConfigurationWarnings()`. Use `ConfigWarnings.RequireEntitySibling<T>(this, message)` rather than hand-rolling `GetParent()` + a child scan — a bare parent check false-warns on any component nested below the entity root, which ENCI's descendant walk resolves fine. Concat `base._GetConfigurationWarnings() ?? []` so a future base's warnings are not swallowed. Godot only displays warnings from `[Tool]` scripts — so such a component takes `[Tool]` plus `Engine.IsEditorHint` early-returns in every lifecycle hook running game logic (`_Ready`, `_PhysicsProcess`, `ValidateRequiredExports`). This is exactly the selective-on-Nodes case in the [`[Tool]` Attribute Policy](tool_attribute_policy.md) (editor-time code) — no policy exception. **A non-`[Tool]` `_GetConfigurationWarnings` override is dead code**; either promote the script or delete the override.

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

- **Direct push when BB mediation exists.** Parallel-wires data through two channels. See `SKILL.md` §Blackboard Decoupling Principle.
- **Registry / autoload access in constructor.** Godot native side not ready yet.

**Canonical patterns:**

- Blackboard late population: bounded retry only when install order cannot make the value ready.
- Physics broadphase: a small bounded retry may be valid because monitoring changes take a few frames.
- `CallDeferred`: use for a known one-frame scene-tree ordering boundary.
- `SetDeferred`: await the documented number of process frames before reading the property.
