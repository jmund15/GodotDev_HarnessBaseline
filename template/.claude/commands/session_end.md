---
disable-model-invocation: false
description: Close the session with checks, commits and push
---

Start closeout only when the drive's work is done: every Skill a phase invokes re-attaches after each later compaction. Each phase runs when its input predicate holds, at the scope its admission sets; the regression gate and the commit are floor. Each phase's command owns its steps; load that command only when reached. Announce the phase and retain its result. Reading instructions or invoking a Skill is not completion. A nonblocking error permits later independent phases; it remains a blocked receipt, not a green precommit check.

## Arguments and choices

No arguments → close this session. `resume` → reuse Phases 2–7's current receipts from an earlier closeout attempt in this session; Phase 1 never reuses a receipt, it reads the review ledger. Any other text is the owner's note on how to close (for example "light audit"): run the close and apply the note where a phase leaves a choice, such as the audit width within what admission allows. A note never skips the floor phases or the commit.

Resolve every collected choice before Phase 7. Apply your recommendation to a reversible choice. Ask a genuine owner-only fork as a packet through `AskUserQuestion` per `CLAUDE.core.md` §Preferences (unless `overnight_ask_guard.py --status` reports `armed`, or `/goal` is active: then `/overnight` §Step 2 decide-or-park owns it), then act on the answer. The close report states what was decided and never ends on open questions. Fix in-scope defects rather than asking about reversible repairs.

A recipe phase whose output a command can check runs as one `/delegate` job once Phase 0 ends: its brief names this session's id, the plans Phase 0 found and each admitted phase's file, and its `task` check lists each admitted phase's artifact assertion from the table below. At brief write, this session runs `close_predicates.py --only <stem>` for each candidate phase and admits the phases §Gated phases runs; this session records each skipped phase, outside the job. Consume it before Phase 3.6 closes dispatch ratings and before any phase that `--depends` on a delegated receipt (Phase 5.5 on `roadmap_atlas`). Phase 7a's full `session_end_check.py --artifacts --stage precommit` stays this session's. The job returns each phase's artifact path and every owner-only choice; this session records the receipts after checking each one's declared inputs against the phase's read and write scope (`## Completion receipts and final check`). Run every other phase here, through Skill where supported; commands marked `disable-model-invocation` run by reading their file and following it under this explicit closeout request.

