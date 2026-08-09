---
description: Audit this session's code changes for smells and design debt via 3 parallel agents.
allowed-tools: Bash(git diff:*), Bash(git ls-files:*), Bash(git log:*), Glob, Grep, Read, Workflow
---

Audit all code changes from this session for code smells, sub-optimal design, and substantive improvements. Delegates analysis to 3 specialized subagents running in parallel, then consolidates findings.

**This command is advisory — it does NOT block commits.** Findings are improvement opportunities, not failures.

## Phase 1: Scope & Load

### 1a. Identify session-modified files

**MANDATORY: Read** [Session File Identification Procedure](agents/session_file_identification.md) **and execute ALL steps.** Do not rely on conversation memory alone — after compaction, it is incomplete. Filter results to `*.cs` and `*.tres` files only.

**Scoping rule:** Only audit files identified by the [Session File Identification Procedure](agents/session_file_identification.md) as session work. Follow the procedure completely — especially Step 2 (Compaction Recovery) after compaction. If git shows changes to files not in Steps 1+2, they belong to other sessions — **exclude them** and note:
```
Excluded from audit (not modified this session): [list of files]
```

If there are no code changes from this session, report "No code changes to audit" and exit. **Exception:** if the session modified `.claude/skills/`, `.claude/commands/`, or `CLAUDE.md`, suggest running `/instruction_audit` on each before exit — harness-doc edits carry cross-reference rot and structural defects this command's `*.cs`/`*.tres` filter does not cover.

