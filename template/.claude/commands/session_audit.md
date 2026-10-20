---
description: Review session changes, a named diff or the always-loaded guidance
allowed-tools: Bash(python3 .claude/tools/lens.py:*), Bash(python3 .claude/tools/task_record.py:*), Bash(python3 .claude/tools/load_census.py:*), Bash(git show:*), Bash(git diff:*), Bash(git ls-files:*), Bash(git log:*), Bash(git cat-file:*), Bash(python3 .claude/tools/review_ledger.py:*), Bash(python3 .claude/tools/session_audit_inputs.py:*), Bash(python3 .claude/tools/post_check.py:*), Glob, Grep, Read, Write, Workflow
---

Audit all code changes from this session for code smells, sub-optimal design, and substantive improvements. Delegates analysis to the seats of its seat map, running in parallel, then consolidates findings.

**Run on its own, this command is advisory — it does NOT block commits, except Phase 1.5's MERGE-BLOCKER findings.** Inside a calling drive or `/session_end` Phase 1, the caller's approval covers every finding: fix it now, worklog it only for a defer reason `worklog_reference` §Do-now gate names, or refuse it with evidence, with no per-finding walkthrough.

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

With a range, `--files` or `--hot-set`, the subject is the list that argument names. Otherwise the subject is the session files the [Session File Identification Procedure](agents/session_file_identification.md) reconciles (its Step 4), never conversation memory, which compaction truncates. Keep two sets: the **code set** (`*.cs`, `*.tres`) and the **harness set** (`.claude/**`, `CLAUDE.md`, `MEMORY.md`, committed or not).

**Scoping rule (every subject but `--hot-set`):** A dirty file outside the procedure's session files belongs to another session — **exclude it** and note it. With a range or `--files`, that is the only exclusion, so committed work from a resumed drive's earlier sessions stays in the subject:
```
Excluded from audit (not modified this session): [list of files]
```

Whenever the roster includes `sa-harness-quality`, read [`session_audit_seats.md`](reference/session_audit_seats.md) §Frozen inputs and freeze them before either the empty-subject exit or any ledger check.

If both sets are empty and no harness seat is admitted, report "No changes to audit" and exit. Under `--hot-set`, skip the ledger check below and audit the whole subject. Otherwise run `python3 .claude/tools/review_ledger.py uncovered --files <subject files> --mandates candidate --roster <the roster>` and audit only the files it prints, plus the harness subject retained above (`--ignore-ledger` audits them all); a `--lens` run passes `--mandates <the named lenses' mandates>` in place of `candidate`. When it prints nothing and no harness seat is admitted, report "All session files covered by the review ledger" and exit, so `/session_end` records skipped-with-basis. A printed pair's optional third field marks how it is uncovered: `depth-unchecked` when only panel rows cover it and no roster seat can judge their depth, and `after:<blob>` when a row covered the file at that earlier blob. An `after:<blob>` pair is a later round (`orchestration` §2 *Later rounds*), sized from the change between that blob (`git cat-file -p <blob>`) and the current file, and `orchestration` reference `later_rounds.md` decides when a rewritten core design takes the full panel again. `/instruction_audit` stays the per-file deep audit for cross-reference rot and structure, suggested per file when that lens returns a structural ASK.

Batch the complete audit when full context exceeds the bound, per `review_agents.md` *Batching*.

### 1b. Assemble the seats' file list

Each seat's CONTEXT lists the `uncoveredFiles` `plan` returned for it (§Ledger plan), by path, with each `.tres` beside its Resource class; no subject file is pasted, and the seat reads what it judges (`review_agents.md` *Context fit*). A kept seat reviews every file in its list, including pairs another of its mandates already covers. `sa-holistic` takes its files from `{{UNIT_LIST}}` instead (§Holistic Trigger).

---

## Phase 1.5: Deleted-Surface Parity Screen (MERGE-BLOCKER tier)

A `--lens` seam review skips this phase only when a later candidate review will run it. Drive-close and `/session_end` candidate reviews run the applicable parity checks even with a restricted roster.