| Phase | Artifact the command writes | Assertion a failed or skipped run cannot satisfy |
|---|---|---|
| 3.5 Routing Audit Aggregation | `logs/routing_audit_stats.json` (`aggregate_routing_audit.py`) | the file's mtime is later than the job's brief, and it parses as JSON; a stale file from an earlier run fails the mtime test |
| 5.4 Roadmap Atlas | `logs/roadmap_atlas_<sid8>.json` (the project's roadmap-atlas generator, `--json`) | the file parses as JSON; the shell redirect creates it before the generator succeeds, so an empty or partial file fails the parse |

## Gated phases

Phases 2, 3.5, 3.6, 3.7, 4, 5, 5.4, 5.5, 5.6, 6 (`worklog`) and 8 (`reindex_search`) open with their predicate: `python3 .claude/tools/close_predicates.py --session <id> --only <stem> --json .claude/scratch/session_end/<sid>/predicate_<stem>.json`. A `false` value skips the phase: record `python3 .claude/tools/session_end_check.py --session <id> --record <stem> --status skipped --predicate <that file> --input <that file> --reason "<the predicate text>"`. A `true` or `null` value runs the phase, and null (an input the predicate could not read) never skips. Every later check, `--resume` included, re-evaluates a false-predicate skip; when the predicate now reads true or null, the receipt shows `stale-skip` and the phase runs. Phases 0, 1, 3 and 7 run every time; Phase 1 records its admitted scope, including no lenses. Phase 8 also keeps its plugin-unavailable skip.

## Phase 0: Session Digest

Run `python3 .claude/tools/session_digest.py --prompt-tail session_end`. Check the header's session identity; if it names a different session, run `python3 .claude/tools/session_digest.py --list 20`, identify this transcript, and rerun with `--session <exact-session-id>`. The command writes the complete JSON plus a compact evidence index, then prints a bounded digest and, when one exists, the session's active task record (`tools/task_record.py`). Keep both artifacts on disk.

Use the bounded digest to identify requirements. Account for every user requirement and friction row: page through both summary sets with `session_digest.py --digest-file logs/session_digest_<sid8>.json --evidence-page <prompts-or-friction> --page <N>`. Each response states the final page, so omitted rows cannot look complete. Fetch any full row needed for a disposition with the same command plus `--select <U-or-F-id>` instead; repeat `--select` for a small batch. Every friction ID needs `fixed`, `harness` (target and correction), `worklog` (accepted item), or `accepted` (reason). Record accounted and unresolved counts. Missing scan coverage stays unknown. Never load the complete JSON or index wholesale into model context.

Run `session_end_check.py --session <id> --resume`. Its observations distinguish access, invocation and receipt status. Resume at the first missing or stale phase only if this session previously started closeout; otherwise run all phases. Changed phase inputs or upstream evidence invalidate reuse.

Run `python3 .claude/tools/lf_normalize.py`; it skips dirty files.

## Phase 1: Session Audit

The session-audit command is a coding-layer command: where the project does not ship it, record Phase 1 skipped with that reason. Otherwise write the session scope through the [Session File Identification Procedure](agents/session_file_identification.md) Step 1 (`close_predicates.py --scope`); a null scope blocks Phase 1 with its `error`. Keep replacements and retired-code parity in that scope.

Read review admission from `triage.admits("candidate-lenses", fields, rigor, gate_green=<checked gate status>)`, with `fields = triage.current(root, rev=<hash>)` for each scope commit and `triage.rigor(task_id, root)`. For uncommitted session-owned changes, read `triage.current(root, task_id=<active task>)` and check that its paths cover those changes; an absent or incomplete record needs current triage before admission. Keep every admission and its inputs in the phase note; pass `gate_green=True` only with fresh applicable gate evidence. Use the widest admitted width (`none < sized < all`) across the session. The coding layer's execution-depth reference (§Candidate width) owns the admission and the sizing.

- `none`: no candidate review; record the admission, not missing lens coverage.
- `sized`: run the session-audit command with `--files <the scope's files> --lens <keys>`, where the keys are `sa-architecture-sweep` plus each candidate lens §Candidate width's sizing adds; record the lenses and why.
- `all`: select today's candidate set under §Mandate admission and run the session-audit command with `--files <the scope's files> --lens <the admitted keys>`.

Read `triage.admits("harness-quality-seat", fields, rigor)` over the same inputs and add `sa-harness-quality` only when admitted. A restricted run passes every selected key with `--lens`; exclude unadmitted conditional seats from the full roster too. An empty file scope needs no review.

The audit's §1a reads `review_ledger.py uncovered` for the selected roster and reviews only uncovered pairs. Consume every admitted lens result and disposition every finding; an absent or partial admitted lens is missing coverage. Rule `held` ids and failed checks per [`orchestrator_action_protocol.md`](agents/orchestrator_action_protocol.md) Step 1.5 and Step 4. When the audit reviewed more than one unit, run `python3 .claude/tools/review_ledger.py whole-scope --subject session:<sid> --scope-json <the scope file> --roster <the roster>` after the residue review. Its `wholeScope: false` admits the session-audit command with `--files <the scope's files> --lens sa-holistic`; otherwise record its `call`. One reviewed unit has nothing to interact with and owes no holistic pass.

Run `python3 .claude/tools/triage.py rederive <hash>^!` for each scope commit and flag recorded trigger values below the derived ones. Record Phase 1 with `--depends session_digest`, retaining scope, admissions, rederive output and the applicable ledger/review results. No admitted lenses is a completed admission check, not a full audit.

## Phase 2: Autolearn

Gate: `--only autolearn` (a friction row or a prompt row with signals in the digest). Run `/autolearn` with the bounded Phase 0 digest, its index counts, selected decisive rows, and Phase 1 results. Preserve its full procedure. Every friction ID must have a disposition.

## Phase 3: Self-Evaluate

Run `/self_evaluate`. Its digest and nuance-recall pass is Phase 2's, not a second dispatch (`.claude/commands/autolearn.md` §Step 0); when Phase 2 recorded a skip, `/self_evaluate` runs that pass itself. Save one structured entry for this exact session and verify it exists. Preserve failed, skipped and partial work as such; green slices do not establish the full goal.

## Phase 3.5: Routing Audit Aggregation

Gate: `--only routing_audit` (the routing log holds a row since the last aggregation). Run the routing-audit aggregation. Its canonical tool is `python3 .claude/tools/aggregate_routing_audit.py`, which owns the rotation and writes `logs/routing_audit_stats.json`. Record coverage and missing inputs. A failed aggregation is non-blocking but must be reported, not counted as completed.

## Phase 3.6: Orchestration Metrics

Gate: `--only orchestration_metrics`. Close out dispatch ratings first (`/orchestration_metrics` §Incremental rating, *Phase close*). Then apply its Stop-gate: `--ignore-run` every benchmark, probe or calibration run of this session, because the `--archive` run archives the rest and an archived run can no longer be ignored. Run `/orchestration_metrics` (its `--archive` form) and save its output to `.claude/scratch/session_end/<sid>/orchestration_metrics.txt`. A generated native/sidecar report is completed; a checked `No Workflow runs found` with no attributable sidecar rows is skipped; synthetic-only rows remain a completed scoped report; collector refusal, missing transcripts, or errors are blocked. Session close never guesses from memory.

Keep model provenance, cost units and currencies separate. Do not change ladder pins at session close.

## Phase 3.7: Hot-set review

Gate: `--only hot_set_review`; a true value lists each over-baseline budget in its `basis.rows`. Before the review run `git status --short -- <those rows' files>`: a file dirty then and absent from the Phase 0 digest's `files_modified_counts` is peer-dirty, and this session's own edits never count. Run the session-audit command with `--hot-set .claude/scratch/session_end/<sid>/predicate_hot_set_review.json`, rule its `held` ids and failed checks per Step 1.5 under Phase 1's disposition rule, then re-measure with `python3 .claude/tools/load_census.py --budgets`. Set a tripwire's baseline in `tools/load_census.project.json` `budgets` (caps stay in `BUDGETS`, `tools/load_census.py`) to its new measurement only when all three hold:

1. a returned review: a blocked or partial one sets no baseline;
2. every FIX on that budget's files is dispositioned (applied, or rejected with a reason), and every addition since its `baselineCommit` holds a FIX disposition or a `kept:` line;
3. no file the budget measures was peer-dirty when Phase 3.7 began.

A `basis.error` of `not-configured` means the project records no tripwire baseline. Report it in the close and skip the review. Once the project records a baseline in `tools/load_census.project.json`, conditions 1-3 govern it. An `oversized_body` row's re-base writes the body's measured bytes into the project file's `known_oversized_no_selector` under the same conditions. For that row, condition 2 reads `baselineCommit` as the commit that last recorded those bytes, or the whole body when none has. Otherwise every baseline stays and the predicate stays true for the next close. A peer-dirty measured file defers that budget's re-base to the next close with none, and its FIXes stage only this session's hunks. The close report shows each budget's delta, each `kept:` line and each deferred re-base with its reason. A blocked review never blocks the close.

## Phase 4: Sync Subsystems

Gate: `--only sync_subsystems`; the coding layer's subsystem-registry sync command reads the same tool in its Step 0. Invoke that command where the project ships it. Record errors and proposals; registry drift is non-blocking.

## Phase 5: Regression Gate

Gate: `--only regression_gate`. Run the project's regression gate once per drive at close (`change_control` §Gate cadence names it and owns that cadence); a project whose cadence names no gate records `[—]`. Red → fix and verify before commit. Capture actual pass/fail counts and the precommit checklist. Meta-only work skips this code gate; harness changes instead require the harness gate at Phase 7a. Do not run the application merely to validate harness prose or Python.

## Phase 5.4: Roadmap Atlas

Gate: `--only roadmap_atlas`. Run the project's roadmap-atlas generator where it ships one: first its `--render` form, then its `--json` form redirected to `logs/roadmap_atlas_<sid8>.json`; `--render` prints no JSON. Keep its output for Phase 5.5 and report Health findings on touched roadmaps. A project that ships no generator, or a failed render, records the phase blocked with that reason; it never blocks the close, and Phase 5.5 then reads the roadmap directly. Its vault files do not belong in the git commit.

## Phase 5.5: Roadmap Drift

Gate: `--only update_roadmap`. Use the exact plan(s) this session executed, identified in Phase 0. Do not scan every live plan as though this session executed it. No executed roadmap plan → record the scoped skip.

For each executed plan, read its `**Roadmap:**` path and Part identifier. Match that Part against Phase 5.4 JSON, or read the roadmap directly if atlas generation failed. Reconcile shipped scope before proposing a state transition. Parse errors are reported, never silent skips.

Complete Part → run the roadmap-update command, then archive the completed plan under `.claude/plans/archive/`. Incomplete Part → retain it. Abandoned/superseded plan → archive it. Age is never completion evidence.

## Phase 5.6: Harness Prune

Gate: `--only harness_prune`. Invoke the project's harness-prune command where it ships one. Require its `.claude/scratch/harness_prune_<sid8>.md` artifact with `### Plans`, `### Worktrees` and `### Scratch`. Record `harness_prune` completed only when all three sub-surfaces finish; a worktree-prune or scratch failure records blocked with the same artifact.

## Phase 6: Worklog Sweep

Gate: `--only worklog`. Its `basis.completion` admits the completion-sweep against touched items; `basis.add` admits the add-sweep over recorded deferral signals. Run only the admitted halves of `/worklog sweep`; a false half skips and a null or unavailable half runs. Retain each half's input and result in the receipt. Vault edits remain outside the git commit.

## Phase 7: Commit and Push

### 7a. Fresh evidence before commit

Use the regression gate's checklist for code work; for meta-only work, assemble the same applicable audit, scope, ownership and verification items without inventing code-test results.

Run `python3 .claude/tools/session_end_check.py --session <id> --artifacts --stage precommit`. This excludes the future commit and reindex phases. Fix missing or stale evidence before committing.

Commit the session's categories locally. With a land runner (`/commit_push` step 6), go to 7b: the runner proves
every session's commits, so no session waits on that close. Otherwise, when another session is open in this checkout
(`ListAgents`) and the owner's words did not ask for this push, skip 7b's push: prove this session's commits with `--for`
(`rules/harness_tooling.md` §Every guard has a re-runnable proof) and leave the push, with its close over every
session's commits, to the last session out, and record Phase 7 `skipped`; the close check accepts that skip only
while the unpushed range holds another session's commits. A close over a peer's moving commits can run for hours
and outlast this session's prompt cache. Otherwise start `python3 .claude/scripts/harness_tests.py --close`
in the background and retain its result for 7b. `rules/harness_tooling.md` owns close coverage.

### 7b. Commit and push

Invoke `/commit_push`. `/session_end` is the push authority for the active branch, `main` included.

Closeout spends effort on this session's work and on reds no live session owns; a peer's dirty file is never a reason to diagnose its work. Another session's red (its `FIRST RED` commit carries another `Claude-Session` trailer, or its proof is a peer's uncommitted file) goes by whether that work is still in progress, read with `git status --short` on the proof and the files it tests:
- a peer holds them dirty: its owner is mid-work and its own close must clear the red. Give it one report line (proof, commit, session), no diagnosis or fix; this session's commits stay local until a green push carries them.
- they are clean: the work is abandoned, so the red is this session's to fix before push, like its own (`CLAUDE.core.md` §Core Principles), naming the author in the commit.

