---
paths:
  - "**/HSM/**"
  - "**/States/**"
  - "**/BehaviorTree/**"
  - "**/AI/**"
  - "**/*State.cs"
  - "**/*Condition.cs"
---

# HSM and Behavior Tree Patterns

**Context:** Hierarchical State Machines route entities to response states; Behavior Trees express tactical AI within a state. This rule codifies the **layering invariant** (HSM observes, physics drives), transition mechanics, and BT action shape. Auto-loads on HSM/BT/state/condition files.

## HSM Routes; Physics Drives

**Rule:** The HSM is an OBSERVER LAYER over the autonomous physics/component substrate. An impulse, damage event, or perception trigger affects the entity *the same way with the HSM removed* — knockback magnitude × mass × stability × the receiver's `MovementStrategy` determines the outcome. The HSM's job is to **route the entity to the correct state**, which selects the appropriate **animation** + **`MovementStrategyOverride`** + **`StatContext`** for the response shape.

- **Determination lives in `TransitionCondition.Check()`.** Conditions may freely read BB, query stateful components (`CombatLog.GetMostRecent<T>(window)`, `ICharacterController3D.Velocity`, perception lists), and process the result (band comparisons, eligibility math against config exports). The *processing* is fine; what's forbidden is **outsourcing the decision** — an external "detector" component that reads state, pre-classifies the transition, and sets a BB flag the condition reads as a dumb boolean. The condition must own the *if-then*, not just consume a pre-computed answer.
- **Calculator / data-processor components ARE fine.** `KnockbackComponent3D` writes `KnockbackResult`; `MovementProcessor3D` exposes `Velocity`; `AIPerceptionManager3D` updates threat lists; `CombatLog` accumulates events. These publish raw observations; conditions interpret them. The line is *who owns the predicate*: data-producer (✅) vs decision-maker writing a flag the condition trusts (❌).
- **Reactive vs active states — only reactive states are restricted from authoring physics.** A **reactive state** (response to a *received* event: `KnockedUpState`, `LaunchedState`, `StunnedState`, `HitState`, `CapturedState`, `DyingState`) must NOT author the event that triggered it. The impulse / damage / faction change predates the state; the state's job is animation + strategy-swap + stat-context for the response. `LaunchedState.OnEnter` calling `ApplyImpulse` is upside-down; `DyingState.OnEnter` calling `SetHealth(0)` is upside-down. An **active state** (chosen action: `SlapState`, `ThrownState`, `JumpState`, `DashState`, `CastChargeState`) IS the action — applying its action's mechanics from `OnEnter` is standard FSM semantics, not a violation. Whether to extract those mechanics into a reusable `*Component` is a separate DRY / multi-invoker call, not an HSM-layering question.
- **Litmus (reactive only):** *"If I deleted the HSM, would the **received event** still occur? Would I only lose the animation + strategy-swap response?"* Yes → stratified correctly. No → causal authority for an external event leaked into the wrong layer. For active states the litmus inverts trivially (deleting the HSM removes the action-decision itself, which is expected) and doesn't apply.
- *Concrete:* `VerticalVelocityCondition` reads `ICharacterController3D` directly from BB and evaluates `IsOnFloor && Velocity.Y > MinY` — no `KnockedUpDetector` component, no `BBDataSig.KnockedUp` flag (KnockedUp Session 2, 2026-05-07).
- *Meta-principle for:* `Observation_Over_Computation_Pattern`, `BB_Flag_Cross_System_Anti_Pattern`, `TransitionCondition_Stateless_Rule` (auto-memory) — the design-time stance those entries memorialize specific rejections of.

## HSM vs. Orchestration Flows

**Rule:** The Jmodot HSM is for **entity behavior routing** — an agent reacting to events/perception over an autonomous physics substrate. **Sequential lifecycle/orchestration flows** (scene transitions, wave spawning, telegraph timing, UI animation sequences) use a **bespoke phase enum + pure advance-function + entry-action switch**, NOT the HSM.

- **Litmus:** *"Is this routing an agent's response to external events, or IS it the orchestrated action itself?"* Observer-over-substrate → HSM. The flow *is* the action (no autonomous substrate to observe, no agent/Blackboard) → bespoke phase machine.
- Forcing an orchestration flow into the HSM bolts on Blackboard injection, a Node-hierarchy, and condition Resources for zero reuse — the states aren't reused and carry no agent. The entry-action `switch` in a phase orchestrator is legitimate dispatch (each case does different work), not the closed-set-switch smell.
- *Examples (bespoke, correct):* `TransitionOrchestrator` + `TransitionPhaseLogic`, `RunPhase`, `WavePhase`, `TelegraphedTimingHandler`, `AnimationPlan`.

