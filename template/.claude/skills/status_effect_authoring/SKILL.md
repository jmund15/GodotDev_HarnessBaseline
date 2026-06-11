---
name: Status Effect Authoring
description: >-
  Procedure for creating a status effect that drives an HSM state transition in
  {{PROJECT_NAME}} (e.g. stun, freeze, root, slow-to-stop). Triggers: "create status effect",
  "stun effect", "freeze effect", "root effect", "slow effect", "status effect that
  transitions to state". SKIP for status effects with no HSM transition (just a CombatTag
  + factory — author inline), pure visual tints (`vfx_patterns`), or balance tuning on
  existing effects.
---

# Status Effect Authoring (HSM State Transition)

## Pre-Workflow Check
- [ ] **auto-memory** searched for status-effect gotchas (`status`, `HSM`, `transition`).
- [ ] **Existing CombatTags** under `Global/Combat/Tags/` reviewed — extending a tag family beats minting a new one when semantically related.
- [ ] **Visual decision made up-front** (state-driven vs effect-driven) — mixing modes produces pulse-vs-persistent collisions (`status_visual_pulse_vs_persistent_pattern.md`).

## Pattern: Hybrid Transition (Event-Driven Entry + State-Driven Exit)

1.  **CombatTag:** Create `Global/Combat/Tags/[effect]_effect.tres` with unique TagId.
2.  **Effect Factory:** Create `DurationRevertibleEffectFactory` with:
    *   Duration (`ConstantFloatDefinition` or `AttributeFloatDefinition`)
    *   RevertibleEffect (e.g., `StatEffectFactory` modifying max_speed)
    *   Tags array including the CombatTag
3.  **Entry Condition:** Create `StatusAppliedCondition` resource checking for StatusResult with the tag.
    *   This is EVENT-DRIVEN: triggers once when effect is applied.
4.  **Exit Condition:** Create `StatusActiveCondition` resource with `Inverted=true`.
    *   This is STATE-DRIVEN: continuously valid while tag is inactive.
5.  **State Class:** Create state extending `State` with:
    *   Movement strategy (e.g., `IdleFrictionStrategy2D` for frozen-in-place)
    *   Animation name
    *   Optional: `VisualEffect` for tint/flash (applied in `OnEnter`, stopped in `OnExit`)
6.  **Wire Transitions:**
    *   Add entry transition to states that can be affected (Idle, Run, etc.)
    *   Add exit transition to the new state (back to Idle)
    *   Add interrupt transitions (e.g., hurt transition) if interrupts should break the status

## Design Decisions

*   **State-driven visuals:** Better when interrupts should clear visuals immediately.
*   **Effect-driven visuals (`StatusRunner.StatusVisualEffect`):** Better when visuals must exactly match effect duration.
*   If using state-driven visuals, keep visual duration synced with effect duration.

## Reference call sites

Existing implementations to study before authoring a new one:

| Effect | Tag / Factory / Transitions |
|---|---|
| Freeze (Wizard) | `AI/HSM/Wizard/Transitions/wizard_freeze_transition.tres` + `wizard_freeze_exit_transition.tres` |
| Freeze (NPC) | `NPCs/AI/Transitions/npc_freeze_transition.tres` + `npc_freeze_exit_transition.tres` |
| Slow / Size duration | `Global/CombatEffects/slow_duration_effect.tres`, `size_increase_duration_effect.tres` |

Transition condition source: `Jmodot/Examples/AI/HSM/TransitionConditions/StatusAppliedCondition.cs`, `StatusActiveCondition.cs`, `StatusActiveAnyTagCondition.cs`.

## Cross-references

- [`jmodot`](../jmodot/SKILL.md) — Combat / Status / HSM subsystem deep-dive.
- [`architecture_philosophy`](../architecture_philosophy/SKILL.md) — HSM Routes; Physics Drives meta-principle (transitions own determination, not external detector flipping BB flag).
- [`testing`](../testing/SKILL.md) — POB test mandatory if the effect has observable behavior.
- `feedback_hsm_routes_physics_drives.md` — observer-layer rule for transitions.
- `runner_required_burn_effect_regression.md` — Runner=null masks as "spell won't cast"; verify status-runner wiring.
