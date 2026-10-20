# Squad Formation System (Jmodot)

Use this reference when wiring Jmodot's coordinated group movement. It is split across a two-node **squad stack**: `SquadRoster` (membership and squad-graph lifetime) and `FormationCoordinator` (formation state onto Blackboards). Around them sit a data-driven shape (`FormationDefinition` Resource), pure slot math (`FormationController`), pluggable member-to-slot assignment (`ISlotAssignmentStrategy`) and a steering consideration (`FormationConsideration3D`) that pulls each member toward its assigned slot. A parallel `SquadDirectiveBrain` publishes a squad-wide *directive* from an ordered `SquadPolicy` chain.

**Code and XML summaries are authoritative.** This is a verified map, not a signature mirror; code wins when they disagree. Locate the current declarations before relying on it.

## Source files

Paths are under the Jmodot framework root.

| File | Role |
|---|---|
| `Core/AI/Squad/FormationDefinition.cs` | `[GlobalClass] [Tool]` Resource. Exports `Vector3[] SlotOffsets`, `float MinSpacing` (1.5, **inert authoring metadata**: it records the spacing already baked into `SlotOffsets`; assignment strategies never receive the definition, so they cannot enforce it), `string FormationName`; computed `int SlotCount` |
| `Core/AI/Squad/FormationAnchorMode.cs` | enum `Leader` / `Centroid` / `Static` |
| `Core/AI/Squad/ISlotAssignmentStrategy.cs` | non-generic strategy contract |
| `Implementation/AI/Squad/FormationController.cs` | static, pure: `CalculateSlotPositions(...)` |
| `Implementation/AI/Squad/NearestSlotStrategy.cs` | greedy nearest-first assignment, O(n^2), leader to slot 0 first |
| `Implementation/AI/Squad/SquadRoster.cs` | membership Node. Owns the floating squad `BlackboardGraph`, attaches member graphs, raises `MemberAdded` / `MemberRemoved(Node3D, IBlackboardGraph)` / `LeaderChanged`. Per-member bookkeeping lives in one private `MemberRecord`; `_members` and `_memberGraphs` stay separate lists because their index alignment is a published contract |
| `Implementation/AI/Squad/FormationCoordinator.cs` | formation Node. The only writer of formation BB state; holds no member list, reads the roster and reacts to its events |
| `Implementation/AI/Squad/SquadDirectiveBrain.cs` | directive Node. Walks the `SquadPolicy` chain and publishes `SquadDirective` (change-gated, with min-hold flap protection) |
| `Implementation/AI/Squad/SquadSnapshotBuilder.cs` | static, pure: roster to immutable `SquadSnapshot` |
| `Implementation/AI/Squad/HealthThresholdPolicy.cs` | reference `SquadPolicy`: prescribes a directive below a mean-health fraction, abstains at or above |
| `Core/AI/Squad/SquadPolicy.cs` | abstract stateless strategy Resource: `Evaluate(in SquadSnapshot)` returns a directive or `null` (abstain) |
| `Core/AI/Squad/SquadDirectiveDefinition.cs` | directive vocabulary Resource |
| `Core/AI/Squad/SquadSnapshot.cs` | `readonly record struct`: `MemberCount`, `PeakMemberCount`, `AverageHealthFraction`, `Leader`, `CurrentDirective`, `TimeSinceDirectiveChangeSeconds` |
| `Implementation/AI/HSM/TransitionConditions/SquadDirectiveCondition.cs` | HSM transition condition reading `SquadDirective` up the graph chain |
| `Implementation/AI/Navigation/Considerations/FormationConsideration3D.cs` | steering consideration (extends `BaseAIConsideration3D`) |
| `Implementation/AI/Squad/DebugFormationComponent.cs` | slot and assignment visualization through the `DebugDraw3D` addon; a child of `SquadRoster` (resolves it with `GetParentOrNull<SquadRoster>()`) |
| `Implementation/AI/BB/BBDataSig.cs` | the four formation BB keys plus `SquadDirective` (framework-side partial) |

Do not copy paths or line numbers from another consumer project.

## How it works

