---
description: Gate C# commits and merges or behavior-coupled Godot data changes
allowed-tools: Bash, Monitor
---

## Purpose

Single source of truth for regression verification. Called by `/session_end`, `/commit_push`, `/review_pr`; also standalone. Rationale and non-negotiables: `change_control`. All mechanics live in `.claude/scripts/regression_gate.ps1`, whose header documents each invariant. **This file owns judgment only:** when the gate applies, failure adjudication, verdict rendering.

**Phase order:** the gate holds the checkout through build and tests. After the build, a headless `--import` refreshes the cache (`IMPORT_GATE`). Sanity runs first, alone, as the GdUnit4 recompile pass; a red Sanity ends the gate `FAIL` before the pool. Logic and pending Integration batches then run in one held pool, headless; `[RequiresRenderer]` classes run windowed as `Render`, the one unit a locked desktop (`SESSION=LOCKED`) slows. §Retry policy owns retry eligibility. Every run writes its TRX under `TestResults/runs/gate-<runId>/`, printed as `TRX_DIR=<path>`; `python3 .claude/tools/test_profile.py <TRX_DIR>` lists the slowest tests.

## When Required

- **Code commits:** any commit touching `.cs` (Logic, Gameplay, Tests, Jmodot), including comment- or whitespace-only edits.
- **Data commits with code coupling:** `.tres`/`.tscn` changes affecting Logic Domain behavior.

## When Exempt

- Pure meta commits (`.claude/`, `skills/`, `CLAUDE.md`, docs) — harness pushes use the close gate in `rules/harness_tooling.md`.
- Pure configuration/asset commits with no code coupling

A cloud session (`CLAUDE_CODE_REMOTE=true`) cannot run this Windows-bound gate. It runs the filtered verify in `rules/cloud_dev.md` instead; its PR states the local gate is still owed.

## Invocation

```bash
pwsh -NoProfile -File .claude/scripts/regression_gate.ps1 -Detach  # gate: final
```

**A gate run declares its chain position** — `hooks/gate_cadence_guard.py` exempts `-StaticOnly`, `-RouteOnly` and watch-only calls, not `-RetryOnly`; other invocations without a marker receive an advisory:

| Marker | Cap | Means |
|---|---|---|
| `# gate: final` | — | Drive close, before commits. |
| `# gate: prepush` | — | Before push/PR. |
| `# gate: checkpoint` | Long-drive exception; full gate; no numerical cap. |

There are no tiers to choose: the script picks the route. A run whose C# differs from the last full-green snapshot only in comments or whitespace, with every other build input identical, ends `PASS route=comment-only` after the build and `DOCS`; any other tree, an absent or unreadable snapshot, or a lexer failure runs the full gate. The route ignores line numbers: a comment edit above a `[CallerLineNumber]` call (a logger or a guard helper) shifts the line it logs. `change_control` §Gate cadence owns slice/lane verification, Part/drive close and fix re-verification. The hook never blocks `verify.ps1` or raw `-Filter` runs.

**`-Detach` is mandatory from an agent session.** A full gate outlives the 600 s foreground Bash ceiling, and a detached run survives a harness kill of the shell that launched it, which a `run_in_background` task does not. `-Detach` returns in ~1s with `OUT=<path>`, and the run is over only when a line begins `VERDICT=` in that file; a killed poller loses the wake-up, never the run.

Watch it through the gate's own stage watcher, the supported Monitor.

```bash
pwsh -NoProfile -File .claude/scripts/regression_gate.ps1 -Watch -Out "<OUT path>"
```

It streams the stage lines and the finished-batch transitions with no repeat, then exits on `VERDICT=`. Integration prints ONE `SUITE` line when all batches are done; its per-batch progress lives in `.claude/scratch/test_runs/integration_batches.json` (`label`, `status` GREEN/RED/PENDING, `passed`, `failed`, `elapsed`).
`change_control` §Gate cadence owns the digest boundary, edits during a run and current-tree validation of stored verdicts.

