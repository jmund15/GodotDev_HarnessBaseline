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
- **Reactive vs active states — only reactive states are restricted from authoring physics.** A **reactive state** responds to a received event and must NOT author the event that triggered it. The impulse, damage, or other input predates the state; the state owns animation, strategy swap, and stat context for the response. An **active state** represents a chosen action and may apply that action's mechanics from `OnEnter`. Whether to extract those mechanics into a reusable component is a separate reuse decision.
- **Litmus (reactive only):** *"If I deleted the HSM, would the received event still occur? Would I only lose the routed response?"* Yes → stratified correctly. No → causal authority for an external event leaked into the wrong layer.
- **Concrete shape:** a condition may read controller velocity from BB and evaluate `IsOnFloor && Velocity.Y > MinY` directly. Do not add a detector component that precomputes the answer into a BB flag.
- *Meta-principle for:* `Observation_Over_Computation_Pattern`, `BB_Flag_Cross_System_Anti_Pattern`, `TransitionCondition_Stateless_Rule` (auto-memory) — the design-time stance those entries memorialize specific rejections of.

## HSM vs. Orchestration Flows

**Rule:** The Jmodot HSM is for **entity behavior routing** — an agent reacting to events or perception over an autonomous substrate. **Sequential lifecycle or orchestration flows** such as scene transitions, staged jobs, and UI animation sequences use a **bespoke phase enum + pure advance function + entry-action switch**, NOT the HSM.

- **Litmus:** *"Is this routing an agent's response to external events, or IS it the orchestrated action itself?"* Observer-over-substrate → HSM. The flow *is* the action (no autonomous substrate to observe, no agent/Blackboard) → bespoke phase machine.
- Forcing an orchestration flow into the HSM bolts on Blackboard injection, a Node-hierarchy, and condition Resources for zero reuse — the states aren't reused and carry no agent. The entry-action `switch` in a phase orchestrator is legitimate dispatch (each case does different work), not the closed-set-switch smell.
- *Example shape:* `PhaseCoordinator` + a pure `PhaseLogic` helper.

## State Transitions

Hybrid Transitions: It is perfectly fine and recommended to use both `EmitSignal` and `TransitionCondition`s depending on the situation.

- **`EmitSignal`:** Use for Internal Logic Completion (e.g., "I finished my animation", "I am fully charged"). The State knows it's done. **Caveat:** `AnimFinished` is valid only when the animation *is* the completion criterion. When the clip is cosmetic over a physical process (turn-pivot bleeding momentum, landing settling velocity), exit on the physics fact via a `TransitionCondition` (e.g. velocity threshold) — the clip length is tuning, the physics state is truth.
- **`TransitionCondition`s:** Use for external interrupts such as cancel input or received damage. The state does not need to know about these; the transition system handles them.

## Blackboard Transition Conditions

- **`BBFlagCondition`** — Edge-triggered. Auto-clears the flag after the transition fires through `OnTransitionCommitted`. Use for one-time events such as operation completion. **Never manually clear the flag.**
- **`BBBoolCondition`** — Level-triggered. Checks a value without modifying it. Use for persistent facts such as grounded or target-present.
- Both live under `Jmodot/Examples/AI/HSM/TransitionConditions/` — production-consumed despite the `Examples/` path; do not treat as sample-only code.

## HSM Override Rule

**Rule:** Override `OnInit()`, `OnEnter()`, `OnExit()`, `OnProcessFrame()`, `OnProcessPhysics()` — **NEVER** override the non-virtual template methods `Enter()`/`Exit()`/`ProcessFrame()`/`ProcessPhysics()`.

- The base class is `State` (`Jmodot/Implementation/AI/HSM/State.cs` — there is no `HierarchicalState`). `Enter()`/`Exit()` manage `IsActive`, the `BBDataSig.SelfInteruptible` flag, and `ActiveStatContext` apply/remove on the stat provider. Bypassing them causes silent state corruption.
- *Convention:* All custom state logic goes in `On*` virtual methods.

## TransitionCondition Authoring

**Rule:** `TransitionCondition` subclasses must be `[GlobalClass, Tool] partial class` (base: `Jmodot/Core/AI/HSM/TransitionCondition.cs`).

