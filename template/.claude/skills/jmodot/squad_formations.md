# Squad Formation System (Jmodot)

Coordinated group movement for AI agents: a data-driven shape (`FormationDefinition` Resource), pure-function slot math (`FormationController`), pluggable member→slot assignment (`ISlotAssignmentStrategy`), a `SquadManager` node that orchestrates state onto Blackboards, and a steering consideration (`FormationConsideration3D`) that pulls each member toward its assigned slot.

**Status (as of 2026-07-06):** framework-complete and test-pinned inside Jmodot; **no {{PROJECT_NAME}} gameplay code or scene consumes it yet** — consumers are Jmodot internals + tests only.

**Not the same thing as the project's `NPCs/Squads/Squad`:** that is a lightweight per-encounter group CONTEXT (holds the squad-scoped `ProvocationLedger`; injected onto member blackboards under `BBNPCSig.Squad` by `CombatSpawnHelper`). This Jmodot stack is group MOVEMENT (formations/slots). When formations get adopted, `SquadManager` attaches at the same spawner seam and can be carried by/alongside the project `Squad` context — see the faction-vs-squad seam doc (`Claude/Architecture/faction-squad-seam.md` in the project vault).

**The maintained API surface is the XML `<summary>` docs in the source files.** This doc is a verified map (concepts, wiring, gotchas), not a signature mirror — when they disagree, code wins; re-verify per Provenance below.

## Source files (all paths verified 2026-07-04)

| File | Role |
|---|---|
| `Jmodot/Core/AI/Squad/FormationDefinition.cs` | `[GlobalClass]` Resource. Exports: `Vector3[] SlotOffsets`, `float MinSpacing` (1.5), `string FormationName`; computed `int SlotCount` |
| `Jmodot/Core/AI/Squad/FormationAnchorMode.cs` | enum `Leader` / `Centroid` / `Static` |
| `Jmodot/Core/AI/Squad/ISlotAssignmentStrategy.cs` | non-generic strategy contract (below) |
| `Jmodot/Implementation/AI/Squad/FormationController.cs` | static, pure: `CalculateSlotPositions(...)` |
| `Jmodot/Implementation/AI/Squad/NearestSlotStrategy.cs` | greedy nearest-first assignment, O(n²), leader→slot 0 first |
| `Jmodot/Implementation/AI/Squad/SquadManager.cs` | orchestrator Node — the only writer of formation BB state (namespace `Jmodot.Implementation.AI.Squad` since 2026-07-06; formerly under `UtilityAI`) |
| `Jmodot/Implementation/AI/Navigation/Considerations/FormationConsideration3D.cs` | steering consideration (extends `BaseAIConsideration3D`) |
| `Jmodot/Implementation/AI/Squad/DebugFormationComponent.cs` | slot/assignment visualization via the `DebugDraw3D` addon (`addons/debug_draw_3d/`); child of SquadManager |
| `Jmodot/Implementation/AI/BB/BBDataSig.cs:128-153` | the four formation BB keys (Jmodot-side partial) |
| `Global/Formations/*.tres` | project-side formation data (see Data files) |

Anti-hallucination note: there is NO `Jmodot/Implementation/AI/Formations/` directory, no `FormationController.AssignSlots`, no `LeaderSlotIndex`/`Metadata` properties on `FormationDefinition`, and no `FormationLeaderTarget` BB key — all were fictions in the pre-2026-07-04 version of this doc.

## How it works

