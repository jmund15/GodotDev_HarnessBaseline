# Stat-Driven AI

## Overview

Stat-driven AI enables AI behavior parameters to be controlled by the stat system. This allows:
- **Buffs/Debuffs** that affect AI capabilities (e.g., "Blinded" reduces sight range)
- **Personality variation** through stat differences (aggressive enemies have higher reaction speeds)
- **Designer tuning** via stat sheets rather than code changes

## AI Attributes

Located in `Global/Attributes/AI/`:

| Attribute | File | Type | Description |
|-----------|------|------|-------------|
| `SightRange` | `sight_range.tres` | `float` | Perception/detection range in meters |
| `ReactionTime` | `reaction_time.tres` | `float` | Decision delay in seconds |
| `TurnRate` | `turn_rate.tres` | `float` | Steering strength in degrees/second |

### Designer-Intuitive Values

Following the project philosophy, stat values should be **semantically meaningful**:
- `SightRange = 15` means 15 meters detection range
- `TurnRate = 90` means 90° per second max turn rate
- `ReactionTime = 0.5` means 500ms between decisions

## StatConsideration (Utility AI)

Scores utility based on a stat value, normalized to 0-1 range.

### Location
`Jmodot/Implementation/AI/UtilityAI/Considerations/StatConsideration.cs`

### Properties

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `TargetAttribute` | `Attribute` | - | The stat to read |
| `MinValue` | `float` | 0 | Maps to score 0 |
| `MaxValue` | `float` | 100 | Maps to score 1 |
| `InvertScore` | `bool` | false | Flip the score (low stat = high score) |
| `ResponseCurve` | `Curve?` | null | Non-linear response transformation |

### Formula

```
normalized = clamp((value - MinValue) / (MaxValue - MinValue), 0, 1)
if (ResponseCurve != null) normalized = ResponseCurve.Sample(normalized)
if (InvertScore) normalized = 1 - normalized
return normalized
```

### Example Usage

```
UtilityAction (Attack)
├── TargetInRangeConsideration
├── StatConsideration           ← Health-based attack urgency
│   ├── TargetAttribute: health
│   ├── MinValue: 0
│   ├── MaxValue: 100
│   └── InvertScore: true       ← Low health = higher attack priority
└── HasAmmoConsideration
```

## StatConsiderationModifier (Utility Modifier)

Modifies utility scores based on stat values. Useful for personality-driven behavior scaling.

### Location
`Jmodot/Implementation/AI/UtilityAI/Modifiers/StatConsiderationModifier.cs`

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `TargetAttribute` | `Attribute` | The stat to read |
| `MinValue` | `float` | Maps to X=0 on curve |
| `MaxValue` | `float` | Maps to X=1 on curve |
| `ResponseCurve` | `Curve` | **Required** - Translates stat to multiplier |

### Example Curves

**Stamina Gate** (Y=0 until X>0.3, then Y=1):
- Low stamina completely blocks the action
- Above 30% stamina, action is available

**Rage Amplifier** (Y increases exponentially with X):
- As health drops (X decreases), damage multiplier increases
- Creates "cornered beast" behavior

## StatDrivenConsideration3D (Steering)

A steering consideration that reads stat values to parameterize its behavior.

### Location
`Jmodot/Implementation/AI/Navigation/Considerations/StatDrivenConsideration3D.cs`

### Properties

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `InfluenceRangeAttribute` | `Attribute?` | null | Stat controlling influence range |
| `DefaultInfluenceRange` | `float` | 15.0 | Fallback when stat unavailable |
| `_weight` | `float` | 1.0 | Score multiplier |
| `_arrivalRadius` | `float` | 1.5 | "Arrived" threshold |
| `_propagateScores` | `bool` | true | Smooth score propagation |

### How It Works

1. Reads `CurrentTarget` position from blackboard
2. Gets influence range from stats (or uses default)
3. If target is outside range, returns zero scores
4. Otherwise, scores directions based on alignment with target direction
5. Distance-based urgency: further = higher score

### Blackboard Requirements

| Key | Type | Required |
|-----|------|----------|
| `BBDataSig.CurrentTarget` | `Vector3` | Yes |
| `BBDataSig.Stats` | `IStatProvider` | No (uses default) |

## Integration with Perception

While not implemented in this phase, the intended integration pattern:

```
PerceptionManager
    ↓ reads
SightRange stat → determines sensor radius
    ↓ detects
Percepts → stored in memory
    ↓ queries
AI decisions based on what's in range
```

## Test Coverage

| Suite | Tests | Description |
|-------|-------|-------------|
| `StatConsiderationTests` | 6 | Normalization, curves, inversion, edge cases |
| `StatDrivenConsideration3DTests` | 3 | Stat integration, defaults, range gating |
