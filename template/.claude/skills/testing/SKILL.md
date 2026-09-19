---
name: Testing
description: >-
  Auto-load when writing, running, or debugging tests, or doing TDD — anything touching
  GdUnit4 suites, the shared fixtures (AbilityTestFixture / ActivationTestFixture / ISceneRunner),
  runtime-test attributes, run commands/filters, or orphan management. SKIP for code
  reviews of test files (use `checklists:test_quality`).
---

# Testing Skill

GdUnit4Net for {{PROJECT_NAME}}. Version SSOT: `{{PROJECT_NAME}}.csproj` PackageReference lines — never trust version strings in prose.

## Recipe index — read the reference file for the step you are on

| At this step | Read |
|---|---|
| Running a suite: `verify.ps1`, the hang-safe wrapper, `-TimeoutMs` sizing, batched Integration runs, filter-coverage checks and multi-suite `|` batching, exit codes, the silent-skip sentinel | `reference/running.md` |
| Deciding what deserves a test, or curating/reviewing an existing suite: subject selection, coverage levels, POB, anti-patterns, retention | `reference/choosing.md` |
| Writing or changing Logic-Domain production code: the Iron Law and the rationalizations | `reference/logic_tdd.md` |
| Writing the test body: attributes, assertions, fixtures, E2E waits, timing gotchas, mocking boundaries, teardown | `reference/authoring.md` |

## Quick Start

```bash
# ALWAYS run by category (Windows pipe crashes on full suite)
dotnet test --settings .runsettings --verbosity quiet --filter "FullyQualifiedName~Tests.Logic"
dotnet test --settings .runsettings --verbosity quiet --filter "FullyQualifiedName~Tests.Integration"
dotnet test --settings .runsettings --verbosity quiet --filter "FullyQualifiedName~Tests.Sanity"
```

Prefer `reference/running.md` §Scope-sized runs (`verify.ps1`) and §Hang-safe runs (`run_test_suite.ps1`) on Windows; the bare commands above are the cloud path.

- **Full prefix `~Tests.<Suite>` is mandatory** — short `~Logic` matches only a subset, mimicking a silent skip. Canonical form: `.claude/commands/regression_gate.md`.
- **`--filter ~` is SUBSTRING, not regex** — escaped metacharacters (`Visual\.`) match literally and hit nothing, mimicking an empty suite rather than erroring. Disambiguate sibling prefixes with a trailing plain dot (`~Tests.Integration.Visual.` excludes `Visuals`).
- **ALWAYS `--verbosity quiet`** — the implicit rebuild otherwise floods Bash output with compiler warnings; counts and error messages survive quiet.
- **NEVER `--no-build`** — stale DLLs silently mask broken tests after branch switches/merges; `dotnet test` rebuilds automatically.
- **ALWAYS `--settings .runsettings`** — GODOT_BIN fallback, 30min safety timeout, `TreatNoTestsAsError`, `MaxCpuCount=1`.
- **Bash timeout: 600000** — the 120s default kills the command but not the Godot subprocess, orphaning it against the named pipe.
- **NEVER pipe test output through `| tail` / `| head`** — they buffer the whole stream and hang on long runs. Use `2>&1` alone.
- **Add `[RequireGodotRuntime]` only for tests using Godot features** (GD.Load, Nodes, scenes).
- **Single-flight: one Godot test process per machine.** Concurrent instances crash CLR `0xc000001d` (`--headless` is no escape — the pipe server needs a display), so every suite serializes on the machine-global run-lock and Logic runs never take `-NoGodotRuntime` (~95% of Logic is `[RequireGodotRuntime]`). Per-worktree mechanisms: `reference/running.md` §Single-flight and parallel dev.

## Test Domains

Identify the domain before writing tests. The **Logic vs Gameplay split** lives in CLAUDE.md *Development Philosophy: Hybrid TDD*; this skill owns the workflow mechanics.

| Domain | Location | Rule | When |
|--------|----------|------|------|
| **Logic** | `Tests/Logic/` | Strict TDD (RED→GREEN→REFACTOR) — `reference/logic_tdd.md` | the Logic subsystems CLAUDE.md's Domain Split names |
| **Gameplay** | `Tests/Integration/`, `Tests/Sanity/` | Automate deterministic, inspect feel — `reference/choosing.md` | the Gameplay subsystems CLAUDE.md's Domain Split names |

**Gate coverage is namespace-coupled.** Namespaces mirror folder paths (`{{PROJECT_NAME}}.Tests.<Suite>.<Domain>`), and `/regression_gate` runs ONLY `~Tests.Logic` / `~Tests.Integration` / `~Tests.Sanity`. A new top-level `Tests/<X>/` tree is **silently un-gated** until both the gate filters and `Tests/regression_baseline.json` are extended. Live deliberate example: `Tests/ProcGenSim/` (manual-only, via `/procgen_sim`).

**Before deferring a playtest or fingerprint list to the user, screen each item.** Such lists mix subjective items with automatable ones; `reference/choosing.md` owns the screen.

## Test Hygiene

- Never leave tests broken, even if unrelated to current work.
- Failing tests obscure whether new changes introduced regressions.
- Debug timeouts immediately — in Logic domain a timeout usually means an infinite loop, not slow execution.

## Pre-Commit Regression Gate

Run `/regression_gate` before committing code changes; the command owns the procedure.
- Run AFTER the final staged state, never from a cached previous run.
- ALL 3 suites (Logic, Integration, Sanity) must pass — one domain is insufficient.
- Windows pipe crashes can silently drop tests — always `--filter` batches, never bare `dotnet test`.
- Exempt: pure meta commits (`.claude/`, skills, docs) touching no code — harness commits instead need a green `harness_tests.py` stamp (CLAUDE.md §Build & Test Commands).

## See Also

| Reference | Contents |
|-----------|----------|
| [scene_runner.md](scene_runner.md) | ISceneRunner API: accessors, input simulation, frame control (vendored GdUnit4 docs) |
| [advanced.md](advanced.md) | Lifecycle hooks, parameterized tests, utilities, FAQ (vendored GdUnit4 docs) |
