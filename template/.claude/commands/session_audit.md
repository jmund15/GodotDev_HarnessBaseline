---
description: Review session changes, a named diff or the always-loaded guidance
allowed-tools: Bash(python3 .claude/tools/lens.py:*), Bash(python3 .claude/tools/task_record.py:*), Bash(python3 .claude/tools/load_census.py:*), Bash(git show:*), Bash(git diff:*), Bash(git ls-files:*), Bash(git log:*), Bash(git cat-file:*), Bash(python3 .claude/tools/review_ledger.py:*), Bash(python3 .claude/tools/session_audit_inputs.py:*), Bash(python3 .claude/tools/post_check.py:*), Glob, Grep, Read, Write, Workflow
---

Audit all code changes from this session for code smells, sub-optimal design, and substantive improvements. Delegates analysis to the seats of its seat map, running in parallel, then consolidates findings.

**Run on its own, this command is advisory — it does NOT block commits, except Phase 1.5's critical parity findings.** Inside a calling drive or `/session_end` Phase 1, the caller's approval covers every finding: fix it now, worklog it only for a defer reason `worklog_reference` §Do-now gate names, or refuse it with evidence, with no per-finding walkthrough. A finding below the run's bar is listed `below bar: noted` with one offer to apply it (`orchestration` reference `rigor.md`).

## Arguments

`/session_audit [<base>..<head> | --files <path>...] [--plan <path>] [--lens <name>...] [--task <id>] [--ignore-ledger]`
`/session_audit --hot-set <predicate file>`

- A range or a file list is the subject in place of the Session File Identification Procedure: a drive's delegates edit in worktrees and a resumed drive spans sessions. With neither, the subject is the session-owned files.
- `--plan <path>` fills `sa-architecture-sweep`'s `{{SHORT_PLAN}}`. With `--task` and no `--plan`, run `python3 .claude/tools/task_record.py show <id>` and use the first plan path in `scope.files` (`.claude/plans/*.md` or a `short_plan.md`, including absolute spellings). If the record holds no plan, print this usage and refuse before scoping or dispatch. Standalone audits without `--task` retain optional `--plan`; without it the placeholder is the word `none`.
- `--lens <name>...` restricts the roster to the named lenses and still reads the ledger. An unknown lens name prints this usage and dispatches nothing.
- `--task <id>` is passed as `--task <id>` to the §1.5b `record` call and rides in `args.ledger.task` in Phase 3, so a drive's reviews list as one unit under `review_ledger.py rows --units`. A drive passes `--task`; `--plan` overrides the record's plan.
- `--hot-set <predicate file>` (from `/session_end` Phase 3.7) reviews the always-loaded set as a whole, which no hunk review answered. Read [`session_audit_seats.md`](reference/session_audit_seats.md) §Hot-set mode for its subject, roster and placeholders.
- `--ignore-ledger` seats every lens the roster builds and passes `--ignore-ledger` to `plan` (the owner-named audit scope). With both flags, `--lens` picks the seats and `--ignore-ledger` skips coverage.

## Phase 1: Scope & Load

### 1a. Identify the subject files

With a range, `--files` or `--hot-set`, the subject is the list that argument names. Otherwise the subject is the session files the [Session File Identification Procedure](agents/session_file_identification.md) reconciles (its Step 4), never conversation memory, which compaction truncates. Keep two sets: the **code set** (the suffixes in `tools/lens_briefs.py` `PRODUCTION_SUFFIXES`) and the **harness set** (`.claude/**`, `CLAUDE.md`, `MEMORY.md`, committed or not).

**Scoping rule (every subject but `--hot-set`):** A dirty file outside the procedure's session files belongs to another session — **exclude it** and note it. With a range or `--files`, that is the only exclusion, so committed work from a resumed drive's earlier sessions stays in the subject:
```
Excluded from audit (not modified this session): [list of files]
```

Whenever the roster includes `sa-harness-quality`, read [`session_audit_seats.md`](reference/session_audit_seats.md) §Frozen inputs and freeze them before either the empty-subject exit or any ledger check.

