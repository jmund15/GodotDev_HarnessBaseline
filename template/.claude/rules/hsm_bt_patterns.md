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
- **Can the signal hover at the boundary and flip the HSM per frame?** Then author asymmetric thresholds with a deadband wider than the signal's noise in the transition pair's `.tres` files (`arch_rule_hysteresis_for_analog_hsm_boundaries`).
- **Never clear a BB key in `OnExit` that the next state reads in `OnEnter`.** A's `OnExit` completes before B's `OnEnter`, so B reads the sentinel and a `?? default` fallback masks it. Clear at the start of the next cycle instead (`arch_rule_onexit_must_not_clobber_consumer_onenter`).

## Blackboard Transition Conditions

- **`BBFlagCondition`** — Edge-triggered. Auto-clears the flag after the transition fires through `OnTransitionCommitted`. Use for one-time events such as operation completion. **Never manually clear the flag.**
- **`BBBoolCondition`** — Level-triggered. Checks a value without modifying it. Use for persistent facts such as grounded or target-present.
- Both live under `Jmodot/Examples/AI/HSM/TransitionConditions/` — production-consumed despite the `Examples/` path; do not treat as sample-only code.
- **BB flags are HSM-transition-only, never cross-system signaling.** Reach another system through events, interface calls or component state, or observe physics directly; a flag conflates "is in state X" with "has property Y" and leaves the key with no owner (`arch_rule_bb_flag_cross_system`).
- **One authoritative owner means a typed component property plus a typed condition, not a new BB bool.** Raw BB bools are correct only for world facts with multiple producers AND consumers; never pattern-match `CurrentState` inside a condition (`feedback_typed_state_over_bb_flag_soup`).

## HSM Override Rule

**Rule:** Override `OnInit()`, `OnEnter()`, `OnExit()`, `OnProcessFrame()`, `OnProcessPhysics()` — **NEVER** override the non-virtual template methods `Enter()`/`Exit()`/`ProcessFrame()`/`ProcessPhysics()`.

- The base class is `State` (`Jmodot/Implementation/AI/HSM/State.cs` — there is no `HierarchicalState`). `Enter()`/`Exit()` manage `IsActive`, refcounted movement-quirk registration, and `ActiveStatContext` apply/remove on the stat provider. Bypassing them causes silent state corruption.
- *Convention:* All custom state logic goes in `On*` virtual methods.

## TransitionCondition Authoring

**Rule:** `TransitionCondition` subclasses use `[GlobalClass] partial class` (base: `Jmodot/Core/AI/HSM/TransitionCondition.cs`); `[Tool]` follows `csharp_patterns.md` §`[Tool]` Attribute.

- `Check(Node agent, IBlackboard bb)` must be **side-effect-free** — it can run multiple times per frame, and a passing check does NOT guarantee the transition commits (`CanExit` may block). Deferred side effects (consuming one-shot flags) belong in `OnTransitionCommitted(agent, bb)`, which fires only after the transition fully commits.
- **Conditions hold no per-evaluation state — no latch, cache or counter.** Resource sharing leaks it across actors, and a `BTCondition` clone is discarded on every tree re-Init, silently resetting it. Keep the answer on the blackboard or a state log (`arch_rule_transition_condition_stateless`).

## Animation Authority (IAnimatedState claims)

**Rule:** Claim animation at the **narrowest active scope that knows what the body should look like**;
everything else stays silent (`AnimationName` empty ⇒ `IsAnimated` false), and locomotion is the
unclaimed fallback where a fallback profile is mounted. Resolution is innermost-first: BT task > HSM
state > locomotion. Authority follows **behavioral granularity** — entities differ in where they put
granularity, never in the rule:

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
  leaf to override it. `AnimationClaimResolver`'s edge-triggered re-push on resolution change corrects a
  wrong order within one physics frame.
- **Both a state and its always-running leaf claiming** = the state's clip is permanently shadowed —
  author one of them silent.
- **Every animated entity mounts an `AnimationClaimResolver`.** An entity whose animator can be
  unclaimed for even one reachable frame carries one with a fallback profile; an entity whose leaf
  states claim 1:1 carries an arbitration-only resolver (null profile), which resolves nothing on its
  non-claiming leaves and pushes nothing, so the last clip persists.
- **One behavior split across standing and moving sibling states duplicates its input slots, and
  wiring one sibling reads identical to wiring both.** Each half declares its own optional
  `InputAction?` export and falls back silently on null, so the feature dies in one motion regime
  only, presenting as intermittent rather than unwired. *Litmus:* diff the two halves' exported
  slots and require the shared ones on both; pin by sweeping the subtree, never by naming the
  states.

## Goal-Directed AI Behaviors (BT)

**Rule:** BT actions fall into two categories based on whether they have a spatial destination:

**1. Destination behaviors** — the agent has a specific place to go, such as a safe point, resource, or waypoint. Express the goal as a **`MoveAction`** holding a `MovementGoal3D`: an optional `MovementTarget3D` (empty follows the primary target); a goal that ends itself carries the band or clearance that ends the step. The goal owns WHERE and WHEN; considerations are HOW. Each action registers its goal as one steering term through its own adapter, so per-agent goal state is keyed by action, never by the shared goal. A new goal subtype edits no existing file (base classes and required overrides: `skills/jmodot/ai.md` §Movement Steps — MoveAction & Goals). `NavigationPath3DConsideration` supplies nav-mesh routing; other considerations are **supplementary modifiers**.

**2. Reactive behaviors** — the agent has no destination, only a directional tendency (continuous flee from a moving threat, idle milling, formation cohesion). Use a `MoveAction` whose goal never ends itself (`FleeGoal3D`, `OrbitGoal3D`, `HoldRangeGoal3D`) under a time limit or a reactive parent. A consideration drives alone only as an always-on backbone term on the steering processor (separation, leash), never as a step's objective. No waypoint is set.

**Transition hygiene:** `WaypointGoal3D` and `NavApproachGoal3D` clear the nav path when their step exits, so a following reactive step starts with `NavigationPath3DConsideration` scoring zero. A step that requests a path any other way clears it on its own exit.

- *Decision heuristic:*
    - Does the behavior have a **specific place** to reach? → `WaypointGoal3D` (a strategy picks the point) or `NavApproachGoal3D` (a target, routed over the navmesh)
    - Is the behavior **indefinite and tracking a moving input** (threat, leader, signal)? → a goal that never ends itself (`FleeGoal3D`, `OrbitGoal3D`, `HoldRangeGoal3D`), ended by a time limit, a reactive parent or the state
    - Is the behavior **bounded with a spatial objective** but the target moves? → `WaypointGoal3D` with a positive `RepickInterval`, an `ApproachGoal3D` that ends inside its band, or a `RetreatGoal3D` that ends past its safe distance
- *Why:* This mirrors the HSM/BT control-authority split at a finer grain. HSM = strategic (WHAT to do), BT = tactical (HOW). Within BT: the goal = WHERE to go and WHEN the step is done; considerations = HOW to move there (style and environment).
- *Examples:*
    - A bounded patrol route → `WaypointGoal3D` over a zone strategy, wander noise as a style term.
    - Indefinite separation from a moving threat → `FleeGoal3D` over the shared threat target, no style term.
    - Bounded flight from a threat → `FleeGoal3D` ended by a `TimeLimit` that succeeds on abort.

## Touchpoints

- Jmodot's controller, movement, perception, and observation components are data producers that conditions may read.
- Companion: [`architecture_philosophy/SKILL.md`](../skills/architecture_philosophy/SKILL.md) covers the broader *Typed-Owned State over BB Flags* and *Marker Interface as Capability Query* rules.
