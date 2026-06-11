---
name: TransitionCondition Resources must be stateless
description: TransitionCondition subclasses hold no mutable per-evaluation state; Check(agent,bb) must be a pure function of args + immutable [Export] config.
type: feedback
originSessionId: 15bc6648-e4d1-4a64-b970-d32e8c122873
---
`TransitionCondition` (Resource) subclasses **must hold no mutable per-evaluation state.** No latch fields. No cached subscription references. No frame counters. `Check(agent, bb)` must be a pure function of `(agent, bb)` + immutable `[Export]` config.

To represent "did event X just happen?" — query an external state log (`CombatLog.GetMostRecent<T>(window)`) or a stateful component on the Blackboard. **Never store the answer locally on the condition.**

**Why:** TransitionConditions are shared via `[ext_resource]` — the same `.tres` instance is referenced by multiple actors/states. Any per-instance mutation leaks across actors: actor A latches the condition during its evaluation, and actor B sees the latched state during its own evaluation. Same Factory→Runner principle as the broader Resource state-cache ban (Resources are immutable shared data; mutable state lives on runners/components).

**How to apply:** When designing a new TransitionCondition, ask *"does Check need to remember anything across calls?"* If yes, the answer doesn't go on the condition — it goes on a Blackboard component (mutable, per-actor) or a state log (queryable history). The condition reads from those, never owns the state.

**Concrete (2026-05-10):** `WallImpactCondition` had a latch field + signal subscription that fired Check() based on "did I see a wall impact since last reset?" — that's per-instance state and would leak between actors sharing the resource. The latch+subscription pattern was deleted; correct shape is a `CombatLog.GetMostRecent<WallImpactEvent>(window)` query (tracked as worklog item).

**User verbatim:** *"if it's a state transition / transition condition, they MUST be stateless resources."*

**Migrated from MCP** (was `TransitionCondition_Stateless_Rule`, entityType `architectural_rule`) 2026-05-11.
