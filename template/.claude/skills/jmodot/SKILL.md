---
name: Jmodot Framework
description: >-
  Auto-load when proposing systems that touch Jmodot's Combat / AI / Movement / Stats
  surface, looking up BBDataSig keys, or diagnosing why an IComponent silently no-ops.
  Triggers: "Jmodot", "Blackboard", "BBDataSig", "IComponent", "BehaviorTree", "Combat
  factory", "MovementProcessor", "EntityStatSheet", "Attribute", "squad formation".
  SKIP for project subsystem ownership (project_subsystems), design-pattern decisions
  (architecture_philosophy), and submodule git mechanics (rules/jmodot_submodule.md auto-loads).
---

# Jmodot Framework

Reference index for the framework submodule. Most mechanical content has been extracted into path-scoped rules that auto-load on file reads.

## Framework/consumer seam (applies to every deep-dive below)

The deep-dives document Jmodot's **generic framework surface**. The consuming project reaches it through two seams: **attribute resources** resolved via `GlobalRegistry.DB` (autoload `Global/GlobalRegistry.cs` exposing `PushinPotionRegistry` — e.g. `GlobalRegistry.DB.MaxSpeedAttr`), and **project-side BBDataSig keys** (the project partial, below). Jmodot code MUST NOT reference `{{PROJECT_NAME}}.*` — project defaults flow through static seams (`rules/jmodot_framework_authoring.md`, auto-loads on `Jmodot/**/*.cs`).

## Companion files

| File | Load mode | Audience |
|---|---|---|
| [`../../rules/jmodot_utilities.md`](../../rules/jmodot_utilities.md) | Auto-loads on `**/*.cs` | Consumers of Jmodot utilities (NodeExts, JmoRng, JmoMath, Map, IRuntimeCopyable, configuration exceptions, IComponent gotcha) |
| [`../../rules/jmodot_framework_authoring.md`](../../rules/jmodot_framework_authoring.md) | Auto-loads on `Jmodot/**/*.cs` | Framework authors (2D/3D parity, framework boundary, static seam pattern) |
| [`../../rules/csharp_patterns.md`](../../rules/csharp_patterns.md) | Auto-loads on `**/*.cs` | `[RequiredExport]`, nullability, signals vs events, test helpers |
| [`../architecture_philosophy/SKILL.md`](../architecture_philosophy/SKILL.md) | Skill (design-time) | Blackboard DI, Resource Strategy Hierarchies, Marker Interface as Capability Query — the design philosophy this framework embodies |

Subsystem deep-dives (read on demand when designing in a specific area):

| Subsystem | Reference |
|---|---|
| Components / Blackboard DI | [components.md](components.md) |
| AI (HSM / BT / Perception / Navigation / Agent) | [ai.md](ai.md) |
| Combat / Status / Health | [combat.md](combat.md) |
| Stats / Modifiers | [stats.md](stats.md) |
| Movement | [movement.md](movement.md) |
| Squad / Formations | [squad_formations.md](squad_formations.md) |
| Stat-Driven AI | [stat_driven_ai.md](stat_driven_ai.md) |

## System Overview

| System | Purpose | Key Interface | Reference |
|--------|---------|---------------|-----------|
| **Components** | Blackboard-based DI | `IComponent`, `IBlackboard` | [components.md](components.md) |
| **AI - HSM** | Declarative state machines | `IState`, `StateTransition` | [ai.md](ai.md) |
| **AI - BT** | Reactive task execution | `IBehaviorTask` | [ai.md](ai.md) |
| **AI - Perception** | Sensor networks + memory | `IAISensor3D`, `Percept3D` | [ai.md](ai.md) |
| **AI - Navigation** | Utility-based steering | `BaseAIConsideration3D` | [ai.md](ai.md) |
| **AI - Agent** | Auto-wired AI setup | `IAIAgent`, `AIAgentComponent` | [ai.md](ai.md) |
| **Combat** | Data-driven effects | `ICombatEffect`, `ICombatant` | [combat.md](combat.md) |
| **Status Effects** | Temporal buffs/debuffs | `StatusRunner` | [combat.md](combat.md) |
| **Health** | Life/death state | `IHealth`, `IDamageable` | [combat.md](combat.md) |
| **Stats** | Character sheet | `EntityStatSheet`, `IStatProvider` | [stats.md](stats.md) |
| **Modifiers** | Stat calculation pipeline | `IModifier`, `IModifiableProperty` | [stats.md](stats.md) |
| **Movement** | Physics control + strategies | `ICharacterController3D`, `IMovementStrategy3D` | [movement.md](movement.md) |

