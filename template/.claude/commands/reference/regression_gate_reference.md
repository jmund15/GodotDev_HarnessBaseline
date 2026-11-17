---
disable-model-invocation: true
---

# `/regression_gate` — flag rows, status vocabulary and sample output

Read at `/regression_gate` §Invocation when a run needs one of the flags below, and at §Reading the verdict when a line is not in the body's tables. Entrypoint: [`../regression_gate.md`](../regression_gate.md).

## Flags

| Flag | Use |
|---|---|
| `-IgnoreEditor` | Skip the editor-live check and the twin route, run inline. Use only when the "editor" it sees is stale (a crashed process not yet reaped); `hooks/editor_live_build_guard.py` denies it on a live checkout. |
| `-RouteOnly` | Print `ROUTE=<path>`, the checkout the gate would run in, and exit 0. On an idle checkout it takes no lock; when an editor is live it syncs the twin first (`scripts/TwinCheckout.ps1`), which can wait for a peer's run on the twin's lock and warms a cold twin's import cache, then releases the lock. |
| `-WaitSec <sec>` | Seconds to wait for a busy checkout (its lock held, a live editor, a foreign test runner) before ending `BUSY` (exit 7); the default is the script parameter's value, not a backlog estimate. The wait holds nothing and prints `WAIT` lines naming the holder. `0` = probe once, do not wait. Expiry wakes, it never kills. Forwarded through `-Detach`; a foreground wait remains subject to the Bash ceiling. |
| `-NoBaselineUpdate` | Evaluate and report without writing the baseline. A recording flag, not a width flag — coverage is unchanged. `/test_compact` needs it to capture counts without ratcheting. |
| `-NoReuse` | Force a fresh run — refuse a ledger REUSE even on a digest+mode+age match. Also refuses the re-check after a wait, and skips the comment-only route. |
| *(none)* | Full gate, or the comment-only route. When an editor is live on this checkout it runs in the checkout's twin (`TWIN=`) and copies a green run's baseline files back (`TWIN_WRITEBACK=`); a busy checkout is waited on (`WAIT`), up to `-WaitSec`, then ends `BUSY` (exit 7); when the twin cannot be synced, the editor is a holder. |
| `-Detach` | Spawn the run as a detached process and exit ~1s with `DETACHED pid=`, `OUT=`, `ERR=`. Survives a kill of the launching shell; forwards every other flag. The child runs the whole wait. |
| `-Watch -Out <path>` | Read-only: stream lines selected by the stage-prefix pattern in `Invoke-StageWatch` and matching-run finished-batch transitions without repeats. The batch state file's `runId` must match the OUT's `gate_run_<runId>.out`. Exit 0 on `VERDICT=`. With usable PID metadata, a dead process without a verdict emits `WATCH_DEAD` and exits 1: rerun the gate. Missing or unusable PID metadata gives no dead-process termination guarantee; inspect OUT, ERR and the PID sidecar before leaving a watch unattended. Takes no lock and runs no gate. `-Batches <path>` overrides the batch state file; `-WatchIntervalSec <n>` sets the poll interval. |
| `-StaticOnly` | Preflight + guards + build + `DOCS`, ~20s; holds the checkout lease from entry through its verdict, so it waits behind a running gate; `VERDICT=STATIC_PASS` at exit 0. The right check for a pure-data commit skipping the `.cs` path. Does NOT satisfy the gate for `.cs`. A comment-only change still ends `PASS route=comment-only`, with or without this flag. |
| `-RetryOnly` | Restore prior green Integration batches individually when the state digest matches and each batch's label and segment list match. Other batches run. Missing state, missing current digest or digest mismatch prints `RETRY_ONLY_IGNORED reason=<why>` and restores no greens. Composition mismatch leaves only the unmatched batches pending. The digest boundary is `change_control` §Gate cadence's. Logic and Sanity still run. |

## Growth lines

`gate_trend: <unit> <s>s vs at most <s>s before, over <n> earlier runs (load-normalized)` names a unit above its own recorded range: Sanity, Logic, Render, an Integration batch, or `pool` (the pool stage as a whole). A batch is a repack of units, so it must top its range by the range's width. The history is the newest `gate_run_*.out` files beside the invoking checkout; a failed or cut-short run counts for the units it finished. A grown batch also names the test classes added since the previous recorded run under its units' folders. Nothing prints when nothing grew, and the verdict never changes. A growth line is surfaced work under the worklog's do-now gate. `python3 .claude/tools/gate_trend.py --replay <test_runs dir>` shows what recorded runs would have printed; proof `tests/test_gate_trend.py`.

## New test folder

A new top-level `Tests/<X>/` folder is renamed under `Logic|Integration|Sanity`, or gated (a filter call in the script + a `suites.X` baseline entry + the guard's `GATED` list), or given an `EXCLUDED` entry with written rationale. Guard `1f` (`test_suite_gate_coverage_guard.py`) enforces this mechanically — an ungated suite is one the three filters never run.

## Status vocabulary

`TREE_CHANGED=1 phase=<phase>` — the tree changed mid-run (a peer's edit, or your own edit under test); results after the change are INVALID, never a regression. `STATUS=LOCKED` (Integration batch status, or the Logic/Sanity wrapper's `STATUS=LOCK_TIMEOUT`) — a suite could not acquire its runtime slot / checkout lock (the wrapper line's `lock=` names which); tiered `LOCKED`, never UNPARSED/INVALID/CONTENTION, and never auto-retried inline since the lock may still be held: the gate ends `BUSY` (exit 7) naming the slot holders. `ROLLBACK_TRIGGER suite=<label> OVERLAP_SIGNATURE=…` — a run that overlapped a peer runtime run hit a trigger kind; act per `testing/reference/running.md` §Runtime slots and parallel dev. `BASELINE action=skipped reason=uncommitted-test-changes` — the ratchet refused a tree carrying uncommitted test changes, possibly a peer's.

## Sample output

```
WAIT site=entry acquired=1 waited=0s abandoned=0   (a busy checkout prints WAIT ... holder=<kind> pid=<n> held=<s> left=<s> lines first)
HELD site=enter acquired=1 waitMs=31 abandoned=0 reaped=0
REAP site=preflight killed=0 scope=orphans-only
PEERS site=preflight crossCheckoutGates=1 action=proceed   (another checkout's gate — this run proceeds; its runtime suites may wait)
ENGINE=OK ver=4.7.1
GUARDS nullstrip=OK tool_cascade=OK script_strip=OK trail_seam=OK floorcell_seam=OK gate_coverage=OK dup_double=OK refcount_free=OK
BUILD=OK
DOCS=OK
IMPORT_GATE=PASS
SUITE Sanity      passed=48   failed=0 tier=PASS delta=+0 status=DONE dur=3s
SUITE Logic       passed=8814 failed=0 tier=PASS delta=+0 status=DONE dur=161s
SUITE Integration passed=1839 failed=0 tier=PASS delta=+0 completeness=OK exit=0
BASELINE action=unchanged
VERDICT=PASS
```