If both sets are empty and no harness seat is admitted, report "No changes to audit" and exit. Under `--hot-set`, skip the ledger check below and audit the whole subject. Otherwise run `python3 .claude/tools/review_ledger.py uncovered --files <subject files> --mandates candidate --roster <the roster>` and audit only the files it prints, plus the harness subject retained above (`--ignore-ledger` audits them all); a `--lens` run passes `--mandates <the named lenses' mandates>` in place of `candidate`. When it prints nothing and no harness seat is admitted, report "All session files covered by the review ledger" and exit, so `/session_end` records skipped-with-basis. A printed pair's optional third field marks how it is uncovered: `depth-unchecked` when only panel rows cover it and no roster seat can judge their depth, and `after:<blob>` when a row covered the file at that earlier blob. An `after:<blob>` pair is a later round (`orchestration` §2 *Later rounds*), sized from the change between that blob (`git cat-file -p <blob>`) and the current file, and `orchestration` reference `later_rounds.md` decides when a rewritten core design takes the full panel again. `/instruction_audit` stays the per-file deep audit for cross-reference rot and structure, suggested per file when that lens returns a structural ASK.

Batch the complete audit when full context exceeds the bound, per `review_agents.md` *Batching*.

### 1b. Assemble the seats' file list

Each seat's brief carries its must-read list: the files the ledger left uncovered for it, written by `prepare` (§Ledger plan), by path, with each `.tres` beside its Resource class (`review_agents.md` *Context fit*). A kept seat reviews every file in its list, including pairs another of its mandates already covers. `sa-holistic` takes its files from `{{UNIT_LIST}}` instead (§Holistic Trigger).

---

## Phase 1.5: Deleted-Surface Parity Screen (critical parity finding tier)

A `--lens` seam review skips this phase only when a later candidate review will run it. Drive-close and `/session_end` candidate reviews run the applicable parity checks even with a restricted roster.

**Run this BEFORE the audit seats.** Findings here surface at the TOP of the report and are tagged critical parity finding (above FIX/ASK/PLAN). Before commit, each is fixed or the user explicitly approves "ship anyway". Inside a caller, an unfixed critical parity finding halts the caller.

### 1.5a. Stub-marker scan

Read [`session_audit_parity.md`](reference/session_audit_parity.md) §Stub-marker scan for the lines to scan, the markers and the blocking examples.

A hit is a critical parity finding only when its surrounding code shows unfinished wiring in shipped code; otherwise report it as a note, not in the critical count. Surface the file, line number and surrounding context.

### 1.5b. Deleted-surface parity diff

The ledger's `parity` mandate covers this deleted-surface check, not a behavioural baseline. Run the [Retired-code procedure](checklists/parity.md) over each session-changed file whose directory lost a file in the session diff. Any retired surface item it leaves unaccounted for is a critical parity finding. After the check, record it inline: `python3 .claude/tools/review_ledger.py record --stage candidate --subject session:<sid> --inline session_audit --mandate parity --outcome <findings|clean> --files <the checked files> [--task <id>]`.

### 1.5c. Gameplay-domain regression re-triage

When session-changed files touch `Wizard/`, `UI/`, `VFX/` or `Prototype/` and the parity check found ANY drops, re-triage the subject with `oracle = runtime` or `owner` and write that to the task record (`task_record.py triage`): the project's objective playtest pre-screen then decides the probe or the owner's playtest.

### 1.5d. Report shape

If 1.5a/b find issues, report them in the shape of [`session_audit_parity.md`](reference/session_audit_parity.md) §Report shape.

If clean, log `Deleted-surface parity: no critical parity finding` and proceed to Phase 2. Report `Behavioural baseline: <proved|unverified|not applicable>` separately, with receipt paths and comparison verdict when proved; `drive_execution.md` §Slices owns its trigger and required before/after receipts.

---

## Phase 2: Launch Audit Sub-Agents

The parent assembles prompts and shared CONTEXT.

**Missing-lane recovery:** salvage the engine spill/transcript first. Redispatch a missing lane only when salvage names a transient cause, through orchestration's legal route with its original template/model/effort. Under armed `/overnight`, this branch needs no second owner prompt. Save planned/returned IDs and terminal results to `.claude/scratch/session_audit_<sid8>_recovery.json`; denial, terminal unavailability, or the second same failure is blocked coverage, never clean.

### Seat Map

`execution_depth.md` §Candidate width and §Mandate admission select the lenses. `lens_briefs.py` (§Ledger plan) materializes their templates; never `Read` [`session_audit_agents.md`](agents/session_audit_agents.md) whole.

