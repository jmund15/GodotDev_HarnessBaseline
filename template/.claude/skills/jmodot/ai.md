# AI Systems

## Hierarchical State Machine (HSM)

### Why This Exists
Declarative, data-driven state logic. States and transitions are defined as Resources in the editor, not hardcoded if/else chains. This makes behavior visible, editable, and reusable.

### Mental Model
States are Nodes with exported Transition resources. Each frame, the active state checks its transitions in order - first one where all conditions pass triggers a transition. Conditions are pure functions that read state but never modify it.

```
State checks Transitions[] → each Transition has Conditions[] → first all-true wins
```

### HSM vs Behavior Tree - When to Use Which

| Use HSM when... | Use Behavior Tree when... |
|-----------------|---------------------------|
| Mode-switching (Idle → Combat → Flee) | Complex action sequences |
| Clear state boundaries | Reactive, interruptible tasks |
| Transitions based on world state | Priority-based decisions |
| One thing at a time | Parallel behaviors needed |

### Rules

1. **Transitions are stateless** - They're shared resources, don't store per-instance data
2. **Conditions are pure** - Read BB/world state, never modify it
3. **`Urgent` bypasses `CanExit()`** - Use for interrupts (damage, stun)
4. **First match wins** - Order transitions by priority

### Anti-Patterns

**Logic in conditions:**
```csharp
// BAD - condition has side effects
public override bool Check(Node agent, IBlackboard bb) {
    bb.Set(BBDataSig.SelfInteruptible, true);  // NO! Check() must be side-effect-free
    return true;
}
```
Deferred side effects (e.g. consuming a one-shot flag) belong in `OnTransitionCommitted(agent, bb)` — fires only after the transition fully commits. See `rules/hsm_bt_patterns.md`.

**Mutable transitions** - Transitions are shared across instances. Don't store runtime data in them.

---

## Behavior Trees

### Why This Exists
For complex, sequential action chains that need to be interruptible. Better than HSM when you have "do A, then B, then C" logic that can fail/restart at any step.

### Mental Model
Tree of tasks evaluated top-down. Composites (Sequence, Selector) control flow. Leaf tasks do actual work. Each task returns Success/Failure/Running.

### Composites

| Composite | Behavior |
|-----------|----------|
| **Sequence** | Run children in order. Fail on first failure. |
| **Selector** | Run children in order. Succeed on first success. |
| **Parallel** | Run all children simultaneously. |

### Rules

1. **Tasks are stateless between runs** - Reset state in `Enter()`
2. **Return Running for multi-frame work** - Don't block in `ProcessFrame()`
3. **Exit cleans up** - Cancel animations, timers, etc.

---

## Perception System

### Why This Exists
Decouples "sensing" from "deciding". Sensors fire events when they detect things. A memory bank stores what was seen and decays over time.

### Mental Model
```
Sensors fire PerceptUpdated → PerceptionManager stores in memory → memory decays → AI queries memory
```

This allows AI to "forget" things not recently seen, enabling behaviors like search patterns when target is lost.

### Rules

1. **Sensors are autonomous** - They fire events, don't care who's listening
2. **Memory has decay** - Configure decay strategy per perception type
3. **Query memory, not sensors** - AI decisions use the memory bank

---

## Navigation & Steering

### Why This Exists
Utility-based movement decisions. Instead of "move toward target", you have multiple Considerations that score each direction. Highest score wins.

### Mental Model
```
Considerations score directions → Modifiers apply personality → sum scores → pick best direction
```

### Key Concepts

- **Consideration**: Scores directions based on world state (seek target, avoid danger)
- **Modifier**: Adjusts scores based on personality (aggressive AI weights attack directions higher)
- **DirectionSet**: The discrete directions being scored — a `DirectionSet3D` Resource family (`Dir4`/`Dir8`/`Dir16`/`Custom` variants, 2D mirrors exist)

### Rules

1. **Considerations are stateless** - Same input → same scores
2. **Modifiers are personality** - Use them to differentiate AI types
3. **Sum all considerations** - Final direction is highest combined score

---

## Utility AI - Composite Considerations

### Why This Exists
Complex utility calculations often need to combine multiple factors. Instead of writing custom code for each combination, `CompositeConsideration` provides flexible composition modes.

### Composition Operators

