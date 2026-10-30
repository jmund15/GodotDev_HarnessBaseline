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
| `-NoReuse` | Force a fresh run — refuse a ledger REUSE even on a digest+mode+age match. Also refuses the re-check after a wait. |

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
