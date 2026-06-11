# Squad Formation System

## Overview

The Squad Formation System provides coordinated movement for groups of AI agents. Formations are data-driven Resources that define slot positions, and agents are dynamically assigned to slots using the steering consideration pipeline.

## Architecture

```
┌─────────────────────┐
│ FormationDefinition │  ← Resource (.tres) defining slot offsets
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ FormationController │  ← Static methods: CalculateSlotPositions, AssignSlots
└──────────┬──────────┘
           │ writes to
           ▼
┌─────────────────────┐     ┌──────────────────────────┐
│   Squad Blackboard  │────▶│ FormationConsideration3D │
│  (shared by squad)  │     │   (per-agent steering)   │
└─────────────────────┘     └──────────────────────────┘
```

## FormationDefinition Resource

Defines the shape and configuration of a formation.

### Location
`Jmodot/Implementation/AI/Formations/FormationDefinition.cs`

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `FormationName` | `string` | Human-readable identifier |
| `SlotOffsets` | `Vector3[]` | Local-space positions relative to formation center |
| `LeaderSlotIndex` | `int` | Which slot is the leader (default: 0) |
| `Metadata` | `Dictionary<string, Variant>` | Custom data for behaviors |

### Pre-built Formations

Located in `Global/Formations/`:

| File | Shape | Slots | Use Case |
|------|-------|-------|----------|
| `line_formation.tres` | Line | 5 | Corridor movement |
| `wedge_formation.tres` | V-shape | 5 | Aggressive advance |
| `defensive_circle.tres` | Circle | 6 | Protection/holdout |

### Creating Custom Formations

```csharp
var formation = new FormationDefinition {
    FormationName = "Diamond",
    SlotOffsets = new[] {
        new Vector3(0, 0, -2),   // Front (leader)
        new Vector3(-2, 0, 0),   // Left
        new Vector3(2, 0, 0),    // Right
        new Vector3(0, 0, 2),    // Rear
    },
    LeaderSlotIndex = 0
};
```

## FormationController (Static Methods)

Provides the calculation logic for formations without instance state.

### Location
`Jmodot/Implementation/AI/Formations/FormationController.cs`

### Key Methods

#### CalculateSlotPositions
```csharp
public static Dictionary<int, Vector3> CalculateSlotPositions(
    FormationDefinition formation,
    Vector3 anchorPosition,
    Vector3 facingDirection)
```
Transforms local slot offsets to world positions based on anchor and facing.

#### AssignSlots
```csharp
public static Dictionary<T, int> AssignSlots<T>(
    IReadOnlyList<T> members,
    FormationDefinition formation,
    ISlotAssignmentStrategy<T> strategy)
```
Assigns members to slots using the provided strategy.

## NearestSlotStrategy

The default slot assignment algorithm. Minimizes total travel distance.

### Location
`Jmodot/Implementation/AI/Formations/Strategies/NearestSlotStrategy.cs`

### Algorithm

1. Create list of (member, slotIndex, distance) tuples for all combinations
2. Sort by distance (ascending)
3. Greedily assign: pick shortest distance, mark member and slot as used
4. Repeat until all members assigned or slots exhausted

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `PositionAccessor` | `Func<T, Vector3>` | How to get member's position |
| `SlotPositions` | `Dictionary<int, Vector3>` | World-space slot positions |

## FormationConsideration3D (Steering)

A steering consideration that guides agents toward their assigned formation slots.

### Location
`Jmodot/Implementation/AI/Navigation/Considerations/FormationConsideration3D.cs`

### Properties

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `_formationWeight` | `float` | 1.0 | Score multiplier |
| `_excludeLeader` | `bool` | true | Leader doesn't follow formation |
| `_arrivalRadius` | `float` | 1.5 | "Arrived" at slot threshold |
| `_maxInfluenceDistance` | `float` | 20.0 | Max steering range |
| `_propagateScores` | `bool` | true | Smooth neighboring scores |

### Blackboard Requirements

Read from **Squad Blackboard** (via parent chain):
| Key | Type | Description |
|-----|------|-------------|
| `BBDataSig.FormationActive` | `bool` | Is formation mode enabled? |
| `BBDataSig.FormationSlotPositions` | `Dictionary<int, Vector3>` | World positions of all slots |

Read from **Agent Blackboard**:
| Key | Type | Description |
|-----|------|-------------|
| `BBDataSig.FormationSlotIndex` | `int` | This agent's assigned slot |

### Score Calculation

1. Check `FormationActive` is true
2. Get this agent's slot index
3. If leader and `_excludeLeader`, return zeros
4. Calculate direction to slot position
5. Score = weight × (distance / maxDistance) × alignment
6. Apply score propagation for smooth steering

## DebugFormationComponent

Visual debugging component for formations.

### Location
`Jmodot/Implementation/AI/Formations/Debug/DebugFormationComponent.cs`

### Usage

Add as child of any node. Call `DrawFormation()` with formation data.

### Visualization

- **Green spheres**: Slot positions
- **Red sphere**: Leader slot
- **Blue lines**: Connections between adjacent slots
- **Labels**: Slot indices

## Blackboard Key Reference

| Key | Type | Scope | Set By |
|-----|------|-------|--------|
| `FormationActive` | `bool` | Squad | SquadManager/HSM State |
| `FormationSlotPositions` | `Dictionary<int, Vector3>` | Squad | FormationController |
| `FormationSlotIndex` | `int` | Agent | SlotAssignmentStrategy |
| `FormationLeaderTarget` | `Vector3?` | Squad | Leader's decision system |

## Integration Example

```csharp
// In SquadManager or formation-controlling state:

// 1. Calculate world positions
var positions = FormationController.CalculateSlotPositions(
    _activeFormation,
    leaderPosition,
    leaderFacing);

// 2. Assign slots to members
var strategy = new NearestSlotStrategy<IAIAgent>(
    agent => agent.Blackboard.Get<Node>(BBDataSig.Agent).GlobalPosition,
    positions);
var assignments = FormationController.AssignSlots(_members, _activeFormation, strategy);

// 3. Update blackboards
_squadBlackboard.Set(BBDataSig.FormationActive, true);
_squadBlackboard.Set(BBDataSig.FormationSlotPositions, positions);
foreach (var (member, slotIndex) in assignments)
{
    member.Blackboard.Set(BBDataSig.FormationSlotIndex, slotIndex);
}

// 4. Steering considerations automatically take over
```

## Test Coverage

| Suite | Tests | Description |
|-------|-------|-------------|
| `FormationConsiderationTest` | 9 | Steering behavior validation |
| `FormationControllerTest` | 8 | Position calculation, slot assignment |
| `NearestSlotStrategyTest` | 7 | Assignment algorithm correctness |