**Scope cap (>20 files):** Follow the [Shared Scoping Rules](agents/review_agents.md#shared-scoping-rules). Ask the user before proceeding.

### 1b. Read all session-modified files

Read the FULL content of each changed `.cs` file (not just diffs). For `.tres` files, read them and their associated Resource class.

### 1c. Load project context

1. Read the [Architecture Philosophy Skill](/.claude/skills/architecture_philosophy/SKILL.md)
2. Read the [Testing Skill](/.claude/skills/testing/SKILL.md)
3. Search auto-memory for domain-relevant gotchas (single-keyword searches)

---

## Phase 1.5: Refactor Parity Check (MERGE-BLOCKER tier)

**Run this BEFORE the 3 subagents.** Findings here surface at the TOP of the report and are tagged MERGE-BLOCKER (above FIX/ASK/PLAN). User must explicitly approve "ship anyway" before commit.

Background: PR #58 shipped three silent regressions because the audit toolchain doesn't do behavioral feature-parity checks (only diff-internal quality). See `feedback_refactor_parity_audit.md`.

### 1.5a. Stub-marker scan

Grep all session-changed `.cs` files for these markers in code AND comments:

```
deferred|TODO|FIXME|follow-up|follow up|stub|not yet wired|no-op until|placeholder
```

ANY hit is a MERGE-BLOCKER. Surface the file, line number, and surrounding context. Examples that should fire:
- `// wiring deferred to the wheel-UI integration follow-up`
- `// TODO: hook this up`
- `// stubbed for now`
- `JmoLogger.Info(this, "...stubbed (no provider wired)...")`

False-positive filter: a marker inside a `<remarks>` block describing PAST work that's now done is OK if the surrounding code clearly shows the wiring is complete.

### 1.5b. Retired-code parity diff

For each session-changed file in a directory where another file was DELETED in the session diff (`git diff <base> --diff-filter=D --name-only`), enumerate parity:

1. Read the deleted file (via `git show <base>:<path>`).
2. List its public surface: `[Export]` fields, public methods, lifecycle hooks (`OnEnter`/`OnExit`/`OnProcessFrame`/`_Ready`/etc.), event subscriptions, side effects (Engine state, BB writes, signals emitted).
3. For each item, verify the replacement either:
   - **Reproduces** the behavior (point at the new line that does it), OR
   - **Explicitly notes removal** in a comment, docstring, or PR description.

Any retired surface item NOT accounted for is a MERGE-BLOCKER finding. Examples that would have caught PR #58:
- Old `CraftWheelState.OnEnter` instantiated `BulletTimeController`; new `CraftMenuOpenState.OnEnter` doesn't.
- Old state resolved BB refs in `OnEnter`; new state did it in `OnInit` (silent semantic change).

### 1.5c. Gameplay-domain regression note

If session-changed files touch `Wizard/`, `UI/`, `VFX/`, or `Prototype/` and the parity check found ANY drops, append:
```
GAMEPLAY-DOMAIN regressions cannot be verified by automated tests alone.
Recommend manual playtest before merge: <list specific behaviors to test>
```

### 1.5d. Report shape

If 1.5a/b find issues:
```
╔══════════════════════════════════════════════════════╗
║   MERGE-BLOCKER: REFACTOR PARITY ISSUES FOUND        ║
╠══════════════════════════════════════════════════════╣
║ Stub markers: <count>   Parity drops: <count>        ║
╚══════════════════════════════════════════════════════╝
```
Then list each finding with file:line and "How old code did it" vs "How new code handles it".

If clean, log a single line `Refactor parity: CLEAN` and proceed to Phase 2.

---

## Phase 2: Launch Audit Sub-Agents

The agent fan-out and Step-1 consolidation run deterministically in the `review-fanout` workflow. Below, assemble the agent prompts + the shared CONTEXT, then dispatch them through the engine — it runs them in parallel, appends the read-only / no-tests / no-LSP single-flight guard to each, and merges/dedups/sorts the findings.

### Agent Templates & Spawn Rules

Use the agent templates defined in [`session_audit_agents.md`](agents/session_audit_agents.md). That file contains:
- Agent Spawn Rules (referenced from `review_agents.md`)
- Finding Schema & Reporting Filter (referenced from `orchestrator_action_protocol.md`)
- 3 always-on agent templates: `sa-design-semantics` (opus), `sa-robustness-performance` (opus), `sa-intuitiveness-testability` (sonnet)
- 1 conditional template: `sa-architecture-sweep` (opus) — design-ideality judgment (existing-seam reuse, cleaner/more-modular/data-driven alternatives, dedup-similar-logic-into-one-source)

### Architecture-Sweep Trigger (conditional 4th agent)

Add `sa-architecture-sweep` to the fan-out when the session shipped **design-scale** work — any of: a new subsystem/type family or top-level concept, a multi-file feature design (3+ production files forming one mechanism), a refactor of a 2+ subclass/consumer family, a new cross-system seam, **or any new `[Export]`/authored field added to a pre-existing class** (the export-surface failures — bypassed families, dual-concern knobs, re-authored values — ship in small diffs that "design-scale" alone never catches). (Mirror of the `/plan_check` litmus; when in doubt on a large session, include it.) Pure tuning/data/bug-fix sessions skip it. When triggered, fill its `{{DESIGN_LIST}}` with the session's major designs — name each mechanism and its key files/seams so the agent judges designs, not diffs. Its findings join the same consolidation; its SUPERIOR-ALTERNATIVE findings default to being implemented this session when effort is S/M and the migration closes a live gap (user preference: "move to the superior design"), deferred-with-worklog otherwise.

### Shared Context Block

Assemble a `CONTEXT` string containing:
- Full file contents of all session-modified files (from Phase 1b)
- Architecture Philosophy key rules summary
- auto-memory gotchas for touched domains
- List of which files are NEW vs MODIFIED
- **Prior-coverage note (conditional):** when the session executed against a plan file (`.claude/plans/*.md`) whose Decision record contains a close-out design-review entry, include a note that the Part's diff was four-axis reviewed + plan-conformance-checked pre-gate — `sa-design-semantics` then concentrates fresh findings on the non-Part session surface and its own axes, and treats re-covering the Part's code as expected re-coverage rather than re-litigation (validated 2026-08-08: the note steered the lens to fresh findings — visibility, catch narrowing — instead of re-covering the close-out review).

Additionally, read these shared checklists and inject their contents into agent prompts:
- Read `/.claude/commands/checklists/code_quality.md` → inject as `{{CODE_QUALITY_CHECKLIST}}`
- Read `/.claude/commands/checklists/test_quality.md` → inject as `{{TEST_QUALITY_CHECKLIST}}`

**If either checklist file cannot be read, STOP and report the error — do not proceed.**

Dispatch through the engine (build each agent's prompt from its `session_audit_agents.md` template + the CONTEXT block above):

```
Workflow({
  scriptPath: ".claude/workflows/review_fanout.js",
  args: { agents: [
    { key: "sa-design-semantics", prompt: "<assembled>", model: "opus" },
    { key: "sa-robustness-performance", prompt: "<assembled>", model: "opus" },
    { key: "sa-intuitiveness-testability", prompt: "<assembled>", model: "sonnet" },
    // + when the Architecture-Sweep Trigger fires:
    { key: "sa-architecture-sweep", prompt: "<assembled>", model: "opus" } ] }
})
```

It returns `{ findings: [...deduped, sorted...], counts }`. Do not spawn the agents manually.

---

## Phase 3: Consolidate & Report

The workflow already performed **Step 1** (merge/dedup by `file:line`, sort critical→tier→category) of the [Orchestrator Action Protocol](agents/orchestrator_action_protocol.md). Continue with **Step 1.5** (verify each FIX `old` against the live file) and **Steps 2–4** (report, NOTE synthesis, user-gated FIX/ASK/PLAN walkthrough) yourself.

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

Then present findings grouped by action tier (FIX → ASK → PLAN) per the protocol's Step 2.

### Verdict — two axes, never merged

Issue **two** verdicts: **correctness** (findings tagged `bug` or `rule` — silent failures, regressions, violated project rules) and **design** (findings tagged `improvement` — structure, seams, naming, taste). Never average them into one rating: strong design plus one data-loss bug is `REVIEW RECOMMENDED` on correctness and `CLEAN` on design, never "pretty good".

Each axis's verdict is set by its **worst surviving finding** — not the average, not the count:

- **CLEAN** — no surviving finding on this axis.
- **MINOR POLISH** — worst surviving finding is non-critical. Ship it, improve later.
- **REVIEW RECOMMENDED** — worst surviving finding is `critical: true`, a MERGE-BLOCKER from Phase 1.5, or a PLAN-tier systemic defect. Address before commit.

### Execute Actions

Follow the protocol's Step 4:
- **FIX:** Present all, apply on user confirmation
- **ASK:** Present questions, apply per user direction
- **PLAN:** Present summary, user decides to defer or address

---

## Constraints

- **Read-only by default.** Do NOT modify any files unless user explicitly approves a FIX action.
- **No feature changes.** Audit focuses on internal quality of existing changes, not new functionality.
- **Substantive only.** Skip cosmetic issues (whitespace, comment style) unless they indicate a real problem.
- **Respect TDD.** Any approved fix in Logic Domain must have test coverage.
- **The agent fan-out lives in the `review-fanout` workflow** — it spawns the 3 always-on agents plus the conditional `sa-architecture-sweep` (templates in [`session_audit_agents.md`](agents/session_audit_agents.md)) in parallel and consolidates. Do not perform the audit inline or re-spawn agents manually.
- **Report honestly.** False positives waste time. When in doubt, don't report.
- **Time-bounded.** The full audit (spawn → consolidate) should complete in under 10 minutes.