## Phase 8: Refresh Semantic Search

Gate: `--only reindex_search`. Invoke `/reindex_search` after the last commit. An unavailable plugin records skipped; an error, such as a concurrent session's lock, records blocked. Neither blocks the close.

## Completion receipts and final check

After each phase, preserve its result artifact and record the exact checked inputs:

`python3 .claude/tools/session_end_check.py --session <id> --record <stem> --status completed --input <scope-file> --input <checked-file> --evidence <result-file> --depends <upstream-stem> --reason "<observed result>"`

Repeat `--input`, `--evidence` and `--depends` as needed. Inputs include the phase's command and every mutable file whose change would alter its conclusion, not just a prose list of filenames. Use stable extracted evidence for an append-only archive rather than hashing unrelated peer additions. Upstream phases actually consumed are dependencies: audit consumes digest; autolearn consumes digest/audit; self-evaluate consumes those results; roadmap drift consumes atlas; commit consumes applicable precommit results. Re-record downstream phases after a relevant input changes.

A stale receipt is handled by its delta, which `--resume` names. Refresh it with `python3 .claude/tools/session_end_check.py --session <id> --refresh <stem> --reason "<what the delta changed, or that it changed nothing>"`, and re-run the phase in full only when the delta changes its conclusion.

`skipped` requires checked inputs and the actual conditional predicate; for a gated phase that is its predicate file: a false predicate skips, a null predicate runs the phase, and a phase that ran records any in-body skip with its reason. `blocked` requires the specific failure. The checker verifies freshness only for declared files, so check declaration completeness against the phase's actual read/write scope before accepting its receipt.

Run the checker with `--artifacts --stage final` and report every blocked, failed, skipped or stale phase.