| Flag | Use |
|---|---|
| *(none)* | Full gate, or the comment-only route above. When an editor is live on this checkout it runs in the checkout's twin (`TWIN=`) and copies a green run's baseline files back (`TWIN_WRITEBACK=`); a busy checkout is waited on (`WAIT`), up to `-WaitSec`, then ends `BUSY` (exit 7); when the twin cannot be synced, the editor is a holder (see exit 7). |
| `-Detach` | Spawn the run as a detached process and exit ~1s with `DETACHED pid=`, `OUT=`, `ERR=`. Survives a kill of the launching shell; forwards every other flag. The child runs the whole wait. |
| `-Watch -Out <path>` | Read-only: stream lines selected by the stage-prefix pattern in `Invoke-StageWatch` and matching-run finished-batch transitions without repeats. The batch state file's `runId` must match the OUT's `gate_run_<runId>.out`. Exit 0 on `VERDICT=`. With usable PID metadata, a dead process without a verdict emits `WATCH_DEAD` and exits 1: rerun the gate. Missing or unusable PID metadata gives no dead-process termination guarantee; inspect OUT, ERR and the PID sidecar before leaving a watch unattended. Takes no lock and runs no gate. `-Batches <path>` overrides the batch state file; `-WatchIntervalSec <n>` sets the poll interval. |
| `-StaticOnly` | Preflight + guards + build + `DOCS`, ~20s; holds the checkout lease from entry through its verdict, so it waits behind a running gate; `VERDICT=STATIC_PASS` at exit 0. The right check for a pure-data commit skipping the `.cs` path. Does NOT satisfy the gate for `.cs`. A comment-only change still ends `PASS route=comment-only`, with or without this flag. |
| `-RetryOnly` | Restore prior green Integration batches individually when the state digest matches and each batch's label and segment list match. Other batches run. Missing state, missing current digest or digest mismatch prints `RETRY_ONLY_IGNORED reason=<why>` and restores no greens. Composition mismatch leaves only the unmatched batches pending. The digest boundary is `change_control` §Gate cadence's. Logic and Sanity still run. |

Read [`regression_gate_reference.md`](reference/regression_gate_reference.md) §Flags for `-IgnoreEditor`, `-RouteOnly`, `-WaitSec`, `-NoBaselineUpdate` and `-NoReuse`.

**The gate has no width knob.** `-Smoke`, `-SmokeImport`, `-Targeted` and `-SkipStatic` are deleted; the guard denies a stale invocation and names the replacement. Narrow runs use `run_test_suite.ps1 -Filter "<raw>"` or `verify.ps1 -Scope <domains>` under `change_control` §Gate cadence. These are different scripts under different verbs, so a narrow result cannot be mistaken for a gate verdict.

**`DOCS`** blocks on XML-doc defects (`CS1587|CS1574|CS1734|CS1570|CS1572|CS1711|CS0419`, vendored trees excluded), parsed from the build the gate already runs — no second build. Scope is the XML-doc warnings emitted by the project build, not a changed-file filter. CS1587 on an `[Export]` is a silently-missing Inspector tooltip, not cosmetic. Full-tree sweep: `.claude/scripts/doc_warning_check.sh`. Doctrine: `rules/csharp_patterns.md` §Core Conventions.

## Retry policy

Parsed test failures are red and never qualify for an automatic retry. Sanity retries a clean `PARTIAL` or `INVALID` result in a solo pass unless its status is `HANG`. Logic uses the same eligibility in the retry pool. Integration retries only batches left `HANG` or `SILENT_SKIP` in that pool. A clean aggregate Integration `SHORTFALL` is judged without an aggregate rerun. The code permits each eligible retry once. Its replacement result is then tiered.

## Reading the verdict

Stdout is ~10 lines; detail (failing tests, guard output, remediation) spills to `.claude/scratch/test_runs/gate_last_<runId>.md`; `gate_last.md` is a latest-run pointer copy. **Read the detail file only when the verdict is not PASS.**

Preflight emits `REAP` even when it kills nothing. Suite reaper lines reach stdout only when killed or spared counts are nonzero. Their detail is retained in the run's detail file. An absent suite line does not prove the site never ran. `scope=orphans-only` marks orphan-only candidates; `PEERS` reports cross-checkout gates; `WAIT` names what this gate waits on.

**`peer waiter live` in an `[activity]` line is not a blocker.** A gate waiting for its checkout stamps its record `kind=waiter`: it holds no build output and no runtime slot / checkout lock, so nothing defers to it. Only `kind=gate` blocks.

[`regression_gate_reference.md`](reference/regression_gate_reference.md) §Sample output shows a passing run's stdout, line by line.

**REUSE** — the ledger selects a producer with matching tree digest and mode, an eligible status (`PASS`, `FAIL`, `WARN` or `STATIC_PASS`) and a finish time within the code's age ceiling. The current engine probe must succeed because the toolchain is gitignored. The age ceiling is enforced by `regression_gate.ps1`'s REUSE scan; inspect it when judging an older artifact. The gate then exits with the producer's verdict instead of running: `REUSE from=<id> mode=<mode> age=<age> session=<session>` plus `BASELINE reused=<action>`. **Report it as `PASS (reused from <id>, digest-match)`, never as a fresh run.** `-NoReuse` forces a fresh run. This definition governs every REUSE below.

Read [`regression_gate_reference.md`](reference/regression_gate_reference.md) §Status vocabulary for `TREE_CHANGED`, `STATUS=LOCKED`, `ROLLBACK_TRIGGER` and `BASELINE action=skipped`.