Enum: `ConsiderationOperator` in `Jmodot/Implementation/AI/UtilityAI/CompositeConsideration.cs` — 11 values (cite the file, don't trust stale tables):

| Operator | Formula | Use When |
|----------|---------|----------|
| **Average** | `Σ(scores)/n` | Balanced blend of factors (**exported default**) |
| **Add / Subtract / Multiply / Divide** | pairwise fold | Code comments suggest 2 considerations only |
| **Min** | `min(scores)` | Bottleneck determines outcome |
| **Max** | `max(scores)` | Any good reason suffices |
| **WeightedAverage** | `Σ(score*weight)/Σ(weight)` | Some factors matter more (missing weights default 1.0; empty `Weights` → plain average) |
| **Veto** | `0 if any≤0, else multiply` | Hard requirements that block if missing |
| **ThresholdGate** | `0 if any<Threshold, else average` | Filter low-confidence options (`Threshold` default 0.1) |
| **Random** | Per-agent deterministic child pick | Personality variety. NOT per-call randomness — derived from `BBDataSig.EntitySeed` via `EntityRngResolver`, so the pick is FIXED per agent (seed-determinism discipline, see `rules/jmodot_utilities.md` §JmoRng) |

Final result is clamped to [0, 1].

### Usage Examples

```csharp
// Veto: Any zero vetoes the whole thing
CompositeConsideration (Operator: Veto)
├── HasAmmoConsideration → 0.0  ← VETO!
├── HealthConsideration → 0.8
└── TargetDistanceConsideration → 0.9
Result: 0.0 (can't attack without ammo)

// WeightedAverage: Prioritize certain factors
CompositeConsideration (Operator: WeightedAverage)
├── DamageOutput (weight: 2.0) → 0.6
├── Risk (weight: 1.0) → 0.4
└── Cooldown (weight: 0.5) → 0.8
Result: (0.6*2 + 0.4*1 + 0.8*0.5) / 3.5 = 0.57

// ThresholdGate: Filter weak options
CompositeConsideration (Operator: ThresholdGate, Threshold: 0.3)
├── ConfidenceConsideration → 0.2  ← Below threshold!
├── OpportunityConsideration → 0.8
└── SafetyConsideration → 0.7
Result: 0.0 (confidence too low)
```

### Properties

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `Considerations` | `Array<UtilityConsideration>` | empty | Child considerations to combine (empty → score 0) |
| `Operator` | `ConsiderationOperator` | `Average` | How to combine scores |
| `Weights` | `Array<float>` | empty | Per-child weights (WeightedAverage only) |
| `Threshold` | `float` | `0.1` | Gate threshold (ThresholdGate only) |

---

## AIAgentComponent - Auto-Wiring

### Why This Exists
AI entities require boilerplate to wire components (Stats, Health, Affinities) to the Blackboard. `AIAgentComponent` eliminates this repetition by auto-discovering and wiring components.

### Before (Manual Wiring)
```csharp
public override void _Ready() {
    // Repeated for EVERY AI entity
    _blackboard.Set(BBDataSig.Agent, this);
    _blackboard.Set(BBDataSig.Affinities, _affinities);
    _blackboard.Set(BBDataSig.Stats, _stats);
    _blackboard.Set(BBDataSig.HealthComponent, _health);
}
```

### After (Auto-Wiring)
```
EnemyWizard (CharacterBody3D)
  ├── AIAgentComponent      ← Just add this!
  ├── AIAffinitiesComponent
  ├── StatController
  ├── HealthComponent
  └── HSM
```

No manual wiring code needed - `AIAgentComponent` handles it automatically.

### What It Wires

| Component | BB Key | Found Via |
|-----------|--------|-----------|
| Parent node | `BBDataSig.Agent` | Always wired |
| `AIAffinitiesComponent` | `BBDataSig.Affinities` | `TryGetFirstChildOfType<>` |
| `IStatProvider` | `BBDataSig.Stats` | `TryGetFirstChildOfInterface<>` |
| `IHealth` | `BBDataSig.HealthComponent` | `TryGetFirstChildOfInterface<>` |

### Interface: IAIAgent

```csharp
public interface IAIAgent : IGodotNodeInterface {
    IBlackboard Blackboard { get; }
    AIAffinitiesComponent? Affinities { get; }
    IStatProvider? Stats { get; }
    IHealth? Health { get; }
    void Initialize();
}
```

### Rules

1. **Add as child of AI entity** - Discovers siblings, not children
2. **Missing components are OK** - Logs what was found, doesn't fail
3. **Manual wiring still works** - Coexists with legacy patterns
4. **Call `Initialize()` in tests** - `_Ready()` calls it automatically

---

## Stat-Driven AI

### Why This Exists
AI capabilities (perception range, reaction speed, turn rate) should be driven by the stat system. This enables buffs/debuffs that affect AI behavior and creates personality variation through stat differences.

### Key Components

| Component | Type | Purpose |
|-----------|------|---------|
| `StatConsideration` | Utility AI | Score based on stat value (normalized 0-1) |
| `StatConsiderationModifier` | Utility Modifier | Scale scores by stat via curve |
| `StatDrivenConsideration3D` | Steering | Stat-driven behavior parameters (e.g., sight range) |

AI attribute `.tres` inventory + semantics: [stat_driven_ai.md](stat_driven_ai.md) (single home — full API documentation there).

---

## Squad Formation System

### Why This Exists
Coordinated movement for groups of AI agents. Data-driven formations integrate with the steering consideration pipeline, allowing squads to maintain shapes while pursuing objectives.

### Architecture

```
                    SquadRoster  (membership + owns the squad graph)
                         │ events
                         ▼
FormationDefinition → FormationController → FormationCoordinator → Blackboard
                                                                       ↓
                                                     FormationConsideration3D

SquadPolicy chain → SquadDirectiveBrain → Blackboard (SquadDirective)
                                                ↓
                                      SquadDirectiveCondition (HSM)
```

### Key Components

| Component | Type | Purpose |
|-----------|------|---------|
| `FormationDefinition` | Resource (`Jmodot/Core/AI/Squad/`) | `SlotOffsets`, `MinSpacing` (inert authoring metadata — no runtime consumer), `FormationName`; slot 0 = leader by convention (no `LeaderSlotIndex`/`Metadata` properties exist) |
| `FormationController` | Static pure functions (`Jmodot/Implementation/AI/Squad/`) | `CalculateSlotPositions(formation, FormationAnchorMode, anchorPosition, anchorForward, memberPositions?)` → `Dictionary<int, Vector3>`. Anchor modes: `Leader` / `Static` / `Centroid`. Transform math treats local **-Z as forward** (`Basis.LookingAt`) |
| `NearestSlotStrategy` | `ISlotAssignmentStrategy` | `AssignSlots(memberPositions, slotPositions, leaderMemberIndex = -1)` → `Dictionary<int, int>` (member→slot; leader gets slot 0; unassigned = -1). Slot assignment lives HERE, not on FormationController |
| `FormationConsideration3D` | Steering (`Jmodot/Implementation/AI/Navigation/Considerations/`) | Guide agents toward their assigned slots |
| `SquadRoster` | Node (`Jmodot/Implementation/AI/Squad/`) | Membership + owns the floating squad `BlackboardGraph`; raises `MemberAdded`/`MemberRemoved(Node3D, IBlackboardGraph)`/`LeaderChanged`. `MemberRemoved` carries the graph registered at enrolment, which a child-tree walk cannot recover. Never author a `BlackboardGraph` child under it |
| `FormationCoordinator` | Node (`Jmodot/Implementation/AI/Squad/`) | Runs the strategy, publishes formation keys to BB. Holds no member list — reads the roster |
| `SquadDirectiveBrain` | Node (`Jmodot/Implementation/AI/Squad/`) | Walks an ordered `SquadPolicy` chain, publishes `SquadDirective` (change-gated + min-hold) |
| `SquadPolicy` / `HealthThresholdPolicy` | Resource (`Jmodot/Core/AI/Squad/`, impl in `Implementation/AI/Squad/`) | Stateless `Evaluate(in SquadSnapshot)` → directive or `null` (abstain). **Shared instances — no mutable fields** |
| `SquadSnapshotBuilder` / `SquadSnapshot` | Static + `readonly record struct` | Roster → immutable per-evaluation aggregate handed to policies |
| `DebugFormationComponent` | Node | Visual debug drawing; child of `SquadRoster` |

### Blackboard Keys (framework BBDataSig partial)

`FormationActive` (`bool`), `FormationSlotPositions` (`Dictionary<int, Vector3>`, squad BB), `FormationSlotIndex` (`int`, member BB, -1 = unassigned), `FormationLeader` (`Node3D`, squad BB), `SquadDirective` (`SquadDirectiveDefinition`, squad BB).

Retired 2026-07-18 by the `SquadManager` decomposition: `ActiveSquadTag`, `HasSquadTag`, `SquadAverageHealth`.

See [squad_formations.md](squad_formations.md) for the full deep-dive.

---

## Integration Points

- HSM states receive **BB** in `Init()` - query for services
- States can run **BT** for complex sub-behaviors (BTState)
- Perception feeds into **BB** values that conditions check
- Navigation outputs to **MovementProcessor** for execution
- **AIAgentComponent** auto-wires components to BB at `_Ready()`