**Seat map** (`orchestration` §2 *Sizing the width*): one seat per admitted lens, and each fired conditional gets its own seat. Merge the lenses one reader can judge in one pass when `orchestration` §2's size or room rule says so; a merged seat's key joins its lens keys with `+`.

### Architecture-Sweep Trigger (conditional 4th agent)

Add `sa-architecture-sweep` to the fan-out when `execution_depth.md` §Mandate admission's `sa-architecture-sweep` row admits it. Read [`session_audit_seats.md`](reference/session_audit_seats.md) §Architecture-Sweep Trigger to fill its `{{DESIGN_LIST}}`.

### Harness-Quality Trigger (conditional 5th agent)

Add `sa-harness-quality` when `execution_depth.md` §Mandate admission admits it. Use §1a's frozen packet, and read [`session_audit_seats.md`](reference/session_audit_seats.md) §Harness-Quality Trigger to fill `{{DOCTRINE_COMMITS}}` and `{{HARNESS_HUNKS}}` and to append `inputs.mustRead` to the seat's must-read list.

### Holistic Trigger (conditional 6th agent)

Add `sa-holistic` when `execution_depth.md` §Mandate admission admits it; `/session_end` Phase 1 runs it with `--lens sa-holistic`. Read [`session_audit_seats.md`](reference/session_audit_seats.md) §Holistic Trigger to fill `{{UNIT_LIST}}` and size the seat.

### Shared Context Block

Write `.claude/scratch/session_audit_<sid8>_context.md` containing:
- The seat's file list from §1b, by path, each marked NEW or MODIFIED
- The [Architecture Philosophy Skill](/.claude/skills/architecture_philosophy/SKILL.md)'s key rules, and the project's testing skill where its stack layer ships one
- auto-memory gotchas for touched domains (single-keyword searches)

Apply `execution_depth.md` §Mandate admission's checklist row before reading shared checklists. For each admitted lens, write only the sections whose checks apply to its changed surfaces to a scratch file, one per placeholder:
- `/.claude/commands/checklists/code_quality.md`: C, D and S → `CHECKLIST_CDS` (`sa-design-semantics`), R and P → `CHECKLIST_RP` (`sa-robustness-performance`), I → `CHECKLIST_I` (`sa-intuitiveness-testability`).
- `/.claude/commands/checklists/test_quality.md`: applicable sections below frontmatter → `TEST_QUALITY_CHECKLIST` (`sa-intuitiveness-testability`).

An unused checklist placeholder's file holds `none`; architecture-, harness- and holistic-only rosters need neither checklist.
`SHORT_PLAN`'s file holds the explicit or task-resolved plan path; include that plan in the architecture seat's must-read list. Without a plan in a standalone audit, it holds `none`. The triggers above name the files for `DESIGN_LIST`, `DOCTRINE_COMMITS`, `HARNESS_HUNKS` and `UNIT_LIST`.

**Ledger plan.** `prepare` composes the ledger plan, the harness freeze, each seat's must-read list, the fold snapshot and the engine args. Write `<run dir>/spec.json`:

```json
{
  "mode": "session-audit",
  "prefix": "sa",
  "topic": "<audit subject>",
  "context": ".claude/scratch/session_audit_<sid8>_context.md",
  "seats": [{"key": "<seat key>", "effort": "<rung>"}],
  "pins": {"panel": "<row>"},
  "efforts": {"panel": "<rung>"},
  "fold": {"tier": "<fold tier>", "model": "<row>", "effort": "<rung>"},
  "subjectFiles": ["<subject files>"],
  "task": "<task id>",
  "sessionAudit": {"sessionBase": "<session base>", "baseRef": "<base>", "files": ["<subject files>"], "session": "<sid>", "plan": "<resolved plan path>", "widen": false, "committedOnly": []}
}
```

```bash
python3 .claude/tools/lens_briefs.py prepare --spec <run dir>/spec.json --out <run dir>
```

```
Workflow({scriptPath: ".claude/workflows/review_fanout.js", args: {argsFile: "<run dir>/args.json"}})
```

