---
name: Status Effect Authoring
description: >-
  Procedure for creating a status effect that drives an HSM state transition. SKIP for status effects with no
  HSM transition (just a CombatTag + factory — author inline), pure visual tints
  (`vfx_patterns`), or balance tuning on existing effects.
---

# Status Effect Authoring (HSM State Transition)

## Pre-Workflow Check
- [ ] **auto-memory** searched for status-effect gotchas (`status`, `HSM`, `transition`).
- [ ] **Existing `CombatTag` Resources** reviewed through the owning path in `skills/project_subsystems/SKILL.md`; extend a related tag family instead of minting a parallel one.
- [ ] **Visual decision made up-front** (state-driven vs effect-driven) — mixing modes produces pulse-vs-persistent collisions (`status_visual_pulse_vs_persistent_pattern.md`).

## Pattern: Hybrid Transition (Event-Driven Entry + State-Driven Exit)

1.  **CombatTag:** Create or extend a tag Resource under the project-owned status/tag path declared in `skills/project_subsystems/SKILL.md`.
2.  **Effect Factory:** Create `DurationRevertibleEffectFactory` with:
    *   Duration (`ConstantFloatDefinition` or `AttributeFloatDefinition`)
    *   RevertibleEffect (e.g., `StatEffectFactory` modifying max_speed)
    *   Tags array including the CombatTag
3.  **Entry Condition:** Create `StatusAppliedCondition` resource checking for StatusResult with the tag.
    *   This is EVENT-DRIVEN: triggers once when effect is applied.
4.  **Exit Condition:** Create `StatusActiveCondition` resource with `Inverted=true`.
    *   This is STATE-DRIVEN: continuously valid while tag is inactive.
5.  **State Class:** Create a state extending `State` with:
    *   A movement strategy matching the consuming project's dimension and control contract
    *   Animation name
    *   Optional: `VisualEffect` for tint/flash (applied in `OnEnter`, stopped in `OnExit`)
6.  **Wire Transitions:**
    *   Add entry transition to states that can be affected (Idle, Run, etc.)
    *   Add exit transition to the new state (back to Idle)
    *   Add interrupt transitions (e.g., hurt transition) if interrupts should break the status

## Runner Authoring Rules

*   **Start-time scaling must survive refresh.** Any target-side scaling applied in `StatusRunner.Start` (element resistance, potency) is silently stripped by the StackPolicy Refresh path — `RefreshDuration` sources its duration from the *incoming, never-started* runner, whose values are unscaled. Override `RefreshDuration` alongside `Start`, and pin the behavior with a refresh-preserves-scaling test.
*   **Status-vs-status rules live in `CategoryInteraction`**, not payload filters or `ImmuneCategories`. Author state-conditional accept/cancel behavior in the project's category-interactions Resource. A bidirectional `CancelIncoming` also rejects the reverse application; author two directional interactions when the directions differ.

## Design Decisions

*   **State-driven visuals:** Better when interrupts should clear visuals immediately.
*   **Effect-driven visuals (`StatusRunner.StatusVisualEffect`):** Better when visuals must exactly match effect duration.
*   If using state-driven visuals, keep visual duration synced with effect duration.

## Reference call sites

Before authoring, locate one live tag, factory, entry transition, and exit transition through the owning paths in `skills/project_subsystems/SKILL.md`. Do not copy another project's status names or folder layout.

Framework transition conditions live under Jmodot's HSM examples. Locate `StatusAppliedCondition`, `StatusActiveCondition`, and `StatusActiveAnyTagCondition` before wiring them.

## Cross-references

- [`jmodot`](../jmodot/SKILL.md) — Combat / Status / HSM subsystem deep-dive.
- [`architecture_philosophy`](../architecture_philosophy/SKILL.md) — HSM Routes; Physics Drives meta-principle (transitions own determination, not external detector flipping BB flag).
- [`testing`](../testing/SKILL.md) — POB test mandatory if the effect has observable behavior.
- Search auto-memory for HSM transition routing and missing-runner wiring before diagnosing an effect that will not start.