## BBDataSig Quick Reference

`BBDataSig` is ONE `static partial class` in namespace `Jmodot.Implementation.AI.BB`, split across **two files**:

| Partial | File | Holds |
|---|---|---|
| Framework | `Jmodot/Implementation/AI/BB/BBDataSig.cs` | `Agent`, `Stats`, `HealthComponent`, `CombatLog`, `CurrentTarget`, `Formation*`, `EntitySeed`, … |
| Project game | `AI/BB/BBDataSig.cs` (repo root) | Casting/crafting/spell/run-scope keys (`CastStarted`, `CraftingSession`, `RunCurrencyAggregator`, …) |

Grepping one file for the other's keys returns nothing — **search both** (pinning test: `Tests/Logic/JmodotTests/BBDataSigFrameworkGrabKeysTest.cs`). Constant name ≠ runtime string in places: `BBDataSig.StatusEffects` → `"StatusEffectComponent"`, `BBDataSig.HolderComponent` → `"HandMovementComponent"` — match on the constant, not the string. When new keys are added, refresh this table.

| Key | Type | Purpose |
|-----|------|---------|
| `BBDataSig.Agent` | `Node` | The owning agent node |
| `BBDataSig.Stats` | `IStatProvider` | Stat calculations |
| `BBDataSig.CharacterController` | `ICharacterController3D` | Physics driver |
| `BBDataSig.MovementProcessor` | `IMovementProcessor3D` | Movement logic |
| `BBDataSig.HealthComponent` | `IHealth` | Health state |
| `BBDataSig.CombatantComponent` | `ICombatant` | Combat receiver |
| `BBDataSig.StatusEffects` | `StatusEffectComponent` | Active buffs/debuffs |
| `BBDataSig.IntentSource` | `IIntentSource` | Input provider |
| `BBDataSig.AnimationComponent` | `IAnimComponent` | Animation control |
| `BBDataSig.SelfInteruptible` | `bool` | Can self-interrupt current state |

## See Also

- **Force vs velocity offset semantics** (when to use which, equilibrium formula) → [movement.md](movement.md) §"The Key Decision: Force vs Velocity Offset".
- **Architectural principles this framework embodies** (Blackboard decoupling, data-driven design, pure-function strategies, validation, logging) → [`../architecture_philosophy/SKILL.md`](../architecture_philosophy/SKILL.md). Project-wide conventions (StringName keys, `JmoLogger` not `GD.Print`) live in `CLAUDE.md` §"Core Code Conventions".
- **Configuration exception throwing convention** (`NodeConfigurationException`, `ResourceConfigurationException`) → [`../../rules/jmodot_utilities.md`](../../rules/jmodot_utilities.md) (auto-loads on `.cs`).

## Provenance & maintenance

Facts verified 2026-07-04. Re-verify:
- BBDataSig split + keys: `Grep "partial class BBDataSig" -g "*.cs"` (expect the two files above), then read both.
- Quick-ref key existence: `Grep "<KeyName> = new" -g "*BBDataSig.cs"`.
- Seam accessor: `Grep "public static PushinPotionRegistry DB" Global/GlobalRegistry.cs`.
