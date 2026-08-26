---
description: Verify regressions before any commit or merge — the single source-of-truth gate.
allowed-tools: Bash(pwsh:*), Bash(dotnet build:*), Bash(git:*)
---

## Purpose

Single source of truth for regression verification. Called by `/session_end`, `/commit_push`, `/review_pr`; also standalone. Rationale and non-negotiables: `change_control`. All mechanics — orphan kill, engine preflight, six static guards, build, three suites, tier evaluation, headless import gate, baseline ratchet — live in `.claude/scripts/regression_gate.ps1`, whose header documents each invariant. **This file owns judgment only:** when the gate applies, failure adjudication, verdict rendering.

## When Required

- **Code commits:** any commit touching `.cs` (Logic, Gameplay, Tests, Jmodot)
- **Data commits with code coupling:** `.tres`/`.tscn` changes affecting Logic Domain behavior

## When Exempt

- Pure meta commits (`.claude/`, `skills/`, `CLAUDE.md`, docs)
- Pure configuration/asset commits with no code coupling

## Invocation

```bash
pwsh -NoProfile -File .claude/scripts/regression_gate.ps1 -Detach  # gate: final
```

**Every invocation declares its chain position** — `hooks/gate_cadence_guard.py` denies one that does not:

| Marker | Cap | Means |
|---|---|---|
| `# gate: final` | — | Drive close, before commits. |
| `# gate: prepush` | — | Before push/PR. |
| `# gate: checkpoint` | 2/session | Exceptionally long drive. |

Every position runs the same full gate; there are no tiers to choose. Between drive start and drive close the expected gate count is **zero** — commits batch to the close gate, and slice/Part verification is `verify.ps1 -Scope <domains>`, which the hook never blocks. Cadence canon: `change_control` §Gate cadence.

**`-Detach` is mandatory from an agent session; `run_in_background: true` is not durable.** Claude Code reaps a backgrounded Bash task once the session goes idle and the whole process tree dies with it — a full gate survives only while the session keeps issuing tool calls. `-Detach` returns in ~1s with `OUT=<path>`; poll that file (Monitor, or a `verify.ps1`-shaped wait) until a line begins `VERDICT=`, which is the only completion signal. A killed poller loses the wake-up, never the run. **Do not edit any file under test while it runs:** the gate verifies the CURRENT working tree, so a mid-run edit invalidates the result. Editing `.claude/` markdown is safe.

| Flag | Use |
|---|---|
| *(none)* | Full gate. The default; the only form that satisfies a `.cs` commit. Queues instead of running inline when an editor is live on this checkout (Exit 7). |
| `-Detach` | Spawn the run as a detached process and exit ~1s with `DETACHED pid=`, `OUT=`, `ERR=`. Survives the session going idle; forwards every other flag. Refused with `-FromQueue` (the watcher already detaches). |
| `-StaticOnly` | Preflight + guards + build + `DOCS`, ~20s, no test lock; `VERDICT=STATIC_PASS` at exit 0. The right check for a pure-data commit skipping the `.cs` path. Does NOT satisfy the gate for `.cs`. |
| `-IgnoreEditor` | Skip the editor-live check, run inline. Use only when the "editor" it sees is stale (a crashed process not yet reaped). |
| `-QueueStatus` | List pending/completed requests in `.claude/scratch/gate_queue/`; no gate run. A result whose `treeDigest` differs from the current tree is STALE (Exit 7). |
| `-WaitForQueue <sec>` | **Defaults to 1800.** On a queue handoff, block up to `<sec>` and exit with the queued run's verdict instead of exit 7. `0` = fire-and-forget. Expiry wakes, never cancels — the run stays queued. Forwarded through `-Detach`, where the block is free; a foreground wait hits the 600s Bash ceiling. |
| `-RetryOnly` | Forwarded to the batched Integration runner — reruns only non-green batches. Use after `HANG`. A budget overrun retries itself once without this flag. |
| `-NoBaselineUpdate` | Evaluate and report without writing the baseline. A recording flag, not a width flag — coverage is unchanged. `/test_compact` needs it to capture counts without ratcheting. |
| `-NoReuse` | Force a fresh run — refuse a ledger REUSE even on a digest+mode+age match. Propagated through the queue, so a `-NoReuse` run that queues re-fires without silently re-enabling reuse. |

**The gate has no width knob.** `-Smoke`, `-SmokeImport`, `-Targeted` and `-SkipStatic` are deleted; the guard denies a stale invocation and names the replacement. Narrow runs are `verify.ps1 -Scope <domains>` — a different script under a different verb, so a narrow result cannot be mistaken for a gate verdict. Rationale: `change_control` §Gate cadence.

