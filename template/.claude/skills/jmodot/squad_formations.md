# Squad Formation System (Jmodot)

Use this reference when wiring Jmodot's coordinated group movement: authored formation shapes, pure slot math, pluggable assignment, Blackboard state, and steering toward assigned slots.

**Code and XML summaries are authoritative.** Locate the current declarations before relying on this map.

## Framework surface

| Type | Role |
|---|---|
| `FormationDefinition` | `[GlobalClass]` Resource containing local-space slot offsets and minimum spacing |
| `FormationAnchorMode` | Leader, centroid, or static anchor selection |
| `ISlotAssignmentStrategy` | Maps member positions to slot positions |
| `FormationController` | Pure local-to-world slot-position math |
| `NearestSlotStrategy` | Greedy nearest-free-slot assignment; leader takes slot 0 |
| `SquadManager` | Owns membership and publishes formation state to Blackboard graphs |
| `FormationConsideration3D` | Steers each assigned member toward its slot |
| `DebugFormationComponent` | Optional slot and assignment display |

Locate these under Jmodot's AI/squad and navigation roots. Do not copy paths or line numbers from another consumer project.

## Flow

1. `FormationDefinition.SlotOffsets` defines the shape in local space. Slot 0 is the leader by convention; with a leader anchor it should be `Vector3.Zero`.
2. `FormationController.CalculateSlotPositions(...)` rotates offsets so local **-Z** aligns with the anchor's forward vector.
3. `ISlotAssignmentStrategy.AssignSlots(...)` maps member indexes to slot indexes. `-1` means unassigned.
4. `SquadManager` writes squad-wide values to its graph and each member's slot index to that member's graph. It attaches member graphs to the squad graph so upward reads work.
5. `FormationConsideration3D` reads the assigned slot and scores directions toward it. It returns zero while formation is inactive, the member is unassigned, the leader is excluded, or the member is inside the arrival radius.

## Coordinate convention

Local **-Z is forward**, +Z is behind, and +X is right. Verify this against `Basis.LookingAt` in the current controller and its tests. If a source comment disagrees with behavior and tests, fix the framework comment rather than teaching the stale claim here.

## Consumer contract

Nothing drives the manager automatically:

- Give `SquadManager` a `BlackboardGraph` before `_Ready`.
- Set an authored default formation and anchor mode only when the consumer wants automatic startup configuration.
- Call `UpdateFormationPositions(anchor, forward)` whenever the anchor moves. Membership changes may reassign slots but do not keep world positions current.
- Drive non-formation squad-state publication at the cadence the consumer needs.
- Add members through `SquadManager`; bypassing it means the consumer owns graph-parent wiring.

## Blackboard keys

The framework-side `BBDataSig` partial owns the formation keys. Locate that partial before editing; a consuming project may define another partial elsewhere.

| Key | Scope | Meaning |
|---|---|---|
| `FormationActive` | squad graph | Whether formation steering is active |
| `FormationSlotPositions` | squad graph | World position by slot index |
| `FormationLeader` | squad graph | Leader node |
| `FormationSlotIndex` | member graph | Assigned slot; `-1` means unassigned |

Cross-scope reads use the member graph's parent chain. A graph-less Blackboard can only read local values and is suitable only for isolated fixtures.

## Gotchas

- A member graph not attached to the squad graph cannot see squad-wide formation state.
- `AddMember(Node3D)` must resolve the member's `BlackboardGraph`; failure should stay visible.
- Slot 0 is the leader by convention, not by an authored `LeaderSlotIndex` field.
- Debug drawing may depend on a framework node group; verify the current implementation before relying on it.
- If `FormationDefinition` lacks `[Tool]`, a typed export from a `[Tool]` Resource can hit the editor-only cast cascade. Fix the framework type or use the base-`Resource` escape hatch from `rules/csharp_patterns.md`.

## Authoring data

Store `FormationDefinition` resources under the owning path declared by `skills/project_subsystems/SKILL.md`. Do not assume another project's formation folder or filenames. Author slot offsets in the -Z-forward convention and keep slot 0 at the origin when it represents the leader.

## Verification

Before changing this subsystem:

- locate the current declarations and XML summaries;
- locate every formation Blackboard key in the framework-side partial;
- verify the -Z-forward transform against implementation and tests;
- inventory the consuming project's authored formations through its subsystem registry;
- run the framework's focused formation suites, then any consumer integration suite that wires the manager.