**Run this BEFORE the audit seats.** Findings here surface at the TOP of the report and are tagged MERGE-BLOCKER (above FIX/ASK/PLAN). Before commit, each is fixed or the user explicitly approves "ship anyway". Inside a caller, an unfixed MERGE-BLOCKER halts the caller.

### 1.5a. Stub-marker scan

Read [`session_audit_parity.md`](reference/session_audit_parity.md) §Stub-marker scan for the lines to scan, the markers and the blocking examples.

A hit is a MERGE-BLOCKER only when its surrounding code shows unfinished wiring in shipped code; otherwise report it as a note, not in the blocker count. Surface the file, line number and surrounding context.

### 1.5b. Deleted-surface parity diff

The ledger's `parity` mandate covers this deleted-surface check, not a behavioural baseline. Run the [Retired-code procedure](checklists/parity.md) over each session-changed file whose directory lost a file in the session diff. Any retired surface item it leaves unaccounted for is a MERGE-BLOCKER finding. After the check, record it inline: `python3 .claude/tools/review_ledger.py record --stage candidate --subject session:<sid> --inline session_audit --mandate parity --outcome <findings|clean> --files <the checked files> [--task <id>]`.

### 1.5c. Gameplay-domain regression re-triage

When session-changed files touch `Wizard/`, `UI/`, `VFX/` or `Prototype/` and the parity check found ANY drops, re-triage the subject with `oracle = runtime` or `owner` and write that to the task record (`task_record.py triage`): the project's objective playtest pre-screen then decides the probe or the owner's playtest.

### 1.5d. Report shape

If 1.5a/b find issues, report them in the shape of [`session_audit_parity.md`](reference/session_audit_parity.md) §Report shape.

If clean, log `Deleted-surface parity: CLEAN` and proceed to Phase 2. Report `Behavioural baseline: <proved|unverified|not applicable>` separately, with receipt paths and comparison verdict when proved; `drive_spine.md` §Slices owns its trigger and required before/after receipts.

---

## Phase 2: Launch Audit Sub-Agents

The parent assembles prompts and shared CONTEXT.

**Missing-lane recovery:** salvage the engine spill/transcript first. Redispatch a missing lane only when salvage names a transient cause, through orchestration's legal route with its original template/model/effort. Under armed `/overnight`, this branch needs no second owner prompt. Save planned/returned IDs and terminal results to `.claude/scratch/session_audit_<sid8>_recovery.json`; denial, terminal unavailability, or the second same failure is blocked coverage, never clean.

### Agent Templates & Spawn Rules

Fetch only the templates `execution_depth.md` §Candidate width and §Mandate admission select: `python3 .claude/tools/lens.py get --shared <admitted lens keys>`. Never `Read` [`session_audit_agents.md`](agents/session_audit_agents.md) whole.

**Seat map** (`orchestration` §2 *Sizing the width*): one seat per admitted lens, and each fired conditional gets its own seat. Merge the lenses one reader can judge in one pass when `orchestration` §2's size or room rule says so. A merged seat's prompt carries each of its lenses' templates.

### Architecture-Sweep Trigger (conditional 4th agent)

Add `sa-architecture-sweep` to the fan-out when `execution_depth.md` §Mandate admission's `sa-architecture-sweep` row admits it. Read [`session_audit_seats.md`](reference/session_audit_seats.md) §Architecture-Sweep Trigger to fill its `{{DESIGN_LIST}}`.

### Harness-Quality Trigger (conditional 5th agent)

Add `sa-harness-quality` when `execution_depth.md` §Mandate admission admits it. Use §1a's frozen packet, and read [`session_audit_seats.md`](reference/session_audit_seats.md) §Harness-Quality Trigger to fill `{{DOCTRINE_COMMITS}}` and `{{HARNESS_HUNKS}}` and to append `inputs.mustRead` to the seat's must-read list.

### Holistic Trigger (conditional 6th agent)