**`DOCS`** blocks on XML-doc defects (`CS1587|CS1574|CS1734|CS1570|CS1572|CS1711|CS0419`, vendored trees excluded), parsed from the build the gate already runs — no second build. Scope is whatever that build compiled, i.e. the changed code. CS1587 on an `[Export]` is a silently-missing Inspector tooltip, not cosmetic. Full-tree sweep: `.claude/scripts/doc_warning_check.sh`. Doctrine: `rules/csharp_patterns.md` §Core Conventions.

## Reading the verdict

Stdout is ~10 lines; detail (failing tests, guard output, remediation) spills to `.claude/scratch/test_runs/gate_last_<runId>.md`; `gate_last.md` is a latest-run pointer copy. **Read the detail file only when the verdict is not PASS.**

`REAP` prints one line per reaping site — preflight, plus `site=suite:<Label>` before every suite — surfaced only on a nonzero count (all sites land in `gate_last.md`). An **ABSENT line means that site never ran**, never "nothing was killed"; `scope=orphans-only` marks a site whose candidates are orphans alone, so it cannot report spares; a peer's processes are never reaped — `PEERS` shows the gate waiting or queueing behind them.

**`peer waiter live` in an `[activity]` line is not a blocker.** A gate parked on its own queued run re-stamps its record `kind=waiter`: it holds no build output and no runtime mutex, so nothing defers to it. Only `kind=gate` blocks.

```
REAP site=preflight killed=0 scope=orphans-only
PEERS site=preflight runners=0 gates=0 action=proceed
PEERS site=preflight crossCheckoutGates=1 action=proceed   (another checkout's gate — this run proceeds; its runtime suites may wait)
QUEUE_WAIT id=q20260819-112809-3948 pending=1 attempt=1 remaining=1740s   (heartbeat, every 60s while parked)
ENGINE=OK ver=4.7.1
GUARDS nullstrip=OK tool_cascade=OK script_strip=OK trail_seam=OK gate_coverage=OK dup_double=OK refcount_free=OK   (plus any project-specific guards)
BUILD=OK
DOCS=OK
SUITE Logic       passed=8814 failed=0 tier=PASS delta=+0 status=DONE dur=161s
SUITE Integration passed=1839 failed=0 tier=PASS delta=+0 completeness=OK exit=0
SUITE Sanity      passed=48   failed=0 tier=PASS delta=+0 status=DONE dur=3s
IMPORT_GATE=PASS
BASELINE action=unchanged
VERDICT=PASS
```

**REUSE** — a prior verdict covers this run when all three hold: content-exact tree digest match, identical mode, fresh engine probe (the toolchain is gitignored, so probed, never assumed). The gate then exits with the producer's verdict instead of running: `REUSE from=<id> mode=<mode> age=<age> session=<session>` plus `BASELINE reused=<action>`. **Report it as `PASS (reused from <id>, digest-match)`, never as a fresh run.** `-NoReuse` forces a fresh run. This definition governs every REUSE below.

**Status vocabulary.** `TREE_CHANGED=1 phase=<phase>` — the tree changed mid-run (a peer's edit, or your own edit under test); results after the change are INVALID, never a regression. `STATUS=LOCKED` (Integration batch status, or the Logic/Sanity wrapper's `STATUS=LOCK_TIMEOUT`) — a suite could not acquire the machine-global runtime mutex; tiered `LOCKED`, never UNPARSED/INVALID/CONTENTION, and never auto-retried inline since the mutex may still be held: it queues when the busy signature holds. `BASELINE action=skipped reason=uncommitted-test-changes` — the ratchet refused a tree carrying uncommitted test changes, possibly a peer's.