1. **Shape** — `FormationDefinition.SlotOffsets` are local-space offsets. Slot 0 is the leader *by convention* (no property marks it). With `Leader` anchor mode, slot 0's offset should be `Vector3.Zero`.
2. **Slot world positions** — `FormationController.CalculateSlotPositions(formation, anchorMode, anchorPosition, anchorForward, memberPositions = null)` → `Dictionary<int, Vector3>`. Anchor = `anchorPosition` (Leader/Static) or the member centroid (Centroid; falls back to `anchorPosition` when `memberPositions` is null/empty). Offsets rotate so local **-Z** aligns with `anchorForward` (`Basis.LookingAt`).
3. **Assignment** — `ISlotAssignmentStrategy.AssignSlots(IReadOnlyList<Vector3> memberPositions, IReadOnlyDictionary<int, Vector3> slotPositions, int leaderMemberIndex = -1)` → `Dictionary<int, int>` mapping member index → slot index, `-1` = unassigned. `NearestSlotStrategy` pins `leaderMemberIndex` to slot 0, then each remaining member greedily takes its nearest free slot.
4. **State onto Blackboards** — `SquadManager` writes squad-scope keys to its `BlackboardGraph` child (`_squadGraph.Local`) and each member's `FormationSlotIndex` to that member's own graph. `AddMember` attaches the member graph to the squad graph (`AttachParent`) so members can read squad state up the hierarchy.
5. **Steering** — `FormationConsideration3D` (in each member's steering set) scores directions toward the assigned slot: zero when formation inactive / slot unassigned / leader excluded / inside `_arrivalRadius`; otherwise `_formationWeight × (distance / _maxInfluenceDistance) × alignment` (positive dot only), Y-flattened for ground movement.

## Coordinate convention — resolved

**Local formation space: -Z is forward (Godot convention); +Z = behind, +X = right.** Pinned by the implementation (`FormationController.cs:42-44` comment + `Basis.LookingAt` in `CalculateRotationBasis`, `:94-106`) and by tests (`Tests/Logic/AI/FormationControllerTest.cs:24-31` "+Z = behind"; `:54-78` local +Z rotates to world -X when facing +X). Shipped data agrees (`Global/Formations/v_formation.tres`: slot-0 leader at origin, flanks at +Z behind).

⚠️ `FormationDefinition.cs:9`'s XML summary claims "+Z is forward" — **that comment is wrong** (contradicts implementation + tests). Fix belongs in a Jmodot PR; until then, trust the tests.

## SquadManager — orchestration contract

Public API (verified): `Members`, `AddMember(Node3D, bool isLeader = false)`, `AddMember(Node3D, IBlackboardGraph, bool isLeader = false)`, `RemoveMember(Node3D)`, `SetFormation(FormationDefinition, FormationAnchorMode)`, `ClearFormation()`, `UpdateFormationPositions(Vector3 anchorPosition, Vector3 anchorForward)`, `UpdateSquadBlackboard()`.

Consumer responsibilities — nothing is automatic:

- Give SquadManager a `BlackboardGraph` child before `_Ready` (tests: `SetSquadGraph` helper).
- Optionally set the `_defaultFormation` + `_anchorMode` exports — `_Ready` applies them via `SetFormation`.
- Call `UpdateFormationPositions(anchor, forward)` whenever the squad anchor moves — **there is no per-frame driver**; slot world positions go stale otherwise. (Internal `ReassignSlots` — on membership/formation change — uses an origin-relative Leader-anchored layout purely for assignment; real world positions come only from `UpdateFormationPositions`.)
- Call `UpdateSquadBlackboard()` from a Timer for the non-formation squad-state keys (`SquadAverageHealth`, `HasSquadTag`, `ActiveSquadTag` — panic-vs-attack tag switch at `_panicHealthThreshold`, default 0.25).

## Blackboard keys (`Jmodot/Implementation/AI/BB/BBDataSig.cs:128-153`)

| Key | Type | Scope | Written by |
|---|---|---|---|
| `FormationActive` | `bool` | squad graph | `SetFormation` (true) / `ClearFormation` (false) |
| `FormationSlotPositions` | `Dictionary<int, Vector3>` | squad graph | `UpdateFormationPositions` |
| `FormationLeader` | `Node3D` | squad graph | `AddMember(isLeader: true)` |
| `FormationSlotIndex` | `int` (-1 = unassigned) | member graph | `ReassignSlots` (via strategy) |

`BBDataSig` is a two-partial class: these keys live in the **Jmodot-side** partial, not the project's `AI/BB/BBDataSig.cs` (same namespace). Grepping the project file for them returns nothing.

## FormationConsideration3D exports

| Export | Default | Range | Meaning |
|---|---|---|---|
| `_formationWeight` | 1.0 | 0.1–5.0 | score multiplier |
| `_excludeLeader` | true | — | slot 0 gets zero scores (leader drives, doesn't follow) |
| `_arrivalRadius` | 1.5 | 0.5–5.0 | inside → no steering |
| `_maxInfluenceDistance` | 20.0 | 5.0–50.0 | distance-factor clamp |

Cross-scope reads (`FormationActive`, `FormationSlotPositions`) go through `blackboard.FindParentGraph()` → `TryGetUp` (squad scope); `FormationSlotIndex` is agent-local. Graph-less blackboards fall back to local-only reads — a deliberate carve-out for test fixtures, not a production path. Listed as compatible with `DistanceScalingModifier3D` in that modifier's XML docs.

## Gotchas

- **Member graph must be attached to the squad graph** or the consideration never sees `FormationActive`. `SquadManager.AddMember` does the `AttachParent`; bypass SquadManager and you own that wiring.
- **`AddMember(Node3D)` requires the member to resolve a `BlackboardGraph`** (`GetGraph()`); otherwise it logs a warning and no-ops.
- **`FormationDefinition` is `[GlobalClass]` but NOT `[Tool]`** (as of 2026-07-04). Exporting it as a typed field on a `[Tool]` script triggers the editor-only `InvalidCastException` cascade (`rules/csharp_patterns.md` §`[Tool]`); fix = add `[Tool]` in a Jmodot PR, or type the export as base `Resource` and cast.
- **DebugFormationComponent** finds members via the `"SquadMembers"` node group — members outside that group draw no connection lines. Slot 0 renders in the leader color.
- **Leader is slot 0 by convention only.** The convention is enforced in exactly two places: `leaderMemberIndex` in the strategy and `_excludeLeader` in the consideration.

## Data files

`Global/Formations/` holds the project's formation Resources — `circle_formation.tres`, `line_formation.tres`, `v_formation.tres` (as of 2026-07-04; inventories rot — glob the directory). Authoring a new one: a `FormationDefinition` `.tres` with `SlotOffsets` in the -Z-forward convention, slot 0 = leader (typically `Vector3.Zero`).

## Tests

`Tests/Logic/AI/`: `FormationControllerTest`, `FormationConsiderationTest`, `NearestSlotStrategyTest`, `SquadManagerTest`, `SquadManagerGraphMigrationTest`. `Tests/Integration/AI/`: `SquadFormationIntegrationTest`, `SquadGraphSceneIntegrationTest`. Suite names only — per-suite test counts rot and add nothing.

## Provenance & maintenance

Re-verify from repo root (quote paths — the repo path contains spaces):

- Class locations: `rg -n "class (FormationDefinition|FormationController|NearestSlotStrategy|SquadManager|FormationConsideration3D|DebugFormationComponent)" -g "*.cs"`
- BB keys: `rg -n "Formation" "Jmodot/Implementation/AI/BB/BBDataSig.cs"`
- Forward-axis pin: `rg -n "matches Godot" "Jmodot/Implementation/AI/Squad/FormationController.cs" "Tests/Logic/AI/FormationControllerTest.cs"`
- Stale `+Z` comment fixed yet? `rg -n "Z is forward" "Jmodot/Core/AI/Squad/FormationDefinition.cs"` (a `+Z is forward` hit = still stale)
- `[Tool]` gap: `rg -n "GlobalClass" "Jmodot/Core/AI/Squad/FormationDefinition.cs"` (no `Tool` in the attribute list = gap still open)
- `.tres` inventory: `Get-ChildItem "Global/Formations"`
- "No project gameplay consumer" status line: `rg -l "SquadManager|FormationConsideration3D" -g "*.cs" -g "*.tscn" -g "!Jmodot/**" -g "!Tests/**" -g "!.claude/**" -g "!harness-baseline/**"` (any hit = status line stale)