| Exit | Verdict | What it means / what you do |
|---|---|---|
| 0 | `PASS` | All tiers clear. `BASELINE action=lowered suite=<s> drop=<n> deleted=<removed> added=<added>` clears that suite's moderate-drop WARN: committed net test deletions explain the drop, and the gate wrote the baseline itself. Proceed; stage any baseline diff. |
| 1 | `FAIL` | Real failures. Follow §Failure handling. |
| 2 | `INVALID` | Silent-skip signature or below the architectural floor. Untrustworthy results, **not** a regression signal — do not interpret counts; verify the affected surface per `change_control` §Gate cadence before any re-run. |
| 3 | `WARN` | An unexplained count drop; baseline was not written. The gate lowers a clean drop of either size (WARN band or the smaller PARTIAL one) no greater than the net test attributes deleted in `updated_on_commit..HEAD`, with no failures, complete results and no uncommitted test changes. Integration lowering requires `COMPLETENESS=OK`; a below-baseline aggregate reports `SHORTFALL`, so that path cannot explain its deletion drop. Unreadable history or data-driven cases leave the drop unexplained. Investigate WARN. When committed deletions explain the drop, fix any counting or ratchet defect and re-verify. When a TRX test-name diff against the baseline run shows every real test present, fix the counter, correct the stored count to the real-test total with that diff as evidence, and re-verify without asking the user. Ask the user only to accept lost coverage (a real test missing, or a drop nothing explains); never otherwise lower the baseline by hand. Stamp handling is `change_control` §Stamp trust's. |
| 4 | `BLOCKED` | Preflight, guard, or build red. Fix and re-run; `gate_last.md` carries each guard's remediation. |
| 6 | `INCOMPLETE` | Any of: a held child the wrapper refused (`HELD_REFUSED`) or whose recompile opt-out was ignored (`SKIP_RECOMPILE_IGNORED`); the gate itself threw (`GATE_ERROR <message>` on stdout, the exception and stack in the detail); a unit without readable results after its pool pass and any retry pass, with no machine-busy signature (a lock wait or a busy no-result unit ends `BUSY`, exit 7, instead). Nothing failed; partial counts prove nothing. `HELD_REFUSED`, `SKIP_RECOMPILE_IGNORED` and `GATE_ERROR` are harness faults (a broken held checkout, a consumed `gdUnit4.api` without the recompile opt-out, a gate defect): read the unit's `wrapper.out` or the stack named in the detail and fix the cause before a re-run. Otherwise re-run with `-RetryOnly` when the machine is less loaded. |
| 7 | `BUSY` | No verdict, and nothing failed. A busy checkout is waited on, holding nothing: `WAIT site=entry … holder=<kind> pid=<n> … held=<s> label="…" … left=<s>` names who holds it and for how long, up to `-WaitSec` (default 1800); at expiry the gate ends here with one `BUSY site=… holder=… next="…"` line and the detail's `## Busy — no verdict`. A holder is the checkout's lock (a gate, a suite wrapper, any lock taker), a live editor, or a test run that holds no lock. Also: an editor live on this checkout whose twin could not be synced (`TWIN_SYNC_FAILED`; the editor is the holder, `site=source`); a suite's lock-wait expiring or a budget-starved run (`site=locked` / `site=starved`; the line names the runtime-slot holders and says to re-invoke with `-RetryOnly`); an editor appearing mid-run (`site=editor-midrun`; a fresh gate routes a live checkout to its twin). **Never kill a holder** — re-invoke after it ends, or raise `-WaitSec`. A gate that waited behind a peer's run re-checks REUSE once it holds the lock, so it adopts that run's verdict with no second run. A gate in a DIFFERENT checkout is no holder: the two share only the runtime slots, so guards, build and Logic run in parallel and only runtime suites wait. A peer whose lock hold expires a lock-wait gives `LOCKED`/exit 7; only a peer kill mid-run with no lock-wait gives `CONTENTION`/exit 8. Not a failure. `-IgnoreEditor` bypasses the editor holder. |
| 8 | `CONTENTION` | A suite killed or died with no `Passed!/Failed!` line AND no `STATUS=LOCK_TIMEOUT`/`LOCKED` (those route to `LOCKED` → exit 7) WHILE a live peer gate/suite record overlapped the window — a peer's run, not a regression. Re-run once inline. Never adjudicate a CONTENTION artifact as FAIL. |
| 124 | `HANG` | A suite wedged and was tree-killed, or an Integration unit still hung or silent-skipped after its retry pass (§Retry policy). Re-run with `-RetryOnly` (the tree is unchanged); a repeated HANG routes per `change_control` §Green-output trust. |