| Exit | Verdict | What it means / what you do |
|---|---|---|
| 0 | `PASS` | All tiers clear. Proceed; stage any baseline diff. |
| 1 | `FAIL` | Real failures. **Run the adjudication below — the one step you must not automate.** |
| 2 | `INVALID` | Silent-skip signature or below the architectural floor. Untrustworthy results, **not** a regression signal — re-run; do not interpret counts. |
| 3 | `WARN` | Tier-2 moderate drop survived a re-run. Ask the user to acknowledge; baseline was not written. An untrusted baseline stamp is repaired by the next fully green run — it never yields WARN alone. |
| 4 | `BLOCKED` | Preflight, guard, or build red. Fix and re-run; `gate_last.md` carries each guard's remediation. |
| 6 | `INCOMPLETE` | Batches skipped for wall-clock budget, the automatic `-RetryOnly` pass not closing the gap, with NO machine-busy signature (no lock-wait, no `LOCKED` batches, no peer overlap) — the machine was slow. OR a watcher-fired (`-FromQueue`) run hit `LOCKED` after the watcher already waited for machine-wide quiet; it re-queues exit 6, capped. Nothing failed; partial counts prove nothing. Re-run when the machine is less loaded — prior greens are preserved. |
| 7 | `QUEUED` | Any of: an editor live on this checkout; a live peer GATE or test run **on this checkout** not clearing in 90s; a suite's lock-wait expiring (`LOCKED`, `runtime-mutex-busy`); a budget-starved run deferring on a machine-busy signature (lock-wait, `STATUS=LOCKED`, peer overlap); an editor appearing mid-run. A peer whose mutex hold expires a lock-wait gives `LOCKED`/exit 7; only a peer kill mid-run with no lock-wait gives `CONTENTION`/exit 8. **A peer GATE counts from its first moment** (its `kind=gate` record), not from when it reaches its suites: two gates on one checkout contend for the shared build output and the runtime mutex, which produces HANG/UNPARSED suites. A gate in a DIFFERENT checkout does not queue this one — sharing only the machine-global mutex, its guards/build/Logic run in parallel and only runtime suites wait; a starved wait degrades to `LOCKED` and queues from there. The request lands in `.claude/scratch/gate_queue/`; a detached watcher fires it once the machine is quiet. **A bare exit 7 means only that the 1800s default wait expired without a verdict** — the normal path adopts the queued verdict when it lands. Resume by re-invoking with the same flags: a finished run serves from the ledger via REUSE, a pending one dedups by tree digest into the same request. Not a failure. `-IgnoreEditor` bypasses. |
| 8 | `CONTENTION` | A suite killed or died with no `Passed!/Failed!` line AND no `STATUS=LOCK_TIMEOUT`/`LOCKED` (those route to `LOCKED` → exit 7) WHILE a live peer gate/suite record overlapped the window — a peer's run, not a regression. Auto-requeued on the queued path; re-run once inline. Never adjudicate a CONTENTION artifact as FAIL. |
| 124 | `HANG` | A suite wedged and was tree-killed after retry. Re-run once. |

Exits 0, 1, 3 and `STATIC_PASS` may be returned **via REUSE**; a reused exit 1 still runs the adjudication below. Exit **5 is the Integration runner's internal code** (`BUDGET_EXCEEDED` or a LOCKED-only completion) — the gate converts it to the automatic `-RetryOnly` pass, the queue handoff, or exit 6; never a final gate exit.

**On `HANG` or `INVALID`, load the [Testing Skill](/.claude/skills/testing/SKILL.md)** — it owns GdUnit4 runtime troubleshooting (wedged-wrapper `taskkill` by parent chain, named-pipe exhaustion, when reboot is the terminal fix). Not on the happy path; the script encodes the mechanics the gate needs.

**A second `HANG`, or counts that DROP across retries, means machine state is exhausted — stop retrying.**

**Direct `run_test_suite.ps1` invocations refuse, they don't queue.** Called outside the gate (a documented pattern in the Testing skill and any project skill that runs suites directly), it builds into the same shared `.godot/mono/temp/bin/Debug/` and is as destructive against an open editor — so at entry it emits `STATUS=EDITOR_OPEN label=<label>` and exits **126**. `-IgnoreEditor` overrides; the gate's `-FromQueue` path forwards it so a watcher-fired run never self-blocks on the check it already passed.

**A queued run cannot be lost — three channels, in order of immediacy.** (1) The gate **waits by default** (`-WaitForQueue`, 1800s) and exits with the queued run's real verdict; backgrounded, that process exit wakes the session. (2) Otherwise the result is announced **on the next prompt** as a `[gate-queue]` line (`hooks/activity_registry.py`) and at the next SessionStart (`hooks/session_context_loader.py`); both read `hooks/gate_queue_surface.py`, which tracks seen-state **per session**, so two sessions on one checkout are each told independently. (3) `-QueueStatus` on demand. Only a result the watcher never produced is invisible — check `.claude/scratch/gate_queue/watcher.log` if a request stays `pending` past its expected run time.

**A queued/`-QueueStatus` result carries `treeDelta`.** Request-time and run-time trees can differ. The result is valid only for the tree it ran against (`head` + `treeDigest`, computed at run start); when that does not match the reader's current tree it is stamped `treeDelta: true` and must not back a bare "Verified" claim.

## Failure handling (exit 1) — MANDATORY user interaction

**NEVER skip, dismiss, or proceed past a failing test without explicit user direction.**

Present each failure from `gate_last.md`:

```
Regression Gate: FAIL
  Logic:       N passed, X failed
  Integration: N passed, Y failed
  Sanity:      N passed, Z failed

FAILING TESTS:
  1. [Suite] FullyQualifiedTestName — "error message summary"
```

Then ask via `AskUserQuestion`:
- **Fix now** — investigate and fix before continuing
- **Known issue** — user confirms pre-existing; note it and continue (user takes responsibility)
- **Abort** — stop the workflow entirely

