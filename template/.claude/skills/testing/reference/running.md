# Running suites and diagnosing a run

Read at the run step, named from `SKILL.md` §Recipe index. `SKILL.md` §Quick Start owns the invariant flags and the runtime-slot rule; this file owns the runners, the caps and the failure signatures. The wrapper adds `--no-build` itself, solely for a gate's held child running against a checkout its own gate already built and is holding stable; an agent never passes the flag.

## Scope-sized runs — `verify.ps1`

`pwsh -NoProfile -File .claude/scripts/verify.ps1 -Scope <domain[,domain]>` expands a domain name into its Logic partition + Integration segments, both through the wrapper below. `change_control` §Gate cadence owns when to run scope and filter verification. `-Filter "<raw>"` passes a raw filter through; `-SelfTest` asserts the domain map still matches on-disk namespaces.

## Hang-safe runs (Windows) — the wrapper underneath

`run_test_suite.ps1`, `verify.ps1`, `regression_gate.ps1` and `run_integration_batched.ps1` accept `-Repo <checkout>`. Omitted: cwd's git toplevel, then the script's checkout. Main's hook redirects a linked worktree's invocation to main's code with the target's `-Repo`. The target owns its domain map, durations, baseline, `.runsettings` and project pins; main owns locks, slots, reaping and verdicts.

`pwsh -NoProfile -File .claude/scripts/run_test_suite.ps1 -Filter "FullyQualifiedName~Tests.<Suite>" -Label <Suite>` file-redirects output and tree-kills on a hard wall-clock cap; bare `dotnet test`'s testhost→Godot grandchildren otherwise hold the caller's stdout pipe open so the read never EOFs. Returns `STATUS=DONE`/`STATUS=HANG` + the count line — a `HANG` is never green (`drive_spine.md` §Halts H4). `/regression_gate` uses it.

**A live editor sends the run to the checkout's twin.** A build writes `.godot/mono/temp/bin/Debug/`, the output the editor has loaded, so when an editor or playtest is live on the target checkout, `run_test_suite.ps1`, `run_integration_batched.ps1`, `verify.ps1` and the gate sync its twin (`<main>/.claude/worktrees/<leaf>--twin`, `scripts/TwinCheckout.ps1`) to the same bytes, print `TWIN=<path> source=<path> digest=<d>`, and run there; a green gate copies its baseline files back. Only a twin that cannot be synced leaves the old answer: `TWIN_SYNC_FAILED`, then `STATUS=EDITOR_OPEN` and exit **126**. A twin's first run warms its import cache, so it is slower. An agent never passes `-IgnoreEditor` or runs a bare `dotnet build|test` on a live checkout; `hooks/editor_live_build_guard.py` denies both. `-IgnoreEditor` is for an editor process that is already dead. `-RouteOnly` prints `ROUTE=<path>` and exits before any build.

`-ForceRecompile` forces the gdUnit4 editor pass. Each admitted run prints `RECOMPILE=RUN|SKIP reason=<reason>`: a standalone run on a gdUnit4.api pin that supports skipping skips the pass when the checkout's completed-pass stamp still matches its C# paths, `[GlobalClass]`/`class_name` declarations and `.tscn`/`.tres`/`.import` content. Missing, unreadable or foreign stamps run the pass, and so does a pin without skip support. The gate's Sanity pass still runs.

## Sizing `-TimeoutMs`

**Omit `-TimeoutMs` for a data-sized cap.** The wrapper uses the same filter's last-green wall, else the target's duration data, with a 150 s floor. `GodotProcess.ps1` owns the headroom and measured basis. An explicit `-TimeoutMs` overrides it. A separate 90 s runtime-boot watchdog stops an attempt whose fresh Godot log never reaches `Waiting for client connecting`; it retries once and keeps `godot_test.hang<n>.log` for each hung attempt. A final hang still reports `STATUS=HANG`, never green.

## Runtime slots and parallel dev

`SKILL.md` §Quick Start owns the runtime-slot rule itself; this section owns its mechanisms, lines and rollback. `.claude/scripts/RuntimeSlots.ps1` owns the lock names, the slot count and every probe.