Exits 0, 1, 3 and `STATIC_PASS` may be returned **via REUSE**. Exit **5 is not a gate path**: it is the standalone Integration runner's internal code (a LOCKED-only completion) for a bare `verify.ps1`/batcher run outside the gate. In gate mode every Integration unit is submitted at the start of one pool call, so exit 5 never arises. Retry eligibility is §Retry policy's. An Integration batch still `HANG` or `SILENT_SKIP` after its retry ends the gate `HANG` (exit 124).

**On `HANG` or `INVALID`, load [testing `reference/running.md`](/.claude/skills/testing/reference/running.md)** — it owns GdUnit4 runtime troubleshooting (wedged-wrapper `taskkill` by parent chain, named-pipe exhaustion, when reboot is the terminal fix).

**A repeated HANG with the same machine-busy or named-pipe signature, or falling engaged-test counts across controlled retries, calls for the runtime troubleshooting owner; stop blind retries and name the evidence.**

## Failure handling (exit 1)

**A FAIL blocks acceptance until repair is verified or the owner explicitly approves the named unresolved failures.**

Present each failure from `gate_last.md`:

```
Regression Gate: FAIL
  Logic:       N passed, X failed
  Integration: N passed, Y failed
  Sanity:      N passed, Z failed

FAILING TESTS:
  1. [Suite] FullyQualifiedTestName — "error message summary"
```

Investigate and repair in-scope causes, then re-verify under `change_control` §Gate cadence and its coverage limits. Repair needs no second owner approval. If repair is blocked, record the cause and the missing input; use the caller's unattended policy for owner-only choices.

A pre-existing failure does not waive that block. Record any approval and use `Verified (with known failures: TestName, ...)` in commit messages, never an unqualified "Verified".

**A reused FAIL is adjudicated exactly like a fresh FAIL** — its detail file carries the producer's failing-test list via the `REUSE` block, and the failure-adjudication flow applies unchanged.

## Multi-Part drives

**A mid-chain Part close does not gate and does not commit** (`change_control` §Gate cadence). Every gate run is a full gate, so its trailer is a plain `Verified: Logic N/0 + Integration M/0 + Sanity K/0`; there is no qualified mid-chain trailer, since there is no narrowed gate to write one for.

## Baseline

`Tests/regression_baseline.json` is **tracked data**; its `silent_skip_sentinels` floors detect a silent skip. The script writes it on a fully green run, including the explained deletion drops in the verdict table. Uncommitted test changes skip the write; FAIL/WARN never write. Its `engine_errors` map holds Godot ERROR-line counts per signature: a full run prints `ENGINE_ERRORS total=… new=… grown=…` and lists growth in the detail, advisory with no verdict effect; a green run lowers stored counts and never raises one. Stage its diff alongside the test changes, likewise `Tests/integration_batch_durations.json` and `.claude/hooks/tool_resource_classes.txt`.

**Adding a top-level `Tests/<X>/` folder:** read [`regression_gate_reference.md`](reference/regression_gate_reference.md) §New test folder.

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
[<state>] /session_audit admitted coverage, no MERGE-BLOCKER findings
[<state>] Refactor parity check (only required if files were deleted this session)

Verdict: APPROVE | APPROVE WITH NOTES | REQUEST CHANGES
```

**Checkbox states:** `[x]` verified true this session · `[ ]` applicable but NOT yet verified (the agent runs the check; if blocked, name the constraint and retain the unverified state) · `[—]` not applicable this session. A suite item satisfied **via REUSE** marks `[x]`.

**Row sources.** The suite rows come from the gate artifact (`gate_last.md`) and its `STATUS` lines. The review and parity rows come from the review ledger (`review_ledger.py rows --task <task id>`, or `--subject session:<id>` without a task):
- **`/session_audit`:** derive candidate width from `execution_depth.md` §Candidate width using the checked gate result. `[—]` when width is `none`; otherwise `[x]` when every admitted mandate is covered over the changed files' hashes with no MERGE-BLOCKER, and `[ ]` when admitted coverage is missing or a MERGE-BLOCKER stands. Check coverage with `review_ledger.py uncovered --files <changed paths> --mandates candidate --roster <admitted roster.json>`; `owed` lists commit summaries, not pairs. Never admit a review just to fill this row.
- **Refactor parity:** `[—]` unless `git diff --diff-filter=D` shows deletions this session; `[x]` when the ledger holds a covering `parity` row with no drops; `[ ]` when files were deleted and no such row exists.

**Verdict mapping:** `APPROVE` — all items `[x]` or `[—]`. `APPROVE WITH NOTES` — all suite items `[x]`, advisory items `[ ]` acknowledged by the user. `REQUEST CHANGES` — any suite item failing, or a blocking test-related item `[ ]`.

## Rules

- If called from another command, the caller decides whether to proceed or block on the verdict.
- Code-commit messages include `Verified: Logic N/0, Integration N/0, Sanity N/0`, plus a baseline note if it moved (e.g. `baseline: Logic +3`).