- `Check(Node agent, IBlackboard bb)` must be **side-effect-free** — it can run multiple times per frame, and a passing check does NOT guarantee the transition commits (`CanExit` may block). Deferred side effects (consuming one-shot flags) belong in `OnTransitionCommitted(agent, bb)`, which fires only after the transition fully commits.
- `[Tool]` is required so the Editor can instantiate the Resource for Inspector display.

## Animation Authority (IAnimatedState claims)

**Rule:** Claim animation at the **narrowest active scope that knows what the body should look like**;
everything else stays silent (`AnimationName` empty ⇒ `IsAnimated` false), and locomotion is the
unclaimed fallback on driver entities. Resolution is innermost-first: BT task > HSM state > locomotion.
Authority follows **behavioral granularity** — entities differ in where they put granularity, never in
the rule:

- **No BT under the state**: the leaf state is the narrowest active scope, so it claims the clip. If locomotion phases are first-class states, the locomotion fallback does not fire there.
- **BT under the state**: ask per subtree whether the state knows the look for its whole duration. If the tree only steers, the state claims. If the clip depends on the active task, the state stays silent and the task claims.

Four sanctioned shapes — pick by who knows the clip:

| Shape | When | Example |
|---|---|---|
| **Container-silent / leaf-claims** | The state can't know because the active task selects the clip; between tasks, locomotion shows movement | action leaves claim |
| **State-claims / leaves-silent** | The state owns one look for its whole duration; its tree only steers or routes | state claims over silent navigation tasks |
| **All-silent** | Pure locomotion — velocity-derived clips are correct | movement-only behavior |
| **One-shot** (shape, no marker interface) | The clip is the completion criterion — the state claims AND gates its exit on `AnimFinished` | recovery or interaction state |

- **Enter-order invariant:** an animated `BTState` claims (`StartAnim`) BEFORE `base.OnEnter()` enters
  its tree — clips are last-start-wins at enter time, so the outer claim must land first for an animated
  leaf to override it. On entities with a locomotion animation driver the per-frame push corrects a wrong
  order within a frame; on driverless entities the order is the only enforcement.
- **Both a state and its always-running leaf claiming** = the state's clip is permanently shadowed —
  author one of them silent.
- Runtime unification (single resolver for all animated entities) is a tracked design topic; this
  grammar is runtime-independent and survives it.

## Goal-Directed AI Behaviors (BT)

**Rule:** BT actions fall into two categories based on whether they have a spatial destination:

**1. Destination behaviors** — the agent has a specific place to go, such as a safe point, resource, or waypoint. Express the goal as a **waypoint** via `WaypointSelectionStrategy`. `NavigationPath3DConsideration` provides nav-mesh-routed pathfinding. Other considerations are supplementary modifiers on the journey.

**2. Reactive behaviors** — the agent has no destination, only a directional tendency (continuous flee from a moving threat, idle milling, formation cohesion). Considerations are the **primary driver**. No waypoint is set.

**Transition hygiene:** When switching from a destination behavior to a reactive behavior, **clear the active nav path** so `NavigationPath3DConsideration` returns zero scores and does not compete with the reactive considerations.

- *Decision heuristic:*
    - Does the behavior have a **specific place** to reach? → Waypoint
    - Is the behavior **indefinite and tracking a moving input** (threat, leader, signal)? → Consideration
    - Is the behavior **bounded with a spatial objective** but the target moves? → Waypoint with re-evaluation on reach
- *Why:* This mirrors the HSM/BT control-authority split at a finer grain. HSM = strategic (WHAT to do), BT = tactical (HOW). Within BT: WaypointStrategy = strategic (WHERE to go), Considerations = tactical (HOW to move there).
- *Examples:*
    - A bounded patrol route → destination; noise or avoidance modifies the route.
    - Indefinite separation from a moving threat → reactive; flee consideration drives movement.
    - Bounded movement to a recalculated safe point → waypoint with re-evaluation on reach.

## Touchpoints

- Jmodot's controller, movement, perception, and observation components are data producers that conditions may read.
- Companion: [`architecture_philosophy/SKILL.md`](../skills/architecture_philosophy/SKILL.md) covers the broader *Typed-Owned State over BB Flags* and *Marker Interface as Capability Query* rules.
