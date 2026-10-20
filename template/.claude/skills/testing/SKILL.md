---
name: Testing
description: >-
  Use to choose, write, run, debug or curate GdUnit4 tests, do Logic-domain TDD or manage test fixtures, runtime attributes, filters and orphans. SKIP checklist-based code review of test files; use checklists:test_quality.
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

On Windows, use `reference/running.md` §Scope-sized runs and §Hang-safe runs for the wrappers; `change_control` §Gate cadence owns when to run them. The bare commands above are the cloud path, where each one runs under `xvfb-run --auto-servernum` (GdUnit4 needs a display).

- **Full prefix `~Tests.<Suite>` is mandatory** — short `~Logic` matches only a subset, mimicking a silent skip. A zero-match run is not a RED, and a unit-only pass does not cover integration. An integration regression's seam test goes RED before GREEN (`_brainstorm_shared/drive_spine.md` §Slices owns the floor rule).
- **A filter narrower than a whole suite has no count sentinel.** Check count magnitude and duration; the TRX testName list is the arbiter (`change_control` §Green-output trust).
- **`--filter ~` is SUBSTRING, not regex** — escaped metacharacters (`Visual\.`) match literally and hit nothing, mimicking an empty suite rather than erroring. Disambiguate sibling prefixes with a trailing plain dot (`~Tests.Integration.Visual.` excludes `Visuals`).
- **ALWAYS `--verbosity quiet`** — the implicit rebuild otherwise floods Bash output with compiler warnings; counts and error messages survive quiet.
- **NEVER `--no-build`** — stale DLLs silently mask broken tests after branch switches/merges; `dotnet test` rebuilds automatically.
- **ALWAYS `--settings .runsettings`** — GODOT_BIN fallback, 30min safety timeout, `TreatNoTestsAsError`, `MaxCpuCount=1`.
- **Start every Godot/C# test or build call with `run_in_background: true`; a gate call uses `-Detach`.** Runs can queue on locks for minutes. Keep working, then use the until-loops in `guards/any.md` for a subagent or `claude -p` child; an orchestrator waits for the wake (`CLAUDE.core.md` §Shell Discipline). `hooks/pre_bash_dispatch.py` applies the background flag to a delegate's foreground run call; plan for a task id, not inline output.
- **NEVER pipe test output through `| tail` / `| head`** — they buffer the whole stream and hang on long runs. Use `2>&1` alone.
- **Add `[RequireGodotRuntime]` to a test that builds or calls any Godot type:** nodes, scenes, `GD.Load`, and value types such as `Vector2`/`Vector3`/`StringName`. Analyzer GdUnit0501 rejects the test otherwise, even for pure math. Only plain C# types need no attribute.
- **Runtime slots: on Windows, run suites through `run_test_suite.ps1` or `verify.ps1`, never bare `dotnet test`.** A bare run takes neither the checkout lock nor a runtime slot, so it collides with peer runs. Logic runs never take `-NoGodotRuntime` — a runtime test silent-skips without the runtime, so the suite reports green with its subjects unrun. Lock and slot mechanics, triggers and rollback: `reference/running.md` §Runtime slots and parallel dev.

## Test Domains

Identify the domain before writing tests. The **Logic vs Gameplay split** lives in CLAUDE.md *Development Philosophy: Hybrid TDD*; this skill owns the workflow mechanics.

| Domain | Location | Rule | When |
|--------|----------|------|------|
| **Logic** | `Tests/Logic/` | Strict TDD (RED→GREEN→REFACTOR) — `reference/logic_tdd.md` | the Logic subsystems CLAUDE.md *Development Philosophy: Hybrid TDD* names |
| **Gameplay** | `Tests/Integration/`, `Tests/Sanity/` | Automate deterministic, inspect feel — `reference/choosing.md` | the Gameplay subsystems CLAUDE.md *Development Philosophy: Hybrid TDD* names |

**Gate coverage is namespace-coupled.** Namespaces mirror folder paths (`{{PROJECT_NAME}}.Tests.<Suite>.<Domain>`), and `/regression_gate` runs ONLY `~Tests.Logic` / `~Tests.Integration` / `~Tests.Sanity`. Adding a top-level `Tests/<X>/` tree is `/regression_gate` §Baseline's.

**Before deferring a playtest or fingerprint list to the user, screen each item.** Such lists mix subjective items with automatable ones; `reference/choosing.md` owns the screen.

## Test Hygiene

- Never leave tests broken, even if unrelated to current work.
- Failing tests obscure whether new changes introduced regressions.
- Debug timeouts immediately — in Logic domain a timeout usually means an infinite loop, not slow execution.

## Verification rules

- A new integration test that drives engine lifecycle (frames, spawns, scene-tree entry and exit, awaited signals), or a change that can affect its lifecycle timing, ordering or isolation, passes twice on the same tree digest before its pass counts. A mechanical edit that changes none of these needs no second pass. Two agreeing runs are the smallest sample that can show nondeterminism; a disagreeing pair is the defect (the `debugging` skill's reproduce phase).
- An owner-reported number (an authored value, an observed value or a verdict: a distance, a timing, a curve point) becomes a regression test of the behaviour that value produces, never an assertion of the literal authored value (`rules/test_authoring.md` #12); an observed value or verdict may be asserted. The test's `<summary>` names where the owner said it (worklog item, checklist or task record). An agent never invents the number; without one, ask, or the behaviour stays owner-checked.

## Pre-Commit Regression Gate

Run `/regression_gate` before committing code changes. It owns applicability, suite coverage, the verdict and the exemptions.

## See Also

| Reference | Contents |
|-----------|----------|
| [scene_runner.md](scene_runner.md) | ISceneRunner API: accessors, input simulation, frame control (vendored GdUnit4 docs) |
| [advanced.md](advanced.md) | Lifecycle hooks, parameterized tests, utilities, FAQ (vendored GdUnit4 docs) |
