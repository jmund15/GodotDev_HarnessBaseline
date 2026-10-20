---
disable-model-invocation: true
---

# Godot Review Agent Items

Engine items for [`review_agents.md`](review_agents.md), which points here when the godot layer is installed. *Batch Sizing* and *Submodule Review* extend the sections of that name there. Each `## <agent>` section extends that agent's checklist and goes into its prompt with the checklists the caller injects.

## Batch Sizing

Godot data files are `.tres` and `.tscn`. An associated pair is a data file plus the `.cs` file that defines or configures it (e.g., `Modifier.cs` + `boosted_modifier.tres`); an orphaned `.tres`/`.tscn` is one with no `.cs` counterpart in any batch.

## Submodule Review

The submodule is `Jmodot`, which defines the APIs {{PROJECT_NAME}} calls. Its branch is `jmodot/<worktree-name>`, derived from the {{PROJECT_NAME}} branch as `rules/jmodot_submodule.md` describes. Reviewing {{PROJECT_NAME}} code without the Jmodot diff misses API contract changes, signature mismatches and behavioral changes.

## test-analyzer

- Deterministic gameplay behavior has an `ISceneRunner` test (`Tests/Integration/` or `Tests/Sanity/`)
- `await runner.AwaitInputProcessed()` called after every `SimulateActionPressed`/`SimulateKeyPress`
- `[RequireGodotRuntime]` used only when actually needed (GD.Load, Nodes, scenes, Vector3)
- Orphan prevention: `using` with ISceneRunner, or explicit cleanup in `[After]`

## error-hunter

- `JmoLogger.Error` missing where exceptions are caught
- `GetNode<T>()` results used without null check (prefer `TryGetNode`)
- Physics callback safety:
  - Direct property assignment (Monitoring, Monitorable) inside physics callbacks or methods called from them (should use SetDeferred)
  - Signal connection/disconnection in physics callbacks without guards

## type-reviewer

- `[RequiredExport]` usage: Every `[Export] = null!` paired with `[RequiredExport]`?
- Resource patterns: Factory->Runner pattern followed? Shared Resources not caching per-instance state?
- Immutability: Data objects implementing `IRuntimeCopyable<T>` where deep copy is needed?
- enum vs Resource: Could an enum be replaced with a Resource subclass for extensibility (or vice versa)?
- For Resource subclasses: verify no mutable instance state leaks between consumers sharing the same `.tres`
- For files with `[Export]` properties: check each Intuitiveness item, including the `## Godot Intuitiveness (I)` items of `code_quality_godot.md`
