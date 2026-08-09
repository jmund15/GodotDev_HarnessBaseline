---
name: Status Effect Authoring
description: >-
  Procedure for creating a status effect that drives an HSM state transition in
  {{PROJECT_NAME}} (e.g. stun, freeze, root, slow-to-stop). SKIP for status effects with no
  HSM transition (just a CombatTag + factory — author inline), pure visual tints
  (`vfx_patterns`), or balance tuning on existing effects.
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
    *   Movement strategy (e.g., `IdleFrictionStrategy3D` — `Movement/IdleFrictionStrategy3D.cs` — for frozen-in-place; this project is 3D, don't wire Jmodot's `...2D` variant)
    *   Animation name
    *   Optional: `VisualEffect` for tint/flash (applied in `OnEnter`, stopped in `OnExit`)
6.  **Wire Transitions:**
    *   Add entry transition to states that can be affected (Idle, Run, etc.)
    *   Add exit transition to the new state (back to Idle)
    *   Add interrupt transitions (e.g., hurt transition) if interrupts should break the status

## Runner Authoring Rules

*   **Start-time scaling must survive refresh.** Any target-side scaling applied in `StatusRunner.Start` (element resistance, potency) is silently stripped by the StackPolicy Refresh path — `RefreshDuration` sources its duration from the *incoming, never-started* runner, whose values are unscaled. Override `RefreshDuration` alongside `Start`, and pin the behavior with a refresh-preserves-scaling test.
*   **Status-vs-status rules live in `CategoryInteraction`**, not payload filters or `ImmuneCategories`: state-conditional acceptance/extinguish (e.g. frozen rejects fire, freeze extinguishes burn) is authored as entries in the project's category-interactions `.tres` (`CancelIncoming`/`CancelExisting`, matched via `IsOrDescendsFrom` in `StatusEffectComponent.AddStatus`). Trap: author two DIRECTIONAL interactions when the two directions need different effects — a bidirectional `CancelIncoming` also rejects the reverse application.

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
