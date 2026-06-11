---
name: BB flags are HSM-transition-only — never cross-system signaling
description: BBFlagCondition/BBBoolCondition exist for HSM transitions; cross-system state communication routes via events/component-state/physics-observation, never via BB flag.
type: feedback
originSessionId: 15bc6648-e4d1-4a64-b970-d32e8c122873
---
**BB flags (`BBFlagCondition` / `BBBoolCondition`) are for HSM transition conditions ONLY.** Cross-system state communication must route via one of:
- C# events → interface calls → component state (e.g., `StatusRunner` / `IStatusEffectComponent` tag-keyed API)
- Direct physics observation (HSM `TransitionCondition` reading `ICharacterController3D` from Blackboard)

**Never** `BBDataSig.Flag = true` to signal another system. That conflates "this entity is in HSM state X" with "this entity has property Y" — the BB becomes flag soup with no clear ownership, no typed contract, and no compile-time discipline on who's allowed to set the flag.

**Concrete (2026-05-07):** KnockedUp wiring — `VerticalVelocityCondition` observes `ICharacterController3D.IsOnFloor && Velocity.Y > MinY` **directly**, NOT `BBDataSig.KnockedUp = true` + `BBFlagCondition`. The HSM observes physics state through a typed condition; no flag in the middle.

**Rejected alternative:** During the same design pass, a `HasStatusCondition` was proposed as a generic cross-system query. Rejected because: (a) it didn't exist — `StatusActiveCondition` already covers tag-keyed status query; (b) KnockedUp is HSM-state-shaped (transient mutex impact phase), not status-shaped (timed / stacking / layered effect). Confusing the two would have produced a misshapen abstraction.

**Adjacent:** See `feedback_typed_state_over_bb_flag_soup.md` for the broader "typed component property over BB-bool flag soup" rule — this rule is the cross-system-signaling-specific case of that principle.

**Migrated from MCP** (was `BB_Flag_Cross_System_Anti_Pattern`, entityType `ArchitecturalPattern`) 2026-05-11.
