---
name: Windowed-history predicates use stateful-Component + stateless-Condition split
description: When a TransitionCondition's predicate needs sliding-window history (input over N seconds, event recency, trail accumulation), the correct shape is always-running stateful Component on BB + stateless Condition reading it. Positive-shape companion to TransitionCondition_Stateless_Rule.
type: feedback
originSessionId: 8892bf33-068a-4265-ad0e-ad954ac0de36
---
When you find yourself wanting to latch state inside a `TransitionCondition` because the predicate needs windowed history (input direction over last N seconds, event recency within window, signed accumulator across frames, trail/breadcrumb buffer), the correct architectural shape is two cooperating objects, not one stateful Condition:

1. **Always-running stateful Component** (Node + IComponent) mirrors `Jmodot/Implementation/Combat/CombatLogger.cs` shape — `Initialize(IBlackboard)` registers self under a `BBDataSig` key, polls or listens to the source each frame, publishes raw observation on BB (the accumulator value, the timestamped event list, the trail buffer). Lives as a child of the entity; per-actor instance; runs regardless of HSM state.
2. **Stateless `TransitionCondition`** with `[Export]` config for the predicate parameters (threshold, sign, recency-window if per-condition). `Check(agent, bb)` does `bb.TryGet<TheComponent>(BBDataSig.TheKey)` and computes the predicate from the published observation. **Never** uses `agent.TryGetFirstChildOfType` (per `Jmodot_HSM_API` rule); always BB-resolved.

**Why:** `TransitionCondition` Resources are shared via `[ext_resource]` across actors (per `arch_rule_transition_condition_stateless.md`); a latch field would leak state across actor instances. The Component is per-actor, so its state is correctly scoped. The split also enforces the HSM-routes-physics-drives layering — Component publishes raw observation (a data-producer per `Observation_Over_Computation_Pattern`); Condition owns the predicate (the if-then determination). Neither side latches a verdict.

**Existing exemplars of this shape:**
- `CombatLogger` (stateful, _PhysicsProcess accumulates events) + `CombatLogCondition` (stateless reader of `CombatLog.GetMostRecent<T>(window)`)
- `AIPerceptionManager3D` (stateful, updates threat memory + decay) + `PerceptionHasMemoryCondition` (stateless reader)
- `ICharacterController3D.Velocity` (stateful, physics-driven) + `VerticalVelocityCondition` (stateless reader of `IsOnFloor && Velocity.Y > MinY`)
- `SharpTurnDetectorComponent` (stateful, _Process accumulates signed turn angle within window) + `SignedTurnDetectionCondition` (stateless reader of `Math.Abs(accum) >= threshold && Sign == required`) — added 2026-05-13 for Wizard locomotion turn detection

**How to apply:** Anytime a TransitionCondition's predicate has "since when" / "within the last N frames" / "summed over a window" / "trail of last K events" semantics, the answer is NOT a latch field on the Condition. The answer is to find or author a stateful Component that publishes the raw history, and have the Condition read it. If the Component doesn't exist yet, author it first (typically as project-local; graduate to Jmodot framework only after a second consumer materializes per `feedback_inspect_existing_abstractions_first.md`).

**Decision flow:**
- Predicate needs no history → stateless Condition reads BB component directly (e.g., `IsOnWallCondition` reading `ICharacterController3D.IsOnWall`).
- Predicate needs windowed history → Component+Condition split (this rule).
- Tempted to put history on the Condition → STOP. Author the Component.

Witnessed 2026-05-13 during Wizard locomotion HSM refactor — `/plan_check` F8 ASK finding pushed the planner from EmitSignal-in-OnProcessFrame (state owns predicate) to declarative `SignedTurnDetectionCondition` reading from `SharpTurnDetectorComponent`, modeled explicitly on the `CombatLogger`/`CombatLogCondition` precedent. The 25-sibling `TransitionCondition` family extension also satisfies `feedback_inspect_existing_abstractions_first.md`.
