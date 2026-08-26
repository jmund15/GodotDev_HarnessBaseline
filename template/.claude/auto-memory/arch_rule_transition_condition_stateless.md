---
name: TransitionCondition Resources must be stateless
description: TransitionCondition subclasses hold no mutable per-evaluation state; Check(agent,bb) must be a pure function of args + immutable [Export] config.
type: feedback
originSessionId: 15bc6648-e4d1-4a64-b970-d32e8c122873
modified: 2026-08-24T07:10:48.061Z
---
`TransitionCondition` (Resource) subclasses **must hold no mutable per-evaluation state.** No latch fields. No cached subscription references. No frame counters. `Check(agent, bb)` must be a pure function of `(agent, bb)` + immutable `[Export]` config.

To represent "did event X just happen?" — query an external state log (`CombatLog.GetMostRecent<T>(window)`) or a stateful component on the Blackboard. **Never store the answer locally on the condition.**

**Why:** the sharing route is Godot's **resource cache on the transition `.tres`**, not the condition file itself. Conditions are authored as inline `sub_resource`s inside each transition `.tres`; when two actor scenes load the same transition by path, `ResourceLoader` hands both the same instance — inline sub-resources included. Any per-instance mutation then leaks across actors: actor A latches during its evaluation, actor B sees the latched state during its own. Same Factory→Runner principle as the broader Resource state-cache ban

**Scope:** the hazard is structural. Whether any cross-actor sharing is actually instantiated is per-project — `git grep` your AI `.tres` tree for standalone condition/transition files with multiple inbound refs before treating this as an observed leak rather than prophylaxis. The original "shared via `[ext_resource]` … referenced by multiple actors" was the wrong route stated in the present tense. (Resources are immutable shared data; mutable state lives on runners/components).

**How to apply:** When designing a new TransitionCondition, ask *"does Check need to remember anything across calls?"* If yes, the answer doesn't go on the condition — it goes on a Blackboard component (mutable, per-actor) or a state log (queryable history). The condition reads from those, never owns the state.

**BTCondition variant — cloning does NOT rescue per-instance state; clone LIFETIME kills it.** `BehaviorTask.Init` duplicates each authored `BTCondition` per tree Init (`Duplicate(true)`), so cross-actor leaks can't happen — but every tree re-Init (BTState re-entry, `_onTreeSuccessState = "."` self-transition) discards the clone and its state with it. A stateful gate (cooldown latch, counter) silently resets on every state churn. Same fix as above: state on the entity's blackboard (`CooldownChannel` timestamp) or a component; the condition stays a stateless reader.
**Concrete (2026-08-24):** centipede's `CooldownCondition` (`_isReady` on the clone) armed on only ~16 of 30 spits — `CombatState → CombatState` self-transitions re-cloned it mid-cooldown. Replaced by `CooldownChannel` (BB-resident ready-at) + `BBCooldownReadyCondition`.

**Concrete (2026-05-10):** `WallImpactCondition` had a latch field + signal subscription that fired Check() based on "did I see a wall impact since last reset?" — that's per-instance state and would leak between actors sharing the resource. The latch+subscription pattern was deleted; correct shape is a `CombatLog.GetMostRecent<WallImpactEvent>(window)` query (tracked as worklog item).

**User verbatim:** *"if it's a state transition / transition condition, they MUST be stateless resources."*

**Migrated from MCP** (was `TransitionCondition_Stateless_Rule`, entityType `architectural_rule`) 2026-05-11.
