---
name: Typed component property over BB-bool flag soup
description: For cross-system polled state, prefer typed component properties + dedicated typed condition classes over multiplying BB-bool flags or pattern-matching CurrentState.
type: feedback
originSessionId: 2026-04-24-craft-rework
---

When transition guards or cross-system code need to query system state, prefer **typed component property + dedicated typed condition class** over a multiplication of BB-bool flags.

**Why:** Said explicitly during the craft-rework session when I was about to add 4 BB-bool flags (`IsCraftingMenuOpen`, `WantsToCommit`, `CraftInterrupted`, `EvasionActive`) to drive CraftSM transitions:
> "All of these seemingly random blackboard bool settings I'm a little bit wary of it just seems like we're throwing these all over the place and it's going to get messy and also these are hard to track... if instead we can just check a specific components privately settable variable I think that makes more sense."

User also rejected the alternative I proposed of pattern-matching `BB.Get<CompoundState>(key).CurrentState is X`:
> "is pattern matching really the preferable approach here? having to grab the current state each time and check? not sure if i completely dislike it but seems maybe unideal?"

**Decision tree:**

- **Cross-system POLLED state with producer/consumer semantics** → typed component property (e.g., `CraftController.IsMenuOpen`) + typed `Condition` class with property selector enum (e.g., `CraftControllerBoolCondition` with `[Export(PropertyHint.Enum)] _property`). BB still has the component as a node ref (same shape as `HealthComponent`, `CombatantComponent` — not flag soup).

- **Event-driven interrupts** (hit, evasion entered, anim finished) → signal subscription in state `OnEnter` / unsubscribe in `OnExit`. Self-emit `TransitionState` from the handler. No flag, no condition.

- **BB-bool flags justified ONLY for:**
  1. Trigger-once flags with auto-clear semantics (existing `BBFlagCondition` pattern — flag set externally, consumed by transition, auto-cleared post-commit).
  2. State with no natural component owner (rare; usually a sign the state should have a component).

**Exemplar (canonical from this session):**
- `CraftController : Node, IComponent` owns three properties: `IsMenuOpen`, `WantsToCommit`, `HasAdditions` (derived from `AddedThisSession > 0`).
- Setters are `internal` so only CraftSM states (same assembly) can mutate.
- One condition class: `CraftControllerBoolCondition` with `[Export(PropertyHint.Enum, "IsMenuOpen,WantsToCommit,HasAdditions")] _property` + `_requiredValue` bool.
- Three transition .tres files reference the same condition class with different `_property` values.
- BB has `BBDataSig.CraftController` as a node ref. **Zero new BB-bool flags.**

**Counter-patterns to avoid:**
- Stamping new `BB.Set<bool>(key, ...)` / `BB.Get<bool>(key, ...)` entries for every new state-machine concern. The codebase has ~15 of these (`HandCanGrab`, `GrabStarted`, `ThrowStarted`, `CastingActive`, `CastStarted`, `CastCompleted`, ...) and the user has explicitly flagged this as accumulating mess.
- `BB.Get<CompoundState>(key).CurrentState is X` type-matching in conditions — leaks SM implementation detail to the condition layer, fragile to state class refactors, awkward indirection.

**Counter-example: raw BB bool IS appropriate when there are MULTIPLE producers AND multiple consumers** ("world facts" with no single owner).

Canonical: `BBDataSig.CraftWheelActive` — the wheel-open/closed flag has multiple producers (CraftWheelState.OnEnter, OnExit, error-path early-returns) and multiple consumers (HandSM transition, WizardSM Crafting↔Idle transitions, modal suppression guards). No single component owns the state. This is the legitimate raw-BB-bool use case. Discriminator litmus: *Is there exactly ONE class that authoritatively owns this state?* Yes → typed property + Consume*() method + dedicated Condition. No → raw BB bool with `BBDataSig` key is correct (decoupling matters more than write-authority narrowness).

User surfaced this concern in 2026-04-21 C11 plan mode while reviewing a `DrinkIntent` BB flag proposal: *"I'm a bit wary of these consumable blackboard flags as anyone could edit them — wondering if pulling the component itself for a publicly-gettable but privately-settable value is more safe."* Pushback led to the typed-property design. The ONE-producer / MULTI-producer distinction is the durable rule.
