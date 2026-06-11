---
allowed-tools: Bash(dotnet build:*), Bash(dotnet test:*)
---

## Purpose

Single source of truth for regression verification. Called by `/session_end`, `/commit_push`, and `/review_pr` before any commit or merge. Can also be invoked standalone.

## When Required

- **Code commits:** Any commit touching `.cs` files (Logic, Gameplay, Tests, Jmodot)
- **Data commits with code coupling:** `.tres`/`.tscn` changes that affect Logic Domain behavior

## When Exempt

- Pure meta commits (`.claude/`, `skills/`, `CLAUDE.md`, docs)
- Pure configuration/asset commits with no code coupling

## Prerequisite

**Load the [Testing Skill](/.claude/skills/testing/SKILL.md) before proceeding.** It contains GdUnit4 runtime troubleshooting, silent skip detection details, and test count baselines that are critical for validating results.

## Baseline File

The gate reads and writes `Tests/regression_baseline.json` — a committed source-of-truth for current test counts, silent-skip sentinels, and drift thresholds. This file is part of the repository (not gitignored) and must be kept in sync with whatever branch is being tested. **Treat it like any other data file:** if your changes cause tests to be added or removed, the gate will auto-update this file and you commit the diff alongside your test changes.

Key fields:
- `suites.<Logic|Integration|Sanity>.passed` — the committed baseline count
- `silent_skip_sentinels.<Suite>_min` — architectural floor that indicates GodotRuntimeExecutor failure regardless of baseline drift
- `drift_thresholds.warn_ratio` (default 0.90) and `hard_fail_ratio` (default 0.70) — how far below baseline before we warn/fail

**Filter coverage:** the gate runs only `~Tests.Logic`, `~Tests.Integration`, `~Tests.Sanity`. Test folders under any other top-level name (e.g. `Tests/Gameplay/`) are **silently un-gated** — they compile and run when invoked manually but never block commits. New top-level `Tests/<X>/` folders require either (a) renaming to one of the three covered prefixes, OR (b) adding a fourth filter call here AND a `suites.X` entry in the baseline JSON. See `arch_rule_test_namespace_matches_gate_filter.md`.

## Procedure

1. **Pre-flight: Kill orphaned Godot processes (editor-safe):**
```bash
# List all Godot processes (check what's running)
powershell.exe -Command "Get-Process -Name 'Godot*' | Select-Object Id, MainWindowTitle | Format-Table -AutoSize"
# Kill only non-editor processes (empty MainWindowTitle = headless test runner / orphan)
powershell.exe -Command "Get-Process -Name 'Godot*' | Where-Object { \$_.MainWindowTitle -notlike '*Godot Engine*' } | Stop-Process -Force -ErrorAction SilentlyContinue"
```
**Why this is safe:** The Godot editor always has `MainWindowTitle` containing "Godot Engine". GdUnit4 test runners are headless (empty title). This kills orphaned test processes without closing the user's editor.

**Note:** the step-3 runner (`run_test_suite.ps1`) performs this clearing — plus a stronger `taskkill /F /T` tree-kill of any surviving `vstest.console`/`testhost`/`test --settings` wrapper — automatically before each suite. This manual pre-flight is for visibility and standalone diagnosis.

**When name-based kill is insufficient (wedged "Starting test execution" that survives a kill):** a detached `dotnet test` wrapper respawns its testhost+Godot grandchildren faster than `Stop-Process` reaps them, holding the named pipe (and sometimes a lock on `.godot/mono/temp/bin/Debug/GodotSharp.dll`). Find and tree-kill the WRAPPER by parent chain, repeat 3–4×:
```bash
powershell.exe -Command "Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -match 'vstest.console|testhost|test --settings' } | ForEach-Object { taskkill /F /T /PID \$_.ProcessId }"
```
If a clean process table STILL wedges runtime suites, machine named-pipe state is exhausted — **reboot is the terminal fix**.

**On Linux/cloud** (no editor to protect — headless only):
```bash
pkill -f "Godot_v" 2>/dev/null || true
```

Orphaned Godot processes block the GdUnit4 runtime executor, causing ALL `[RequireGodotRuntime]` tests to be **silently skipped** (reported as "Passed" but never ran).