Add `sa-holistic` when `execution_depth.md` §Mandate admission admits it; `/session_end` Phase 1 runs it with `--lens sa-holistic`. Read [`session_audit_seats.md`](reference/session_audit_seats.md) §Holistic Trigger to fill `{{UNIT_LIST}}` and size the seat.

### Shared Context Block

Assemble a `CONTEXT` string containing:
- The seat's file list from §1b, by path, each marked NEW or MODIFIED
- The [Architecture Philosophy Skill](/.claude/skills/architecture_philosophy/SKILL.md)'s key rules, and the project's testing skill where its stack layer ships one
- auto-memory gotchas for touched domains (single-keyword searches)

Apply `execution_depth.md` §Mandate admission's checklist row before reading shared checklists. For each admitted lens, load only sections whose checks apply to its changed surfaces:
- `/.claude/commands/checklists/code_quality.md`: C, D and S → `{{CHECKLIST_CDS}}` (`sa-design-semantics`), R and P → `{{CHECKLIST_RP}}` (`sa-robustness-performance`), I → `{{CHECKLIST_I}}` (`sa-intuitiveness-testability`).
- `/.claude/commands/checklists/test_quality.md`: applicable sections below frontmatter → `{{TEST_QUALITY_CHECKLIST}}` (`sa-intuitiveness-testability`).

Fill an unused checklist placeholder with `none`; architecture-, harness- and holistic-only rosters need neither checklist. Stop and report the error only when a required section is unreadable.

Fill `{{SHORT_PLAN}}` with the explicit or task-resolved plan path; include that plan in the architecture seat's must-read list. Without a plan in a standalone audit, fill it with `none`.

**Ledger plan.** Write the roster the seat map builds to `.claude/scratch/session_audit_<sid8>_roster.json`, one entry per seat: `{key, mandates, tier, effort}`, where `mandates` is each lens's `mandates:` line in the registry and the pins are resolved per `orchestration` §5. Then run `python3 .claude/tools/review_ledger.py plan --stage candidate --subject session:<sid> --roster <roster.json> --files <subject files> [--ignore-ledger]` and dispatch only the seats it returns; report each `dropped` mandate with its `coveredBy` row. For §1a's admitted harness seat, plan that seat separately with a one-seat roster, `--ignore-ledger` and `inputs.files`, then merge it into the returned roster once. Forward `inputs.mustRead` even for an empty file subject; earlier coverage narrows what it reads, not whether it runs.

Each seat's must-read list is the diff of its §1b files (`git diff <base>..HEAD` plus the uncommitted `git diff`, written to scratch) and each new file whole; `sa-holistic`'s is its units' diffs. Write it to a list file and pass it as the seat's `mustRead` (`agents/review_agents.md` *Seat coverage contract*).