**Wait for the response.** Do NOT auto-fix, auto-skip, or auto-continue. If *Fix now*: fix, then re-run **ALL** suites, not just the fixed one. If *Known issue*: commit messages must NOT say "Verified" — use `Verified (with known failures: TestName, ...)`.

**A reused FAIL is adjudicated exactly like a fresh FAIL** — its detail file carries the producer's failing-test list via the `REUSE` block, and the AskUserQuestion flow applies unchanged.

## Multi-Part drives

**A mid-chain Part close does not gate and does not commit** — it verifies by `verify.ps1 -Scope` over its accumulated blast zone, and its work batches to the drive close, whose full gate backs every commit in the drive (`change_control` §Gate cadence). Every gate run is a full gate, so its trailer is a plain `Verified: Logic N/0 + Integration M/0 + Sanity K/0`; there is no qualified mid-chain trailer, since there is no narrowed gate to write one for. Post-failure re-verification after a fix (integration-local → `-RetryOnly`; domain-spanning → `verify -Scope <domains>`; shared-state → whole-tier re-run) is owned by `change_control` §Gate cadence.

> **Sub-suite filtered runs have no count sentinel.** Hand-run a narrower filter than a whole suite and an executor-connect failure reports `Passed!` with only the non-runtime subset, in ms-scale time. Sanity-check count magnitude and duration; the TRX testName list is the arbiter. See `gotcha_unit_filtered_test_run_fake_green.md`.

## Baseline

`Tests/regression_baseline.json` is **tracked data**. The script ratchets it forward on a fully green run — **skipped, baseline left at its floor, when the tree carries uncommitted test changes** (possibly a peer's; the next clean-tree green run ratchets) — never lowers it, and never writes it in a FAIL/WARN state. Stage its diff alongside whatever caused the growth, likewise `Tests/integration_batch_durations.json` and `.claude/hooks/tool_resource_classes.txt`.

A negative delta is never auto-applied. If the user confirms a drop is intentional (feature and its tests removed), they ask for the update explicitly in a follow-up.

**Adding a top-level `Tests/<X>/` folder** requires renaming it under `Logic|Integration|Sanity`, or gating it (a filter call in the script + a `suites.X` baseline entry + the guard's `GATED` list), or an `EXCLUDED` entry with written rationale. Guard `1f` (`test_suite_gate_coverage_guard.py`) enforces this mechanically — an ungated suite is one the three filters never run.

## Report verdict

Two sections, both required.

**7a. Test summary:**
```
Regression Gate: PASS
  Logic:       N passed, 0 failed  (duration)    [delta since baseline]
  Integration: N passed, 0 failed  (duration)    [delta since baseline]
  Sanity:      N passed, 0 failed  (duration)    [delta since baseline]

Baseline: Tests/regression_baseline.json (updated | unchanged)
```
Example deltas: `[+12]`, `[unchanged]`, `[-3 — drop acknowledged]`.

**7b. Pre-Commit Checklist** (canonical format — also rendered by `/session_end` Phase 7 and `/merge_pr` Step 6):

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

**Checkbox states:** `[x]` verified true this session · `[ ]` applicable but NOT yet verified (user must run or override) · `[—]` not applicable this session. A suite item satisfied **via REUSE** marks `[x]`, reported as `PASS (reused from <id>, digest-match)`.

**Self-attest sources:**
- **`/session_audit`:** `[x]` if it ran this session returning APPROVE or APPROVE WITH NOTES; `[ ]` if it didn't run, or returned unresolved REQUEST CHANGES; `[—]` only on pure-meta commits.
- **CLAUDE.md compliance:** `[x]` if no PreToolUse pattern-enforcement violations fired AND you can cite session changes against the relevant sections (e.g. "no `GD.Print` introduced"); `[ ]` if uncertain; `[—]` on commits touching no `.cs`.
- **Refactor parity:** `[—]` unless `git diff --diff-filter=D` shows deletions this session; `[x]` if `/session_audit` Phase 1.5b reported no parity drops; `[ ]` if files were deleted but parity wasn't checked.

**Verdict mapping:** `APPROVE` — all items `[x]` or `[—]`. `APPROVE WITH NOTES` — all suite items `[x]`, advisory items `[ ]` acknowledged by the user. `REQUEST CHANGES` — any suite item failing, or a blocking test-related item `[ ]`.

## Rules

- **NEVER proceed past failures without user direction.** The single most important rule.
- Never claim "Verified" without a gate run against the final staged state.
- The gate runs against the CURRENT working tree — not a previous run's results.
- **REUSE is the sanctioned exception to the previous two rules**, on its three conditions above; report it as reused, never as a fresh run.
- If called from another command, the caller decides whether to proceed or block on the verdict.
- Code-commit messages include `Verified: Logic N/0, Integration N/0, Sanity N/0`, plus a baseline note if it moved (e.g. `baseline: Logic +3`).
