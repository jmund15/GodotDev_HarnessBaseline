# Phase 0 — Symptom triage router: Godot / C# rows

Companion of [`symptom_router.md`](symptom_router.md). Read it with that file at Phase 0 when the godot layer is installed. The Route column owns the depth; don't re-derive it.

| Symptom | Likely cause | Discriminating check | Route |
|---|---|---|---|
| Test log dominated by one repeated engine error | Test-run exhaust, not application behavior: the exit-time ObjectDB leak dump emits one `Cannot get path of node` ERROR per orphaned Node | Grep the log head for `GdUnit4TestRunnerScene` (= test provenance); count `Leaked instance:` lines | `/analyze_godot_logs` |
| Tests "Passed" but suspiciously fast / low count | Silent skip: GodotRuntimeExecutor never connected, so every `[RequireGodotRuntime]` test reports Passed without running | Compare the Passed count against the regression gate's silent-skip sentinel counts; grep for `GodotRuntimeExecutor failed` / `Connection timeout` | `testing` + `/regression_gate` |
| `InvalidCastException` in editor only, all tests green | `[Tool]` cascade: a typed `[Export]` references a Resource or subclass lacking `[Tool]`; no runtime test catches it | `/regression_gate` step 1c (`tool_cascade_audit.py`); check the referenced class for `[GlobalClass, Tool]` | `rules/csharp_patterns.md` + `architecture_philosophy` |
| Component does nothing, no errors | `IComponent.Initialize(bb)` never called: components silently no-op while `IsInitialized == false` | Check `IsInitialized`; read the entity `.tscn` wiring and the bootstrapper's pre-registration order | `jmodot` + `rules/jmodot_utilities.md` |
| `#if DEBUG` code absent at test time | Godot's Debug configuration defines `TOOLS`, not `DEBUG`: the guard strips silently under `dotnet test` | Grep the file for `#if DEBUG` | `rules/csharp_patterns.md` (Test Helper Setters) |
| `.tres` change has no effect | Out-of-tree placement (registration is by directory scan), or an editor bulk-resave nulled a value-type `[Export]` | Confirm the file sits under a scanned root; `git diff` the `.tres` for `= null` on value types (`tres_nullstrip_guard.py` catches staged cases) | `rules/godot_files.md` |
| Build/DLL-lock errors out of nowhere | Concurrent Claude session: every worktree builds one project DLL and shares one GdUnit4 named pipe | `git worktree list`; `tasklist \| findstr Godot`: another session's runtime may be live | `environment_bootstrap` |

## Cross-references

`/regression_gate` (Phase 5 broader verification: silent-skip sentinel, baseline drift, explicit failures) · `/analyze_godot_logs` (post-run `godot.log`, Phases 1–2).