1b. **Data-integrity pre-check: `.tres` value-type null-strip guard:**
```bash
python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/tres_nullstrip_guard.py"
```
Scans staged `.tres` additions for `Field = null` on numeric-intent Exports — the editor-resave null-strip shape that loads as `0` instead of the C# default, with a green build and no warning (incident `9b16d361` silently zeroed crit/knockback/range knobs across ~10 archetypes; only `*Required` was ever restored). Exit 1 = suspected strip: treat like a build failure — set the explicit intended value (e.g. `KnockbackVelocityScaling = 1.0`), or confirm the field is a genuine nullable override before proceeding. Diff-scoped, so already-committed nulls don't trip it. Reference: `gotcha_editor_reserialize_value_export_null_strip.md`.

1c. **`[Tool]` cascade static gate:**
```bash
python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/tool_cascade_audit.py"
```
Project-wide typed-`[Export]` graph analyzer. **Exit 1 = a {{PROJECT_NAME}} `[GlobalClass]` Resource is missing `[Tool]`** (a cascade gap): treat like a build failure — add `[Tool]` (`[GlobalClass]` → `[GlobalClass, Tool]`) to each flagged class, or run `python3 .claude/hooks/apply_blanket_tool.py` to fix all at once. Godot-free and deterministic. This is the **editor-only** failure mode — `InvalidCastException` in the auto-generated setter — that **no GdUnit4 / runtime test can catch** (at runtime every script is its real type). Side effect: regenerates `.claude/hooks/tool_resource_classes.txt` (the edit-time hook's allowlist — stage the diff if it changed, same as `regression_baseline.json`) and `logs/tool_audit_inventory.md`. Policy + mechanism: `Tool Attribute Audit` charter; Memory `Tool_Attribute_Cascade_Rules`.

2. **Build:**
```bash
dotnet build -consoleLoggerParameters:ErrorsOnly
```
If build fails, stop and fix. The `-consoleLoggerParameters:ErrorsOnly` flag suppresses ~337 compiler warnings (~50KB of noise) that otherwise drown errors.

3. **Run all three suites in sequence via the hang-proof runner** (Windows local):
```bash
pwsh -NoProfile -File .claude/scripts/run_test_suite.ps1 -Filter "FullyQualifiedName~Tests.Logic"       -Label Logic
pwsh -NoProfile -File .claude/scripts/run_test_suite.ps1 -Filter "FullyQualifiedName~Tests.Integration" -Label Integration
pwsh -NoProfile -File .claude/scripts/run_test_suite.ps1 -Filter "FullyQualifiedName~Tests.Sanity"      -Label Sanity
```
Each Bash tool call: `timeout=600000`. The wrapper self-limits to an 8-min wall-clock cap (under the Bash 600000ms ceiling) so the Bash tool ALWAYS regains control — even on a wedge.

**Why the wrapper (not bare `dotnet test`):** a wedged `dotnet test` hangs the Bash caller *past its own timeout forever* — testhost→Godot grandchildren inherit the tool's stdout pipe and hold its write-end open, so the tool blocks on a read that never EOFs. The wrapper (a) redirects `dotnet` output to a file so no grandchild inherits the caller's pipe, (b) hard-caps the wall-clock and `taskkill /F /T`-tree-kills on expiry (name-based `Stop-Process` misses the respawning wrapper), and (c) tree-kills stale orphans BEFORE each launch. It still enforces `--settings .runsettings`, `--verbosity quiet`, and no `--no-build` internally. Full rationale: `.claude/scripts/run_test_suite.ps1` header + memory `GdUnit4_Process_Management`.

**Reading the wrapper output (one line per suite):**
- `STATUS=DONE  exit=0` + a `Passed! - Failed: 0, Passed: N ...` line → suite ran; take counts from that line.
- `STATUS=HANG  ... exit 124` → the run wedged and was tree-killed. Re-run that suite ONCE (the wrapper clears orphans first). If a second run also HANGs or counts DROP across retries, STOP — machine-state is exhausted; **reboot is the terminal fix** (memory: flaky executor recovery is non-monotonic).
- `WARN=SILENT_SKIP_SIGNATURE` → runtime executor connection failed; results INVALID regardless of count. Re-run once.

**Partial-count re-run guard:** if a suite returns `STATUS=DONE` but its `Passed:` count is BETWEEN the silent-skip sentinel floor and the committed baseline (a "partial-but-clean" flaky-executor result, e.g. Logic 6258 vs baseline 6348), do NOT trust it — re-run that one suite once before evaluating tiers. A count at-or-above baseline is the only trustworthy green.

**Filter prefix matters:** use the FULL namespace prefix `~Tests.Logic` (not `~Logic`). The shorter `~Logic` matches a *subset* of test classes whose FQN doesn't start with `{{PROJECT_NAME}}.Tests.Logic.*` — observed 618 vs 4921 test counts on the same suite, looks like silent-skip but isn't. See memory `GdUnit4_Process_Management`.

**Cloud/Linux:** the wrapper is Windows-only. On cloud (`CLAUDE_CODE_REMOTE=true`), run the bare `dotnet test --settings .runsettings --verbosity quiet --filter "..."` form — `cloud_test_enforcer.py` prefixes `xvfb-run` and the named-pipe path differs (see `.claude/rules/cloud_dev.md`).

**Validate counts (three-tier evaluation per suite):**

Before running the suites, **read `Tests/regression_baseline.json`** to load the committed baseline, sentinels, and thresholds. (If the file is missing, STOP and report the problem — the baseline file should always be present on any checked-out branch.)

**Baseline-trust check (stamp consistency):** the baseline is only trustworthy if it was written by a real green run via this gate (step 6 stamps `updated_on_commit` = `HEAD` at write time). Verify the stamp matches the commit that actually last modified the file: compare `updated_on_commit` against `git log -1 --format=%h -- Tests/regression_baseline.json`. If they DIFFER, the baseline was carried forward or hand-edited rather than produced by a green run at that state — emit `"WARN: baseline stamp (<updated_on_commit>) ≠ last commit that touched the file (<actual>); counts may be stale/masking failures. Treating Tier-2 as advisory; require a full clean Failed:0 run before trusting the new counts."` and do NOT auto-update the baseline this run until a clean green run re-establishes it. *Rationale: a stale baseline whose `passed` count kept growing from new-test additions can ride over pre-existing reds indefinitely — exactly the masking that hid 5 Integration failures whose stamp pointed at an older commit than the one that wrote the file.*

After each suite run, extract the `Passed: N, Failed: X, Skipped: Y` counts from the GdUnit4 output line, then evaluate in this order:

1. **Tier 1 — Silent-skip sentinel (architectural floor, non-drifting):**
   - If `current.passed < silent_skip_sentinels.<Suite>_min` → **HARD FAIL**. Report: `"Silent skip signature: <Suite>.passed=N is below architectural floor M. GodotRuntimeExecutor likely failed — runtime tests were silently skipped. Kill orphaned processes and re-run."`
   - These sentinels do NOT grow with the suite. `Logic_min=500` sits comfortably above the ~388 non-runtime-only count that appears when Godot runtime fails to authenticate.

2. **Tier 2 — Drift comparison (primary regression detector):**
   - Let `baseline = suites.<Suite>.passed` from the JSON file.
   - If `current.passed < baseline * drift_thresholds.hard_fail_ratio` (default 70%) → **HARD FAIL**. Report: `"Major drop: <Suite> current N vs baseline M (X% of baseline). Investigate for deleted/broken tests or compilation failures."`
   - If `current.passed < baseline * drift_thresholds.warn_ratio` (default 90%) → **WARN**. Report: `"Moderate drop: <Suite> N vs M (X% of baseline). Confirm with user before proceeding."` Ask user to acknowledge via `AskUserQuestion` (see Failure Handling below).
   - If `current.passed >= baseline` → **PASS**. This is the happy path for both "baseline matches exactly" and "baseline grew from new test additions."

3. **Always:** ALL suites must report `Failed: 0`. Any failure = gate FAILS regardless of count.

**CRITICAL — Silent skip detection (secondary check):** Separately from the Tier 1 count-based check, scan test output for `GodotRuntimeExecutor failed` or `Connection timeout` strings. If present, ALL runtime tests were silently skipped — the results are INVALID regardless of the Tier 1 verdict. Kill orphaned processes and re-run.

4. **Evaluate results:**
   - **ALL suites must report 0 failures** (Tier 3: `Failed: 0`) AND pass Tiers 1 and 2.
   - Record the pass/fail counts for use in commit messages AND for the baseline-update step below.

4b. **`[Tool]` cascade headless gate (high-fidelity backstop — runs AFTER the suites):**
```bash
powershell.exe -Command "Get-Process -Name 'Godot*' -ErrorAction SilentlyContinue | Where-Object { \$_.MainWindowTitle -notlike '*Godot Engine*' } | Stop-Process -Force -ErrorAction SilentlyContinue"
GODOT_BIN_PATH=$(grep -oP '<GODOT_BIN>\K[^<]+' "$CLAUDE_PROJECT_DIR/.runsettings")
"$GODOT_BIN_PATH" --headless --import --path "$CLAUDE_PROJECT_DIR" > "$CLAUDE_PROJECT_DIR/logs/tool_import_gate.log" 2>&1
grep -i -E "InvalidCastException|Unable to cast object of type" "$CLAUDE_PROJECT_DIR/logs/tool_import_gate.log" && echo "TOOL CASCADE GATE: FAIL (cast exception above)" || echo "TOOL CASCADE GATE: PASS"
```
**Ordering matters — run this AFTER step 3, NEVER before:** the import spawns a Godot process that interferes with the GdUnit4 suites' runtime executors if it precedes them — observed as a false-low Integration count (~its non-runtime floor) from a poisoned executor handoff. One-shot **editor-mode** import (`--import` self-quits; touches only `.godot`/`.import`, never `.tres`). Any `InvalidCastException` = a non-`[Tool]` class placed under a `[Tool]` parent's typed `[Export]` in an actual `.tres`/`.tscn` — a cascade gap that *currently throws*. Catches what the static gate (1c) cannot: **Node** cascades, **escape-hatch** inline placements (`[Export] Resource?`), and **Jmodot-side** gaps. Grep match → gate **FAILS**: read the log for the offending target type and add `[Tool]` to it (and its concrete subclasses). On Linux/cloud prefix with `xvfb-run --auto-servernum`. Empirically proven (Tool Attribute Audit Phase 0): a planted gap surfaced `InvalidCastException: Unable to cast object of type 'Godot.Resource' to type '<X>'`.

5. **On failure — MANDATORY user interaction:**
   - **NEVER skip, dismiss, or proceed past a failing test without explicit user direction.**
   - Present each failure clearly:
     ```
     Regression Gate: FAIL
       Logic:       N passed, X failed
       Integration: N passed, Y failed
       Sanity:      N passed, Z failed

     FAILING TESTS:
       1. [Suite] FullyQualifiedTestName — "error message summary"
       2. [Suite] FullyQualifiedTestName — "error message summary"
     ```
   - Then ask the user how to proceed using `AskUserQuestion`:
     - **Fix now** — Investigate and fix the failing test(s) before continuing
     - **Known issue** — User confirms this is a pre-existing/known failure; note it and continue (user takes responsibility)
     - **Abort** — Stop the current workflow entirely
   - **Wait for the user's response.** Do NOT auto-fix, auto-skip, or auto-continue.
   - If "Fix now": fix the issue, then re-run ALL suites (not just the fixed one).
   - If "Known issue": log the acknowledged failures in the report and continue, but commit messages must NOT include "Verified" — use "Verified (with known failures: TestName, ...)" instead.

6. **On success — update baseline file:**

   After all three suites pass (Tier 1, 2, 3 all clear AND `Failed: 0` everywhere), check whether the committed baseline needs updating:

   - **For each suite, compute the delta:** `delta = current.passed - baseline.passed`
   - **If ALL deltas are zero** (counts match exactly): no file update needed. Report as "baseline unchanged."
   - **If ANY delta is positive** (suite grew): use the `Write` tool to overwrite `Tests/regression_baseline.json` with the new counts. Preserve all other fields (`schema_version`, `silent_skip_sentinels`, `drift_thresholds`); update `updated_at` to the current UTC timestamp, and set `updated_on_commit` to the output of `git rev-parse --short HEAD` and `updated_on_branch` from `git branch --show-current`. Inform the user: *"Baseline auto-updated: Logic +N, Integration +M. Stage `Tests/regression_baseline.json` alongside your test additions when committing."*
   - **If ANY delta is negative** (suite shrank): DO NOT auto-update. The Tier 2 WARN flow should already have asked the user to acknowledge the drop. If the user confirmed the drop is intentional (e.g., removed a feature and its tests), they can explicitly ask you to update the baseline in a follow-up step — auto-lowering is too dangerous to do silently.

   The baseline file should NEVER be written when the gate is in a FAIL or unacknowledged-WARN state. The stash only ratchets forward (or stays) on a fully green run.

7. **Report verdict:**

The verdict is rendered in TWO sections: the test summary (low-level results) followed by the structured Pre-Commit Checklist (gate-decision view). Both are required output.

**7a. Test summary (existing format):**
```
Regression Gate: PASS
  Logic:       N passed, 0 failed  (duration)    [delta since baseline]
  Integration: N passed, 0 failed  (duration)    [delta since baseline]
  Sanity:      N passed, 0 failed  (duration)    [delta since baseline]

Baseline: Tests/regression_baseline.json (updated | unchanged)
```
Example deltas: `[+12]`, `[unchanged]`, `[-3 — drop acknowledged]`.

**7b. Pre-Commit Checklist (canonical format — also rendered by `/session_end` Phase 7 and `/merge_pr` Step 6):**

```
## Pre-Commit Checklist

[x] Logic suite: N passing, 0 failing  (Δ baseline: +X / -Y)
[x] Integration suite: N passing, 0 failing  (Δ: +X / -Y)
[x] Sanity suite: N passing, 0 failing  (Δ: +X / -Y)
[x] No silent skips detected (no `GodotRuntimeExecutor failed` / `Connection timeout`)
[x] No JmoLogger.Error fired during test runs
[<state>] /session_audit run this session, no MERGE-BLOCKER findings
[<state>] CLAUDE.md compliance self-check
[<state>] Refactor parity check (only required if files were deleted this session)

Verdict: APPROVE | APPROVE WITH NOTES | REQUEST CHANGES
```

**Checkbox states** (three-state, not two):
- `[x]` — verified true this session (e.g., `/session_audit` ran AND returned clean)
- `[ ]` — applicable but NOT yet verified (e.g., `/session_audit` not run; user must run or override)
- `[—]` — not applicable this session (e.g., no files deleted → parity check N/A; pure-meta commit → CLAUDE.md compliance N/A by exemption rule)

**Self-attest source rules** (orchestrator follows these to fill the bracketed state items):
- **`/session_audit` checkbox:** `[x]` if a `/session_audit` invocation in this session returned verdict APPROVE or APPROVE WITH NOTES; `[ ]` if no `/session_audit` ran this session (or it returned REQUEST CHANGES that wasn't resolved); `[—]` only on pure-meta commits exempt from `/session_audit`.
- **CLAUDE.md compliance checkbox:** `[x]` if no PreToolUse pattern enforcement violations fired this session AND the orchestrator can affirmatively cite session changes against the relevant CLAUDE.md sections (e.g., "no `GD.Print` introduced", "no `GetNode` in `_Process`"); `[ ]` if uncertain; `[—]` on commits touching no `.cs` files.
- **Refactor parity checkbox:** `[—]` (default, applicable only when `git diff --diff-filter=D` shows deleted files this session); `[x]` if `/session_audit` Phase 1.5b ran and reported no parity drops; `[ ]` if files were deleted but parity wasn't checked.

**Verdict mapping:**
- `APPROVE` — all applicable items `[x]` or `[—]`, no `[ ]`
- `APPROVE WITH NOTES` — all suite items `[x]`, but one or more advisory items `[ ]` that user has acknowledged
- `REQUEST CHANGES` — any suite item failing OR any test-related item `[ ]` blocking commit

This checklist is the **canonical artifact** for pre-commit gating. `/session_end` Phase 7 and `/merge_pr` Step 6 both render it (with PR-specific items added at the merge stage). It REPLACES the prior implicit "tests passed → ready to commit?" prompt with explicit, scannable invariants.

## Rules

- **NEVER proceed past failures without user direction.** This is the single most important rule.
- Never claim "Verified" without actually running the tests after the final staged state.
- The regression gate runs against the CURRENT working tree state — not a previous run's results.
- **The baseline file (`Tests/regression_baseline.json`) is tracked data.** Auto-update it on growth, never auto-lower it, treat diffs as commit-worthy artifacts the developer stages alongside whatever caused the growth.
- If called from another command (e.g., `/session_end`), the calling command decides whether to proceed or block based on the verdict.
- Commit messages for code commits should include: `Verified: Logic N/0, Integration N/0, Sanity N/0`. If the baseline file was updated, also note it (e.g., `baseline: Logic +3`).
