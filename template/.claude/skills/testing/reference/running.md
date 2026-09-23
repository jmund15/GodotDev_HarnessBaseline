# Running suites and diagnosing a run

Read at the run step, named from `SKILL.md` §Recipe index. The entrypoint owns the invariant flags (`--filter`, `--settings .runsettings`, never `--no-build`, `--verbosity quiet`, no pipes, timeout 600000) and the single-flight rule; this file owns the runners, the caps and the failure signatures.

## Scope-sized runs — prefer `verify.ps1`

`pwsh -NoProfile -File .claude/scripts/verify.ps1 -Scope <domain[,domain]>` expands a domain name into its Logic partition + Integration segments, both through the wrapper below. Prescribed after every slice and at every Part close (`change_control` §Gate cadence); never a gate — it records nothing and backs no commit. `-Filter "<raw>"` passes a raw filter through; `-SelfTest` asserts the domain map still matches on-disk namespaces.

## Hang-safe runs (Windows) — the wrapper underneath

`pwsh -NoProfile -File .claude/scripts/run_test_suite.ps1 -Filter "FullyQualifiedName~Tests.<Suite>" -Label <Suite>` file-redirects output and tree-kills on a hard wall-clock cap; bare `dotnet test`'s testhost→Godot grandchildren otherwise hold the caller's stdout pipe open so the read never EOFs. Returns `STATUS=DONE`/`STATUS=HANG` + the count line; `/regression_gate` uses it. The bare commands in `SKILL.md` §Quick Start stay valid as the **cloud path** (`xvfb-run`; both runners are Windows-only).

## Sizing `-TimeoutMs`

**Size `-TimeoutMs` proportionally — it is a hang-DETECTION deadline, so detection latency = the cap.** `cap ≈ clamp(2–2.5× expected run time, floor 90s)`; expected time from `Tests/integration_batch_durations.json` or a prior run's `Duration:` line. Do NOT under-cap: full Logic runs ~160s healthy (cap 6–8 min), and the FIRST run after `.tscn`/`.tres` edits pays reimport in-process (+60–90s) — a false HANG kill is worse than late detection, executor recovery being non-monotonic. Unknown expected time + fresh scene edits is the one case a generous blanket cap is correct. Executor briefs pass sized caps, never a copied 300000.

## Single-flight and parallel dev

`SKILL.md` §Quick Start owns the single-flight rule itself. `-NoGodotRuntime` is only for a filter provably containing zero runtime tests (per-worktree lock, no pipe drain).

Parallel runs use a worktree-scoped pre-flight tree-kill that still reaps unattributable orphans. Per-worktree pipe salt (`GDUNIT4_PIPE_SUFFIX` + forked gdUnit4.api) makes overlapping runs mis-connect instead of cross-talking, with per-worktree logs at `TestResults/godot_test.log`.

## Batched integration runs

`pwsh -NoProfile -File .claude/scripts/run_integration_batched.ps1` splits the suite into ~3 serial duration-balanced batches (weights: `Tests/integration_batch_durations.json`, committed + auto-refreshed on green), each through the wrapper with a batch-sized cap — a wedge costs one ~1-min retry, and each batch's fresh Godot process resets orphan accumulation. Ends with a sum-check vs baseline (`COMPLETENESS=OK` required). On `STATUS=HANG`/`BUDGET_EXCEEDED`, re-invoke `-RetryOnly` (greens skipped). Batches stay SERIAL — the gdunit4 connect pipe is machine-global per assembly. Per-batch boot (~40s) buys low variance; tune with `-TargetBatchSec` (default 60).

## Filter coverage and batching

- **`--list-tests` ignores `--filter`** (VSTest) — it dumps the whole assembly, so it cannot verify a filter's coverage; use group-sum arithmetic against the baseline.
- **Batch multi-suite evidence runs with `|` into ONE invocation** — `--filter "FullyQualifiedName~Tests.Logic.A|FullyQualifiedName~Tests.Integration.B"`. Each invocation pays a full rebuild + test-host boot (~40–90s). Split only when runs must be attributed separately (a RED proof, isolating one suite's wedge).

## Exit codes

| Code | Meaning | Action |
|------|---------|--------|
| `0` | Pass | ✓ |
| `100` | Failures OR executor timeout | Check test count - may be cosmetic |
| `101` | Warnings | Review orphan warnings |
| `-1073740791` | Godot crash (orphan accumulation) | Run in batches — never the full suite unfiltered |

## "GodotRuntimeExecutor timed out" — the silent skip

**This is a SILENT TEST SKIP.** All `[RequireGodotRuntime]` tests report "Passed" while never running — the regression gate is INVALID.

- **~388 is a silent-skip SENTINEL, not a suite size.** It is the count of Logic tests passing WITHOUT the Godot runtime — the signature when the executor fails to connect. The real Logic baseline is ~19× larger; current counts and machine-readable floors (`silent_skip_sentinels`, e.g. `Logic_min: 500`) live in `Tests/regression_baseline.json`, auto-updated on green by `/regression_gate` — never hardcode them. Logic ≈ 388 ⇒ silent skip however green the output looks.
- **Pre-test checklist:** kill orphaned Godot processes BEFORE running (positive identification only — Editor/Playtest/Unknown are constitutionally spared):
  ```powershell
  . .claude/scripts/GodotProcess.ps1
  $map = Get-ProcSnapshot
  Get-ReapableGodot -Checkout (Get-Location).Path -Map $map |
      ForEach-Object { taskkill /F /T /PID $_.ProcessId }
  ```
- **Post-test validation:** scan output for `GodotRuntimeExecutor failed` or `Connection timeout`. Present ⇒ results invalid; fix and re-run.
- **If the sentinel fires:** the executor isn't reaching Godot — kill orphans (above), then verify `GODOT_BIN`: User env var (`setx GODOT_BIN "C:\path\to\godot.exe"`) and/or `--settings .runsettings` (which hardcodes a machine-specific path — `environment_bootstrap` skill on a new machine).

## More gotchas

Search auto-memory (semantic-search) for "GdUnit4" or "Godot C# test gotchas".