Snapshot the subject files as a blob subject ([protocol Step 4 *Fold subject*](agents/orchestrator_action_protocol.md#fold-subject)): root `.`, files `<subject files>`, out `.claude/scratch/session_audit_<sid8>_snapshot.json`. Dispatch through the engine (build each agent's prompt from its `session_audit_agents.md` template + the CONTEXT block above):

```
Workflow({
  scriptPath: ".claude/workflows/review_fanout.js",
  args: { agents: [
    { key: "sa-design-semantics", prompt: "<assembled>", model: "<resolved native id>", effort: "<resolved effort>", agentType: "general-purpose", mustRead: [<must-read entries>] },
    { key: "sa-robustness-performance", prompt: "<assembled>", model: "<resolved native id>", effort: "<resolved effort>", agentType: "general-purpose", mustRead: [<must-read entries>] },
    { key: "sa-intuitiveness-testability", prompt: "<assembled>", model: "<resolved native id>", effort: "<resolved effort>", agentType: "general-purpose", mustRead: [<must-read entries>] },
    // + each conditional seat (`sa-architecture-sweep`, `sa-harness-quality`, `sa-holistic`) when `execution_depth.md` §Mandate admission admits it:
    { key: "sa-architecture-sweep", prompt: "<assembled>", model: "<resolved native id>", effort: "<resolved effort>", agentType: "general-purpose", mustRead: [<must-read entries>] } ],
    spillDir: ".claude/scratch/session_audit_<sid8>_spill",
    fold: { tier: "<fold tier>", model: "<row>", effort: "<rung>", subjects: [{ root: ".", snapshot: ".claude/scratch/session_audit_<sid8>_snapshot.json", files: [<native-legal harness subject files>] }] },
    ledger: { stage: "candidate", subject: "session:<sid>", roster: ".claude/scratch/session_audit_<sid8>_roster.json", task: "<id>", premiseCommits: "<inputs.premiseCommits>", head: "<inputs.head>", blobs: "<inputs.blobs>" } }
})
```

Consume the result per [protocol Step 1.5](agents/orchestrator_action_protocol.md). For a harness seat, its ledger arguments carry the frozen `premiseCommits`, `head` and `blobs` paths unchanged into `review_ledger.py record`; the path fields must pass the engine's `safePath` validation. Omit those three fields when no harness packet exists. Check the report's ledger rows against the frozen packet; missing forwarding stops premise recording and is reported, never inferred from current HEAD or replaced with an inline substitute audit row. The parent omits production-code subjects from native `args.fold`, then snapshots and dispatches those findings on the serialized sidecar or solo route. That executor runs the project's tests and regression gate; the native engine does not reroute or hand them off automatically (`review_fold.md` code clause).

---

## Phase 3: Consolidate & Report

The workflow already performed **Step 1** (lossless source ingestion and sorted/grouped views) of the [Orchestrator Action Protocol](agents/orchestrator_action_protocol.md). Consume the result per **Step 1.5** (the subject is a blob subject over the audit's files in the main checkout). Then run **Steps 2–4** (report, NOTE synthesis, FIX/ASK/PLAN execution under §Execute Actions) yourself. After recording a harness audit, recheck the outgoing population with `python3 .claude/tools/review_ledger.py owed --mandates harness-quality --doctrine --roster <harness one-seat roster.json>`. A commit added during the audit remains owed; report it and seat the existing lens over new frozen inputs before claiming outgoing premise coverage.

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

Issue **two** verdicts: **correctness** (findings tagged `bug` or `rule` — silent failures, regressions, violated project rules) and **design** (findings tagged `improvement` — structure, seams, naming, taste). Never average them into one rating: strong design plus one data-loss bug is `REVIEW RECOMMENDED` on correctness and `CLEAN` on design, never "pretty good".

Before `CLEAN` on correctness, run `python3 .claude/tools/task_record.py check --rerun <task_id>` on the session's active record: a STALE or MISSING row, or one whose evidence items check UNVERIFIED or UNCHECKED, is a decision whose premise is unverified and holds the axis at `REVIEW RECOMMENDED` until it is re-run.

Each axis's verdict is set by its **worst surviving finding** — not the average, not the count:

- **CLEAN** — no surviving finding on this axis.
- **MINOR POLISH** — worst surviving finding is non-critical.
- **REVIEW RECOMMENDED** — worst surviving finding is `critical: true`, a MERGE-BLOCKER from Phase 1.5, or an open PLAN (`question` set) of category `bug` or `rule`. Address before commit.

**Harness sessions close with one context-cost line** (harness set non-empty): run `python3 .claude/tools/load_census.py --delta <base>`. Append any new per-call hook emission from the `sa-harness-quality` runtime observation.

### Execute Actions

Follow the protocol's Step 4, which owns the mechanics. Run on its own, present FIX/ASK/PLAN and act on the user's answers; the caller's approval rule is in the header note above.

---

## Constraints

- **Read-only until authorized.** Modify files only under §Execute Actions' authority.
- **Substantive only.** Apply `orchestration` §8's finding filter.
- **Seat the audit through the `review-fanout` workflow.** Do not perform it inline or re-spawn agents manually.
- **Report honestly.** The reporting filter is [orchestrator_action_protocol.md](agents/orchestrator_action_protocol.md); the evidence floor is `review_agents.md` §Evidence & Strength.