1. **Shape.** `FormationDefinition.SlotOffsets` are local-space offsets. Slot 0 is the leader by convention (no property marks it). With `Leader` anchor mode, slot 0's offset should be `Vector3.Zero`.
2. **Slot world positions.** `FormationController.CalculateSlotPositions(formation, anchorMode, anchorPosition, anchorForward, memberPositions = null)` returns `Dictionary<int, Vector3>`. The anchor is `anchorPosition` (Leader/Static) or the member centroid (Centroid; it falls back to `anchorPosition` when `memberPositions` is null or empty). Offsets rotate so local **-Z** aligns with `anchorForward` (`Basis.LookingAt`).
3. **Assignment.** `ISlotAssignmentStrategy.AssignSlots(IReadOnlyList<Vector3> memberPositions, IReadOnlyDictionary<int, Vector3> slotPositions, int leaderMemberIndex = -1)` returns `Dictionary<int, int>` mapping member index to slot index; `-1` means unassigned. `NearestSlotStrategy` pins `leaderMemberIndex` to slot 0, then each remaining member greedily takes its nearest free slot.
4. **State onto Blackboards.** `SquadRoster.AddMember` attaches the member graph to the squad graph (`AttachParent`) so members can read squad state up the hierarchy. `FormationCoordinator` writes the squad-scope formation keys and each member's `FormationSlotIndex`. The coordinator resolves the squad graph per use through `_roster.SquadGraph` and **never caches it**; `Disband` disposes it.
5. **Steering.** `FormationConsideration3D` (in each member's steering set) scores directions toward the assigned slot. It returns zero when formation is inactive, the slot is unassigned, the member holds slot 0 **while the squad has a designated leader**, it is inside `_arrivalRadius`, or it is beyond `_maxInfluenceDistance`. Otherwise it scores `Weight x clamp((distance - _arrivalRadius) / (_fullPullDistance - _arrivalRadius), 0, 1) x alignment` (positive dot only), flattened on Y for ground movement. The ramp saturates at `_fullPullDistance`, so `Weight` stays the sole cross-consideration magnitude knob.

## Coordinate convention

**Local formation space: -Z is forward (Godot convention); +Z is behind, +X is right.** The implementation pins it through `Basis.LookingAt` in the rotation-basis helper, and the controller's tests assert it. If a source comment disagrees with behavior and tests, fix the framework comment rather than teaching the stale claim here.

## The squad stack: orchestration contract

Two sibling Nodes. `FormationCoordinator` and `SquadDirectiveBrain` each take a `[Export, RequiredExport] SquadRoster _roster` back-reference; the coordinator subscribes to roster events in `_EnterTree` and unsubscribes in `_ExitTree`.

- **`SquadRoster`**: `Members`, `MemberGraphs` (index-aligned with `Members`), `Leader`, `PeakMemberCount`, `SquadGraph`, `AddMember(Node3D, bool isLeader = false)`, `AddMember(Node3D, IBlackboardGraph, bool isLeader = false)`, `RemoveMember(Node3D)`, `Disband()`; events `MemberAdded(Node3D)`, `MemberRemoved(Node3D, IBlackboardGraph)`, `LeaderChanged(Node3D?)`; export `_removeMembersOnDeath` (true).
- **`FormationCoordinator`**: `SetFormation(FormationDefinition, FormationAnchorMode)`, `ClearFormation()`, `UpdateFormationPositions(Vector3 anchorPosition, Vector3 anchorForward)`; exports `_roster`, `_defaultFormation`, `_anchorMode`.
- **`SquadDirectiveBrain`**: `CurrentDirective`, `Tick(float)`, `EvaluateNow()`; exports `_roster`, `_policies`, `_evaluateIntervalSeconds` (0.5), `_minDirectiveHoldSeconds` (1.0). It drives itself from `_PhysicsProcess`.

Consumer responsibilities; nothing else is automatic:

- **Do NOT author a `BlackboardGraph` child under `SquadRoster`.** The roster mints and owns a floating squad graph lazily; a scene-authored child is rejected with a warning and its contents are lost. Owner-managed lifetime is what lets `Disband()` dispose the subgraph.
- Optionally set the coordinator's `_defaultFormation` and `_anchorMode` exports; `_Ready` applies them through `SetFormation`.
- Call `UpdateFormationPositions(anchor, forward)` whenever the squad anchor moves. **There is no per-frame driver**, so slot world positions go stale otherwise. The internal slot reassignment on a membership, formation or leader change compares member positions against a zero-anchored slot set purely for assignment; real world positions come only from `UpdateFormationPositions`.
- Enroll members **only once they are inside the tree**; `AddMember` warns and no-ops otherwise.

## Blackboard keys

The framework-side `BBDataSig` partial owns these keys. Locate that partial before editing; a consuming project may define another partial elsewhere in the same namespace, so a search of the consumer's own file finds none of them.

| Key | Type | Scope | Written by |
|---|---|---|---|
| `FormationActive` | `bool` | squad graph | `FormationCoordinator.SetFormation` (true) / `ClearFormation` (false) |
| `FormationSlotPositions` | `Dictionary<int, Vector3>` | squad graph | `FormationCoordinator.UpdateFormationPositions` |
| `FormationLeader` | `Node3D` | squad graph | `FormationCoordinator`, off `SquadRoster.LeaderChanged` (**not** inside `AddMember`) |
| `FormationSlotIndex` | `int` (-1 = unassigned) | member graph | `FormationCoordinator`'s slot reassignment (through the strategy) |
| `SquadDirective` | `SquadDirectiveDefinition` | squad graph | `SquadDirectiveBrain.EvaluateNow` |

Squad health lives inside `SquadSnapshot` and reaches policies through the snapshot, not the blackboard; no member-readable aggregate-health channel exists.

Cross-scope reads use the member graph's parent chain. A graph-less Blackboard can only read local values and is suitable only for isolated fixtures.

## FormationConsideration3D exports

| Export | Default | Range | Meaning |
|---|---|---|---|
| `_excludeLeader` | true | none | slot 0 gets zero scores, but ONLY while `FormationLeader` resolves non-null up the graph; a leaderless squad still steers its slot-0 member |
| `_arrivalRadius` | 1.5 | 0.5-5.0 | inside it, no steering |
| `_fullPullDistance` | 4.0 | 0.5-20.0 | ramp saturation: at or past it the aligned bin scores the full `Weight` |
| `_maxInfluenceDistance` | 20.0 | 5.0-50.0 | hard cutoff: past it the member is lost from formation and the consideration contributes nothing |

## Gotchas

- A member graph not attached to the squad graph cannot see squad-wide formation state. `SquadRoster` does the `AttachParent`; bypass it and you own that wiring.
- `AddMember(Node3D)` must resolve the member's `BlackboardGraph`; otherwise it logs a warning and no-ops. Enroll AFTER `AddChild`.
- **A policy chain that all-abstains retains the current directive.** `SquadDirectiveBrain` contains no fallback logic by design, and `HealthThresholdPolicy` abstains at or above its threshold, so a chain of only threshold policies is a **one-way latch** (a squad that panics never recovers). Author an always-fires tail policy, or the recovery arm has no home.
- Slot 0 is the leader by convention, not by an authored `LeaderSlotIndex` field. The convention is enforced in exactly two places: `leaderMemberIndex` in the strategy and `_excludeLeader` in the consideration.
- If `FormationDefinition` lacks `[Tool]`, a typed export from a `[Tool]` Resource can hit the editor-only cast cascade. Fix the framework type or use the base-`Resource` escape hatch from `rules/csharp_patterns.md`.

## Authoring data

Store `FormationDefinition` resources under the owning path declared by `skills/project_subsystems/SKILL.md`. Do not assume another project's formation folder or filenames. Author slot offsets in the -Z-forward convention and keep slot 0 at the origin when it represents the leader. A formation steers only the members whose scenes carry a `FormationConsideration3D`; a formation assigned to a squad of other members yields slots nothing fills.

## Verification

Before changing this subsystem:

- locate the current declarations and XML summaries;
- locate every formation Blackboard key in the framework-side partial;
- verify the -Z-forward transform against implementation and tests;
- inventory the consuming project's authored formations through its subsystem registry;
- run the framework's focused formation suites (formation controller, consideration, assignment strategy, roster, coordinator, directive brain and snapshot builder), then any consumer integration suite that wires the roster.