`prepare` briefs only the mandates the ledger returns (add `"ignoreLedger": true` inside `ledger` for an owner-named scope). Each seat's must-read list is the diff of its §1b files (`git diff <base>..HEAD` plus the uncommitted `git diff`) and each new file whole; `sa-holistic`'s is its units' diffs. Write one list file per seat and name it in the spec's `seatFiles` (`agents/review_agents.md` *Seat coverage contract*); a placeholder the checklists do not fill (`TRANSCRIPT_CORRECTIONS`) rides in `subs` as `<NAME>=<file>`. Each seat takes its lenses' `seat_tier`; a written tier cannot override it. For §1a's admitted harness seat, `sessionAudit` runs `session_audit_inputs.py freeze` and fills that seat's must-read list from its mandate views; `inputs.mustRead` is forwarded even for an empty file subject, since earlier coverage narrows what it reads, not whether it runs. The frozen `premiseCommits`, `head` and `blobs` paths ride unchanged into `args.ledger`; missing forwarding stops premise recording and is reported, never inferred from current HEAD or replaced with an inline substitute audit row. Never hand-build `<run dir>/roster.json` or `<run dir>/args.json`; `prepare` writes both. Consume the result per [protocol Step 1.5](agents/orchestrator_action_protocol.md), each subject's fold per [Step 4 *Fold subject*](agents/orchestrator_action_protocol.md#fold-subject). `prepare` leaves the code set (`*.cs`, `*.tres`) out of a native `args.fold` and prints those paths; their findings are the parent's: it applies them itself or through one executor job that keeps RED→GREEN (`orchestrator_action_protocol.md` Step 4).

---

## Phase 3: Consolidate & Report

The workflow already performed **Step 1** (lossless source ingestion and sorted/grouped views) of the [Orchestrator Action Protocol](agents/orchestrator_action_protocol.md). Consume the result per **Step 1.5** (the subject is a blob subject over the audit's files in the main checkout). Then run **Steps 2–4** (report, NOTE synthesis, FIX/ASK/PLAN execution under §Execute Actions) yourself.

### Report Format

```
╔══════════════════════════════════════════════════════╗
║              SESSION AUDIT - [DATE]                   ║
╠══════════════════════════════════════════════════════╣
║ Files Analyzed: X (.cs) + Y (.tres)                  ║
║ Findings: FIX:X  ASK:X  PLAN:X                      ║
║ Categories: bug:X  rule:X  improvement:X             ║
╚══════════════════════════════════════════════════════╝
```

Then present findings grouped by action tier (FIX → ASK → PLAN) per the protocol's Step 2. After the tiers, list each resolved PLAN applied from `options[0]` without an owner answer that carries an `options[1]`, one line as `<id> applied <options[0] short> — alternative: <options[1]>`.

### Verdict — two axes, never merged

Issue **two** verdicts: **correctness** (findings tagged `bug` or `rule` — silent failures, regressions, violated project rules) and **design** (findings tagged `improvement` — structure, seams, naming, taste). Never average them into one rating: strong design plus one data-loss bug is `REVISE` on correctness and `APPROVE` on design, never "pretty good".

Before `APPROVE` on correctness, run `python3 .claude/tools/task_record.py check --rerun <task_id>` on the session's active record: a STALE or MISSING row, or one whose evidence items check UNVERIFIED or UNCHECKED, is a decision whose premise is unverified and holds the axis at `REVISE` until it is re-run.

Each axis's verdict is set by its **worst surviving finding** that counts at the run's rigor (`orchestration` reference `rigor.md`); a finding below the bar is listed `below bar: noted` and moves no verdict — not the average, not the count:

- **APPROVE** — no surviving finding on this axis.
- **APPROVE WITH NOTES** — worst surviving finding is non-critical.
- **REVISE** — worst surviving finding is `critical: true`, a critical parity finding from Phase 1.5, or an open PLAN (`question` set) of category `bug` or `rule`. Address before commit.

**Harness sessions close with one context-cost line** (harness set non-empty): run `python3 .claude/tools/load_census.py --budgets` and report each budget's measured value against its baseline, plus any oversized body it flags. Append any new per-call hook emission from the `sa-harness-quality` runtime observation.

### Execute Actions

Follow the protocol's Step 4, which owns the mechanics. Run on its own, present FIX/ASK/PLAN and act on the user's answers; the caller's approval rule is in the header note above.

---

## Constraints

- **Read-only until authorized.** Modify files only under §Execute Actions' authority.
- **Substantive only.** Apply `orchestration` §8's finding filter.
- **Seat the audit through the `review-fanout` workflow.** Do not perform it inline or re-spawn agents manually.
- **Report honestly.** The reporting filter is [orchestrator_action_protocol.md](agents/orchestrator_action_protocol.md); the evidence floor is `review_agents.md` §Evidence & Strength.
