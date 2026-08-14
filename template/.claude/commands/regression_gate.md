---
description: Verify regressions before any commit or merge — the single source-of-truth gate.
allowed-tools: Bash(pwsh:*), Bash(dotnet build:*), Bash(git:*)
---

## Purpose

Single source of truth for regression verification. Called by `/session_end`, `/commit_push`, and `/review_pr` before any commit or merge; also invocable standalone. Rationale for WHY this gate exists and its non-negotiables: `change_control` skill.

Every mechanical phase — orphan kill, engine preflight, the six static guards, build, the three suites, tier evaluation, the headless import gate, and the baseline ratchet — runs inside `.claude/scripts/regression_gate.ps1`. Its header documents each invariant and why it holds. **This file owns only what needs judgment:** when the gate applies, failure adjudication, and verdict rendering.

## When Required

- **Code commits:** any commit touching `.cs` (Logic, Gameplay, Tests, Jmodot)
- **Data commits with code coupling:** `.tres`/`.tscn` changes affecting Logic Domain behavior

## When Exempt

- Pure meta commits (`.claude/`, `skills/`, `CLAUDE.md`, docs)
- Pure configuration/asset commits with no code coupling

## Invocation

```bash
pwsh -NoProfile -File .claude/scripts/regression_gate.ps1
```

**Run it with `run_in_background: true`.** The suites alone exceed the Bash tool's 600000ms ceiling, and backgrounding frees the session while it runs. **Do not edit any file under test while it runs** — the gate verifies the CURRENT working tree, so a mid-run edit invalidates the result. Editing `.claude/` markdown is safe.

| Flag | Use |
|---|---|
| *(none)* | Full gate. The default; the only form that satisfies a `.cs` commit. Queues instead of running inline when an editor is live on this checkout — see Exit 7 below. |
| `-StaticOnly` | Preflight + guards + build + `DOCS`, ~20s, takes no test lock; reports `VERDICT=STATIC_PASS` at exit 0. The right check for a pure-data commit that skips the `.cs`-triggered path. Does NOT satisfy the gate for `.cs`. |
| `-IgnoreEditor` | Skip the editor-live check and run inline regardless. Use only when you know the "editor" the check sees is stale (e.g. a crashed process not yet reaped). |
| `-QueueStatus` | List pending/completed requests in `.claude/scratch/gate_queue/`; no gate run. A result whose `treeDigest` differs from the current tree is STALE — see Exit 7. |

**`DOCS`** blocks on XML-doc defects (`CS1587|CS1574|CS1734|CS1570|CS1572|CS1711|CS0419`, vendored trees excluded) parsed from the build the gate already runs — no second build, and `-SkipStatic` does not bypass it. Scope is whatever that build compiled, i.e. the changed code. CS1587 on an `[Export]` is a silently-missing Inspector tooltip, not a cosmetic warning. Full-tree sweep: `.claude/scripts/doc_warning_check.sh`. Doctrine: `rules/csharp_patterns.md` §Core Conventions.
| `-Smoke` | Chain mode (below): defers Sanity + import gate, never ratchets. |
| `-RetryOnly` | Forwarded to the batched Integration runner — reruns only non-green batches. Use after `HANG`. A budget overrun retries itself once without this flag. |
| `-SkipStatic` | Skip guards 1b–1g on a re-run after a suite-only fix. |
| `-NoBaselineUpdate` | Evaluate and report without writing the baseline. |
| `-NoReuse` | Force a fresh run — refuse a ledger REUSE even when a digest+mode+age match exists. Propagated through the queue (a `-NoReuse` run that queues re-fires without silently re-enabling reuse). |

## Reading the verdict

Stdout is ~10 lines; full detail (failing test names, guard output, remediation) spills to a per-run file `.claude/scratch/test_runs/gate_last_<runId>.md`; `gate_last.md` is a latest-run pointer copy. **Read the detail file only when the verdict is not PASS.**

`REAP` reports one line per reaping site — preflight here, plus `site=suite:<Label>` from the runner before every suite, surfaced only when a count is nonzero (all sites, zero-count included, land in `gate_last.md`). Three facts decide how to read it: **an ABSENT line means that site never ran, never "nothing was killed"**; `scope=orphans-only` marks a site whose candidate set is orphans alone, so it cannot report spares by construction; and a peer's processes are never reaped — `PEERS` shows the gate waiting or queueing behind them instead.