## State Transitions

Hybrid Transitions: It is perfectly fine and recommended to use both `EmitSignal` and `TransitionCondition`s depending on the situation.

- **`EmitSignal`:** Use for Internal Logic Completion (e.g., "I finished my animation", "I am fully charged"). The State knows it's done. **Caveat:** `AnimFinished` is valid only when the animation *is* the completion criterion. When the clip is cosmetic over a physical process (turn-pivot bleeding momentum, landing settling velocity), exit on the physics fact via a `TransitionCondition` (e.g. velocity threshold) — the clip length is tuning, the physics state is truth.
- **`TransitionCondition`s:** Use for External Interrupts (e.g., "Player pressed Cancel", "Player took damage"). The State doesn't need to know about these; the transition system handles them.

## Blackboard Transition Conditions

- **`BBFlagCondition`** — Edge-triggered. Auto-clears flag after transition fires. Use for one-time events ("cast completed", "charge ready"). **Never manually clear the flag — the condition handles it.**
- **`BBBoolCondition`** — Level-triggered. Checks value without modifying it. Use for persistent state ("is grounded", "has target").

## HSM Override Rule

**Rule:** Override `OnEnter()`, `OnExit()`, `OnProcessFrame()`, `OnProcessPhysics()` — **NEVER** override `Enter()`/`Exit()` directly.

- The base `HierarchicalState` manages `IsActive`, `StatContext`, and blackboard flag lifecycle in `Enter()`/`Exit()`. Overriding these methods bypasses that bookkeeping and causes silent state corruption.
- *Convention:* All custom state logic goes in `On*` virtual methods.

## TransitionCondition Authoring

**Rule:** `TransitionCondition` subclasses must be `[GlobalClass, Tool] partial class`.

- `Check()` must be **side-effect-free** — it runs every `_Process` tick on every outgoing transition. Writing state, firing events, or modifying blackboard values inside `Check()` causes frame-rate-dependent behavior.
- `[Tool]` is required so the Editor can instantiate the Resource for Inspector display.

## Goal-Directed AI Behaviors (BT)

**Rule:** BT actions fall into two categories based on whether they have a spatial destination:

**1. Destination behaviors** — the agent has a specific place to go (flee to a safe point, forage an ingredient, patrol a waypoint). Express the goal as a **waypoint** via `WaypointSelectionStrategy`. `NavigationPath3DConsideration` provides nav-mesh-routed pathfinding. Other considerations (obstacle avoidance, zone bounds, light reactive flee) are **supplementary modifiers** on the journey.

**2. Reactive behaviors** — the agent has no destination, only a directional tendency (continuous flee from a moving threat, idle milling, formation cohesion). Considerations are the **primary driver**. No waypoint is set.

**Transition hygiene:** When switching from a destination behavior to a reactive behavior (e.g., WanderState → ScurryState), **clear the active nav path** so `NavigationPath3DConsideration` returns zero scores and doesn't compete with the reactive considerations.

- *Decision heuristic:*
    - Does the behavior have a **specific place** to reach? → Waypoint
    - Is the behavior **indefinite and tracking a moving input** (threat, leader, signal)? → Consideration
    - Is the behavior **bounded with a spatial objective** but the target moves? → Waypoint with re-evaluation on reach
- *Why:* This mirrors the HSM/BT control-authority split at a finer grain. HSM = strategic (WHAT to do), BT = tactical (HOW). Within BT: WaypointStrategy = strategic (WHERE to go), Considerations = tactical (HOW to move there).
- *Examples:*
    - `WanderState` → destination (zone waypoint), wander noise as modifier
    - `ScurryState (hoarder)` → reactive (indefinite flee from moving wizard), flee consideration as primary
    - `ScurryState (test_critter)` → destination (bounded 3s flee to safe point), flee consideration as modifier

## Touchpoints

- `VerticalVelocityCondition` — canonical example of HSM-routes/physics-drives layering.
- `KnockbackComponent3D` / `CombatLog` / `AIPerceptionManager3D` — calculator/data-producer components conditions read from.
- `WanderState` / `ScurryState` — destination vs reactive BT shapes.
- Companion: [`architecture_philosophy/SKILL.md`](../skills/architecture_philosophy/SKILL.md) covers the broader *Typed-Owned State over BB Flags* and *Marker Interface as Capability Query* design rules that this layering invariant rests on.
