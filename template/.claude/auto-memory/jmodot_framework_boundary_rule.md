---
name: Jmodot_Framework_Boundary_Rule
description: Code-level boundary rule preventing Jmodot from reaching into consuming-project namespaces. Introduces the static-seam pattern used for defaults (CombatFactoryDefaults) as the framework-safe equivalent of the PP-internal Default Value Pattern. Authored 2026-04-19.
type: feedback
originSessionId: 65fe3ebf-5342-45f2-b44f-e56a7c3d003f
---
## Rule
Jmodot code MUST NOT reference the consuming project's namespace (e.g. `{{PROJECT_NAME}}.Global.GlobalRegistry`, `{{PROJECT_NAME}}.Combat.*`). Framework code is game-agnostic by contract.

**Why:** Reusability. Any file that does `using {{PROJECT_NAME}}.Global;` or `{{PROJECT_NAME}}.Global.GlobalRegistry.DB.X` breaks Jmodot's portability — it can no longer be used by TimeRobbers or any other game consumer without rewriting those files.

**How to apply:** For project-wide defaults previously obtained via `?? GlobalRegistry.DB.X` fallback pattern:

1. Introduce a framework-agnostic static seam class in `Jmodot.Core.*` (nullable static fields or a `Configure()` method).
2. Have the consuming project's autoload populate the seam at `_EnterTree`.
3. The seam owns its own `Reset()` method — Jmodot-only tests reset via the seam directly, not via the consuming project's autoload reset path.

## Canonical example (landed 2026-04-19)
- **Seam class:** `Jmodot/Core/Combat/CombatFactoryDefaults.cs` — static nullable fields for crit attrs + 4 status runner PackedScenes.
- **Consumer wiring:** `{{PROJECT_NAME}}/Global/GlobalRegistry.cs::WireCombatFactoryDefaults()` called from `_EnterTree` and `EnsureInitializedForTests`.
- **Test reset:** `CombatFactoryDefaults.Reset()` called from tests' `[BeforeTest]`/`[AfterTest]` AND from PP's `GlobalRegistry.ResetForTests()`.
- **Fail-loud contract:** When both the per-factory override AND the seam default are null, `Create()` throws `InvalidOperationException` via `JmoLogger.Error + throw` (mirror of existing `TickEffectFactory.PerTickEffect` guard).

## What it replaced
Prior contamination: 7 Jmodot files had either `using {{PROJECT_NAME}}.Global;` or fully-qualified `{{PROJECT_NAME}}.Global.GlobalRegistry.DB.*` references (6 factories + 1 unused import in `ModifiedFloatDefinition.cs`). The `?? GlobalRegistry.DB.X` pattern was canonical inside PP but contamination inside Jmodot.

## Interaction with the PP-internal Default Value Pattern
Architecture Philosophy's "Default Value Pattern" (`ConfigOverride ?? GlobalRegistry.DB.DefaultAttribute`) remains the canonical pattern for **PP-internal code** (SpellArchitecture, Casters, etc.). The inversion applies ONLY at the Jmodot boundary — inside Jmodot code, the seam class replaces the registry reference.

## No carve-out for "temporary" violations
Plans that accept "temporary R11 violation as follow-up" must still be rejected at
implementation time. Jmodot/* must NEVER import {{PROJECT_NAME}}.* even transiently — the
boundary rule has no temporary-violation carve-out, even when the plan author explicitly
flags the trade.

**Workaround:** place the new code on the PP side mirroring an existing PP sibling, then
worklog the future Jmodot relocation as one combined item alongside the marker / dep
blocking the move.

**Concrete (2026-05-07 Wind Blast Session 4):** plan §10.A accepted importing
`{{PROJECT_NAME}}.AI.HSM.Shared.IControlLossState` into a new Jmodot `LaunchedState` as
"temporary directional-coupling smell." Implementation overrode the plan: `LaunchedState`
placed in PP next to `CapturedState`; `LaunchedMovementStrategy3D` (no marker dep) went
to Jmodot. The relocation became one worklog item (move `IControlLossState` + all 6
control-loss states together).

## Related
- `Jmodot_Modularity_Philosophy` — general game-agnostic rule this code-level rule enforces.
- `Jmodot_CombatFactoryDefaults_Seam` — per-file reference for the canonical example.