```
REAP site=preflight killed=0 scope=orphans-only
PEERS site=preflight runners=0 gates=0 action=proceed
ENGINE=OK ver=4.7.1
GUARDS nullstrip=OK tool_cascade=OK script_strip=OK trail_seam=OK gate_coverage=OK dup_double=OK
BUILD=OK
DOCS=OK
SUITE Logic       passed=8814 failed=0 tier=PASS delta=+0 status=DONE dur=161s
SUITE Integration passed=1839 failed=0 tier=PASS delta=+0 completeness=OK exit=0
SUITE Sanity      passed=48   failed=0 tier=PASS delta=+0 status=DONE dur=3s
IMPORT_GATE=PASS
BASELINE action=unchanged
VERDICT=PASS
```

**Reuse vocabulary.** When a prior run's verdict covers this run (content-exact digest match + identical mode + a fresh engine probe — the toolchain is gitignored, so it is probed, not assumed), the gate exits with the producer's verdict instead of running: `REUSE from=<id> mode=<mode> age=<age> session=<session>`, plus `BASELINE reused=<action>` surfacing the producer's baseline action. Report reused results as such (`PASS (reused from <id>, digest-match)`), never as a fresh run. `TREE_CHANGED=1 phase=<phase>` means the tree changed mid-run (a concurrent session's edit, or your own edit under test) — results after the change are INVALID, never a regression. `STATUS=LOCKED` from the Integration runner means a batch could not acquire the machine-global runtime mutex; the gate does NOT auto-retry it inline (the mutex may still be held) — it queues when the busy signature holds. `BASELINE action=skipped reason=uncommitted-test-changes` means the ratchet refused a tree carrying uncommitted test changes, which may be a peer's.

| Exit | Verdict | What it means / what you do |
|---|---|---|
| 0 | `PASS` | All tiers clear. Proceed; stage any baseline diff. |
| 1 | `FAIL` | Real failures. **Run the adjudication below — this is the one step you must not automate.** |
| 2 | `INVALID` | Silent-skip signature or below the architectural floor. Results are untrustworthy, **not** a regression signal — re-run; do not interpret counts. |
| 3 | `WARN` | Tier-2 moderate drop survived a re-run. Ask the user to acknowledge; baseline was not written. (An untrusted baseline stamp is repaired by the next fully green run — it never yields WARN by itself.) |
| 4 | `BLOCKED` | Preflight, guard, or build red. Fix and re-run; `gate_last.md` carries each guard's own remediation. |
| 6 | `INCOMPLETE` | Batches skipped for wall-clock budget; the automatic `-RetryOnly` pass did not close the gap, with NO machine-busy signature (no lock-wait, no `LOCKED` batches, no peer overlap) — the machine was simply slow. Nothing failed — counts are partial and prove nothing either way. Re-run when the machine is less loaded; prior greens are preserved. |
| 7 | `QUEUED` | An editor is live on this checkout, a peer gate or test run didn't clear, a budget-starved run deferred (machine-busy signature: lock-wait, `STATUS=LOCKED`, or a peer overlap — though a peer overlapping the suite window itself surfaces `CONTENTION`/exit 8 instead), or an editor appeared mid-run. **A peer GATE counts from its first moment** (via its `kind=gate` activity record), not from when it reaches its suites — two gates on one checkout contend for the shared build output and the runtime mutex, which is what produces HANG/UNPARSED suites. Returns in under a second — a request landed in `.claude/scratch/gate_queue/`, a detached watcher fires it once the machine is quiet. Not a failure; poll with `-QueueStatus` or wait for the SessionStart surfacing. `-IgnoreEditor` bypasses. |
| 8 | `CONTENTION` | A suite died with no `Passed!/Failed!` result line WHILE a live peer gate/suite record overlapped the window — a peer session's run, not a regression. Auto-requeued on the queued path; re-run once on the inline path. Never adjudicate a CONTENTION artifact as FAIL. |
| 124 | `HANG` | A suite wedged and was tree-killed after retry. Re-run once. |

Exits 0, 1, 3 (and `STATIC_PASS`) may be returned **via REUSE** — a prior run's verdict for byte-identical content, the same mode, and a probed-OK toolchain. A reused verdict is still subject to the exit-1 adjudication flow below.

Exit **5 is the Integration runner's internal code** (`BUDGET_EXCEEDED` or a LOCKED-only completion) — the gate converts it to the automatic `-RetryOnly` pass, the queue handoff, or exit 6; it is never a final gate exit.

**On `HANG` or `INVALID`, load the [Testing Skill](/.claude/skills/testing/SKILL.md)** — it owns GdUnit4 runtime troubleshooting (wedged-wrapper `taskkill` by parent chain, named-pipe exhaustion, when reboot is the terminal fix). Do not load it on the happy path; the script encodes the mechanics the gate itself needs.

**A second `HANG`, or counts that DROP across retries, means machine state is exhausted — stop retrying.**