- **Checkout lock:** every wrapper run takes its checkout's lock, so runs from one checkout serialize (they share the build output and `TestResults/`) — except the gate's own pool: after the gate holds the checkout (build done, tree stable) its pool runs its own children concurrently inside that one hold, never taking the lock per child.
- **Runtime slots:** a runtime (Godot-spawning) run then takes one of the machine's runtime slots, so runs from different checkouts overlap up to the count: the committed default in `RuntimeSlots.ps1` (whose header owns the lock mechanics), lowered by `<git-common-dir>/runtime-slots`. That file holds one integer: `1` serializes every checkout at its next acquisition, a smaller value than the default lowers the count to it, and anything outside `1..RuntimeSlotMax` gives 1. `-NoGodotRuntime` takes no slot and skips the pipe drain; use it only for a filter provably containing zero runtime tests.
- **Lane width:** `orchestration` §2 Width owns the dispatch limit. Read the count from the `SLOT=<n>/<count>` line.
- **Choosing the default:** re-run the gate-scale overlap check after a machine change or a new overlap crash; record the measured numbers in the project's memory on runtime-suite pipe contention.
- **Lines:** the wrapper prints `SLOT=<slots>/<C> source=<…> abandoned=<0|1> peerAtStart=<0|1>` on every acquired run, `REAP site=slot-handoff` for a dead holder's reaped test tree, and `STATUS=LOCK_TIMEOUT lock=<checkout|runtime-slots>` (exit 125) when the wait expires. On an own-pool overlap the run's `STATUS=` line carries `selfOverlap=1`, never an `OVERLAP_SIGNATURE=` line. It also prints `USERDIR=<dir>`, the run's Godot `user://` root. A missing `SLOT=` line means the run never reached its locks.
- **Isolation:** a worktree-scoped pre-flight tree-kill still reaps unattributable orphans. Per-worktree pipe salt (`GDUNIT4_PIPE_SUFFIX` + forked gdUnit4.api) makes overlapping runs mis-connect instead of cross-talking, with per-worktree logs at `TestResults/godot_test.log`. Each checkout's test Godot also gets its own `user://`: the wrapper redirects testhost's `APPDATA` to `%LOCALAPPDATA%\{{PROJECT_NAME}}\test-appdata\<worktree-id>` (printed as `USERDIR=`), so fixed-name `user://` test files no longer race across parallel runs. The live game's `app_userdata` and its logs stay untouched.
- **Tagged-run log path:** a run given `-ResultsRoot` logs instead under `TestResults/runs/<runId>/<safe>/godot_test.log` (`<safe>` is its sanitized label), so the same-checkout isolation above extends to concurrent tagged runs' logs.

**Trigger candidates:** an `OVERLAP_SIGNATURE=` wrapper line, a `ROLLBACK_TRIGGER` gate line, or a `triggers.log` entry (`%TEMP%\harness-activity\runtime-slots\triggers.log`), in a run that overlapped a **foreign** peer runtime run — a run's own gate pool overlapping itself is never a trigger candidate: it is a plain RED, whose wrapper `STATUS=` line carries `selfOverlap=1` instead.

Trigger kinds: `testhost-native-crash`, `executor-connect`, `run-aborted`, `cache-file-error`

`refcounted-free-fatal` is never a trigger; it follows `CAUSE=REFCOUNTED_FREE` to the load-and-drop hunt. A test that goes red only under overlap is a defect to root-cause, not a trigger.

**Who acts:** the session that sees the candidate. It does not ask first. It commits but does not push.

A trigger is confirmed only when the trigger line's `filter=` passes solo: re-run it on the primary checkout with `run_test_suite.ps1 -Filter <filter> -SlotCount 1`, which holds every slot and so excludes every run from a current wrapper. A wrapper from before the N-slot commit cannot exclude runs on the higher slots, so its count-1 run is not exclusive. `STATUS=LOCK_TIMEOUT lock=runtime-slots` is not a verdict — re-run once no peer runtime run is live.