**Direct `run_test_suite.ps1` invocations refuse, they don't queue.** Called outside the gate (documented pattern in the Testing skill, procgen skills), it builds into the same shared `.godot/mono/temp/bin/Debug/` and is exactly as destructive against an open editor — so at entry it emits `STATUS=EDITOR_OPEN label=<label>` and exits **126** rather than queueing. `-IgnoreEditor` overrides; the gate's own `-FromQueue` path forwards it so a watcher-fired run never self-blocks on the check it already passed.

**A queued/`-QueueStatus` result carries `treeDelta`.** Request-time and run-time trees can differ — the user kept editing while the run waited. The result is only valid for the tree it actually ran against (`head` + `treeDigest`, computed at run start); when it doesn't match the reader's current tree the result is stamped `treeDelta: true` and must not back a bare "Verified" claim.

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

**A reused FAIL is adjudicated exactly like a fresh FAIL** — its detail file carries the producer's failing-test list (via the `REUSE` block), and the AskUserQuestion flow applies unchanged.

## Chain mode (multi-Part same-session drives — user-authorized 2026-07-16)

Mid-chain commits in a multi-Part drive may run `-Smoke` instead of the full gate. Requirements:

- The **chain-final commit** (and any commit before a push/PR) runs the FULL gate. Baseline updates land only at full-gate runs.
- Trailers say `Verified: Logic N/0 + <domain> integration M/0 (chain smoke; full gate at chain close)` — never an unqualified `Verified:`.
- A Jmodot edit rippling beyond the Part's own surface upgrades that cycle to the full gate, at orchestrator judgment.
- If the chain-final full gate fails, bisecting the smoke-gated commits is the driving session's responsibility before any push.

Rationale: mid-chain the work is unpushed; full-suite marginal value concentrates at the share boundary.

> **Sub-suite filtered runs have no count sentinel.** If you hand-run a narrower filter than a whole suite, an executor-connect failure reports `Passed!` with only the non-runtime subset, in ms-scale time. Sanity-check count magnitude and duration; the TRX testName list is the arbiter. See `gotcha_unit_filtered_test_run_fake_green.md`.

## Baseline

`Tests/regression_baseline.json` is **tracked data**. The script ratchets it forward on a fully green run — **skipped, baseline left at its floor, when the tree carries uncommitted test changes** (which may be a peer's; the next clean-tree green run ratchets) — never lowers it, and never writes it in a FAIL/WARN state. Stage its diff alongside whatever caused the growth — likewise `Tests/integration_batch_durations.json` and `.claude/hooks/tool_resource_classes.txt` if they changed.

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

**Checkbox states** (three-state):
- `[x]` — verified true this session
- `[ ]` — applicable but NOT yet verified (user must run or override)
- `[—]` — not applicable this session

A suite item satisfied **via REUSE** marks `[x]`: the reused verdict is a content-exact digest match (byte-identical tree) with the same mode and a fresh engine probe. Report it as `PASS (reused from <id>, digest-match)`, never as a fresh run.

**Self-attest sources:**
- **`/session_audit`:** `[x]` if it ran this session returning APPROVE or APPROVE WITH NOTES; `[ ]` if it didn't run (or returned unresolved REQUEST CHANGES); `[—]` only on pure-meta commits.
- **CLAUDE.md compliance:** `[x]` if no PreToolUse pattern-enforcement violations fired AND you can affirmatively cite session changes against the relevant sections (e.g. "no `GD.Print` introduced"); `[ ]` if uncertain; `[—]` on commits touching no `.cs`.
- **Refactor parity:** `[—]` unless `git diff --diff-filter=D` shows deletions this session; `[x]` if `/session_audit` Phase 1.5b reported no parity drops; `[ ]` if files were deleted but parity wasn't checked.

**Verdict mapping:** `APPROVE` — all items `[x]` or `[—]`. `APPROVE WITH NOTES` — all suite items `[x]`, advisory items `[ ]` acknowledged by the user. `REQUEST CHANGES` — any suite item failing, or a blocking test-related item `[ ]`.

## Rules

- **NEVER proceed past failures without user direction.** The single most important rule.
- Never claim "Verified" without a gate run against the final staged state.
- The gate runs against the CURRENT working tree — not a previous run's results.
- **REUSE is the sanctioned exception to the previous two rules**: a content-exact digest match + identical mode + a fresh engine probe makes a reused verdict a run against byte-identical content and toolchain. Reused results are reported as such (`reused from <id>`), never as a fresh run; `-NoReuse` forces a fresh run.
- If called from another command, the caller decides whether to proceed or block on the verdict.
- Code-commit messages include `Verified: Logic N/0, Integration N/0, Sanity N/0`, plus a baseline note if it moved (e.g. `baseline: Logic +3`).