- **Confirmed overlap cause:** write `1` to `<git-common-dir>/runtime-slots` (every checkout's next acquisition, probe and watcher tick reads it, and runs already holding a slot finish); commit `$script:RuntimeSlotDefault = 1` on `main` (`fix(harness): runtime slots back to 1 after <kind> under overlap`); and record the incident, with its `triggers.log` line, in the project memory on runtime-suite pipe contention. The override file stays in place — removing it is part of any later re-enable.
- **Solo failure:** the cause is solo, and there is no rollback. Every trigger kind also has solo causes, which the project memory on runtime-suite pipe contention lists; follow the wrapper's `CAUSE`/diagnostic lines.

## Batched integration runs

`pwsh -NoProfile -File .claude/scripts/run_integration_batched.ps1` splits the suite into ~3 duration-balanced batches (weights: `Tests/integration_batch_durations.json`, committed + auto-refreshed on green), each through the wrapper with a batch-sized cap — a wedge costs one ~1-min retry, and each batch's fresh Godot process resets orphan accumulation. Ends with a sum-check vs baseline (`COMPLETENESS=OK` required). On `STATUS=HANG`/`LOCKED`, re-invoke `-RetryOnly`. The `/regression_gate` flag table owns its tree-digest rule. Standalone (`verify.ps1`, no held checkout) the batches run through the pool at width 1, so they stay serial and take no `--no-build`; inside a gate's held pool they run alongside Logic at pool width, not serially. Per-batch boot (~40s) buys low variance; tune with `-TargetBatchSec` (default 60).

## Per-test profile

`python3 .claude/tools/test_profile.py <runs-dir> [--top N] [--json]` reads every TRX under a `TestResults/runs/<runId>/` dir (a gate's Logic, Sanity and Integration runs, or a standalone `verify.ps1` run) and reports the slowest tests, per-class/per-segment totals and failures with messages.

## Filter coverage and batching

- **`--list-tests` ignores `--filter`** (VSTest) — it dumps the whole assembly, so it cannot verify a filter's coverage; use group-sum arithmetic against the baseline.
- **Batch multi-suite evidence runs with `|` into ONE invocation** — `--filter "FullyQualifiedName~Tests.Logic.A|FullyQualifiedName~Tests.Integration.B"`. Each invocation pays a full rebuild + test-host boot (~40–90s). Split only when runs must be attributed separately (a RED proof, isolating one suite's wedge).

## Exit codes

| Code | Meaning | Action |
|------|---------|--------|
| `0` | Pass | ✓, but only once the count checks out |
| `100` | Failures OR executor timeout | Failures are failures, and an executor timeout is an invalid run — never read either as cosmetic |
| `101` | Warnings | Review orphan warnings |
| `-1073740791` | Godot crash (orphan accumulation) | Run in batches — never the full suite unfiltered |

## "GodotRuntimeExecutor timed out" — the silent skip

**This is a SILENT TEST SKIP.** All `[RequireGodotRuntime]` tests report "Passed" while never running — the regression gate is INVALID.

- **The count sentinel:** a Logic count equal to the number of Logic tests that pass WITHOUT the Godot runtime is the signature of an executor that failed to connect, however green the output looks. The floors live in `Tests/regression_baseline.json` `silent_skip_sentinels`, which `/regression_gate` reads and evaluates — never hardcode them.
- **Pre-test cleanup:** the hang-safe wrapper's checkout-scoped pre-flight tree-kill reaps orphaned test processes before the run (Editor, Playtest and Unknown processes are spared). Any other process stop follows `CLAUDE.core.md` §Shell Discipline.
- **Post-test validation:** scan output for `GodotRuntimeExecutor failed` or `Connection timeout`. Present ⇒ results invalid; fix and re-run.
- **If the sentinel fires:** the executor isn't reaching Godot — rerun through the hang-safe wrapper, whose pre-flight reaps orphans (above), then verify `GODOT_BIN`; `environment_bootstrap` owns this machine's paths.
