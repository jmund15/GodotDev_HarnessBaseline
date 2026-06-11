---
disable-model-invocation: true
allowed-tools: Bash(gh pr view:*), Bash(gh pr diff:*), Bash(gh pr list:*), Bash(git log:*), Bash(git diff:*), Bash(git -C Jmodot diff:*), Glob, Grep, Read, Workflow
description: "Thorough code review of a single PR or unstaged changes"
---

Review a single PR (or unstaged changes) for missed errors, noncompliance, bad design, TDD compliance, and overall code quality.

## Arguments
- `$ARGUMENTS` — Parsed as: `[PR_NUMBER] [ASPECT_FLAGS...]`
  - **PR number** (optional): Integer. If provided, review that PR's diff. If omitted, review unstaged changes on current branch.
  - **Aspect flags** (optional): One or more of `code`, `tests`, `errors`, `types`, `data`, `pool`, `transcript`, `full`. Default: `full`.
  - Examples:
    - `/review-pr` — Review unstaged changes, all aspects
    - `/review-pr 5` — Review PR #5, all aspects
    - `/review-pr 5 tests errors` — Review PR #5, only test analyzer and error hunter
    - `/review-pr code` — Review unstaged changes, only code reviewer
  - **Parsing rule:** First argument is a PR number if it's a positive integer. All remaining arguments are aspect flags.

## Phase 1: Gather Context

### 1a. Determine scope
- **PR number given** → `gh pr diff <N> --patch` for the diff, `gh pr view <N> --json headRefName,title,body,additions,deletions,changedFiles` for metadata
- **No PR number** → `git diff` for unstaged changes on current branch (pre-commit review mode)

### 1b. Read all changed files at HEAD (full context, not just diff hunks)
For each file in the diff, read the full file content. Agents need surrounding context to judge design quality.

**Scope cap (>20 changed files):** Follow the [Shared Scoping Rules](agents/review_agents.md#shared-scoping-rules). Ask the user before proceeding.

**Jmodot submodule changes:** If the diff shows a Jmodot submodule pointer change (single line changing the commit hash), fetch the actual Jmodot diff so review agents can see what changed:
```bash
# Extract old and new commit hashes from the PR diff
git -C Jmodot diff <old-commit>..<new-commit> --stat   # summary
git -C Jmodot diff <old-commit>..<new-commit>           # full diff
```
Include this Jmodot diff in the CONTEXT block alongside the {{PROJECT_NAME}} diff. Without this, agents only see a single hash-change line and miss all submodule code changes.

### 1c. Classify the PR

Classify using the [PR Classification](agents/pr_classification.md) tables (Domain + Type).

**Risk Level:**
| Level | Criteria |
|-------|----------|
| LOW | Meta-only, or <50 lines changed |
| MEDIUM | New code with tests, or Data-only changes |
| HIGH | Refactors touching existing systems, >200 lines, no tests, or cross-domain changes |

### 1d. Load project context
1. Read the [Architecture Philosophy Skill](/.claude/skills/architecture_philosophy/SKILL.md) — this is the primary compliance reference
2. Read the [Testing Skill](/.claude/skills/testing/SKILL.md) — for TDD compliance checks
3. Search auto-memory with single-keyword searches for each domain touched:
   - Logic domain: search "Godot", "test"
   - Data domain: search "UID", "tres"
   - Gameplay domain: search "HSM", "pool"
   - Refactors: search "refactor"

### 1e. Find transcript summaries (PR mode only)
1. Get commit date range: `git log main..<branch> --format="%ai" | sort` → earliest and latest
2. List transcript summaries: all `.summary.json` files in `logs/transcript_backups/`
3. Match by filename timestamp (format: `YYYYMMDD_HHMMSS`) within commit date range (+-1 hour buffer)
4. From matched summaries, extract:
   - User messages with `"correction"` signal → list of `content_preview` strings
   - User messages with `"instruction"` signal → original requirements
   - `metadata.total_messages` / `metadata.total_tool_calls` → session complexity
   - Count distinct session ID prefixes in matched filenames → compaction count
5. If no matches found, skip silently. Note "No transcript data" in report.

---

## Phase 2: Launch Review Sub-Agents

Parse aspect flags from `$ARGUMENTS`. Default is `full` (all agents).

### 2a. Load agent templates (MANDATORY)

Read the canonical agent template file and inject its contents into subagent prompts:
```
Read: /.claude/commands/agents/review_agents.md
```
This file contains all agent definitions, spawn rules, scoring rubric, and fix classification. **Do NOT use inline/hardcoded agent templates — the canonical file is the single source of truth.**

### 2b. Assemble shared context

**CONTEXT block** — assemble a string containing:
- The full diff (from `gh pr diff`)
- Full file contents of all changed files (read in Phase 1b)
- auto-memory gotchas for touched domains (from Phase 1d)
- Architecture Philosophy rules summary (key rules, not full file — keep context efficient)
- Transcript corrections (from Phase 1e, if found)
- The Finding Schema from `orchestrator_action_protocol.md` (so agents know the output format)

**Checklist injection** — read and split the code quality checklist into targeted sections:
- Read `/.claude/commands/checklists/code_quality.md`:
  - Extract **Compliance (C) + Design (D) + Semantics (S)** sections → `{{CHECKLIST_CDS}}` (for code-reviewer)
  - Extract **Robustness (R) + Performance (P)** sections → `{{CHECKLIST_RP}}` (for error-hunter)
  - Extract **Intuitiveness (I)** section → `{{CHECKLIST_I}}` (for type-reviewer)
- Read `/.claude/commands/checklists/test_quality.md` → `{{TEST_QUALITY_CHECKLIST}}` (for test-analyzer)

**If either checklist file cannot be read, STOP and report the error — do not proceed.**

### 2c. Select the roster, then dispatch via the review-fanout workflow

**Roster selection** (do this in-context — it depends on the diff):
- **ASPECT FILTER:** include only agents whose aspect tag matches the requested flags. `full` = all agents.
- **CONDITIONAL AGENTS:** data-integrity only for `.tres`/`.tscn` changes; pool-lifecycle only for pool/spell-lifecycle changes; transcript-auditor only if transcript summaries were found.
- **Available agents:** code-reviewer, test-analyzer, error-hunter, type-reviewer, data-integrity, pool-lifecycle, transcript-auditor.

For each selected agent, build its prompt from the `review_agents.md` template, substituting `{{CONTEXT}}` (the block from 2b), `{{PR_NUM}}`, `{{BRANCH}}`, `{{CHECKLIST_CDS}}`, `{{CHECKLIST_RP}}`, `{{CHECKLIST_I}}`, `{{TEST_QUALITY_CHECKLIST}}`, `{{TRANSCRIPT_CORRECTIONS}}`.

Then dispatch them all through the engine — it runs them in parallel, appends the read-only / no-tests / no-LSP single-flight guard to every agent (so no agent can run `/regression_gate` concurrently and wedge the Godot pipe), and consolidates per Step 1 of the action protocol:

```
Workflow({
  scriptPath: ".claude/workflows/review_fanout.js",
  args: { agents: [ { key: "code-reviewer", prompt: "<assembled>", model: "<per template>" }, ... ] }
})
```

It returns `{ findings: [...deduped, sorted...], counts, perAgent }`. The fan-out shape, single-message parallelism, and Step-1 merge/dedup/sort are now deterministic — do not re-spawn agents manually.

### Phase 2 Completion

The workflow already performed **Step 1 (Merge & Deduplicate)** of the [Orchestrator Action Protocol](agents/orchestrator_action_protocol.md) — its `findings` are deduped by `file:line` and sorted critical→tier→category. Continue with the rest of the protocol yourself (these need the live-file/user access the workflow lacks):

1. **Step 1.5 — Verify FIX findings against actual file content** — Read each FIX finding's `file:line`, whitespace-tolerant match of `old`; surface unverifiable ones in the ⚠️ UNVERIFIED section.
2. **Steps 2–4** — present the unified report, add NOTE synthesis, then walk FIX/ASK/PLAN with the user.

---

## Phase 3: Present Report

Follow the **Orchestrator Action Protocol** report format. Present all findings grouped by action tier:

```
╔══════════════════════════════════════════════════════╗
║           PR REVIEW: #<N> — <title>                   ║
╠══════════════════════════════════════════════════════════╣
║ Domain: <domains>  │  Risk: <level>  │  Verdict: <V>  ║
║ Files: <count> (+<add> -<del>)  │  Tests: <new count> ║
║ Session: <compactions> compactions, <corrections> corrections ║
╚══════════════════════════════════════════════════════════╝

## FIX — Auto-applied on confirmation (<count>)
<N>. [<agent>] <description>  [<category>] [CRITICAL]
   → <file>:<line>
   → OLD: `<exact code>`
   → NEW: `<replacement code>`
   → <rationale>

## ASK — Needs your input (<count>)
<N>. [<agent>] <description>  [<category>] [CRITICAL]
   → <file>:<line>
   → Question: <specific question>
   → <rationale>

## PLAN — Deferred to follow-up (<count>)
<N>. [<agent>] <description>  [<category>]
   → Scope: <affected files/areas>
   → <rationale>

## Notes (orchestrator-synthesized, if applicable)
- <cross-agent patterns or observations>

## Transcript Context (if available)
- <N> corrections during implementation session
- Key corrections: <list content_preview strings>
- Session complexity: <messages> messages, <tool_calls> tool calls, <compactions> compactions

## PR Hygiene
- Title: <OK | NEEDS UPDATE — suggest better title>
- Description: <OK | MISSING — draft suggested description>
- Tests included: <YES | NO — flag if Logic Domain changes lack tests>

## Verdict
<APPROVE | APPROVE WITH NOTES | REQUEST CHANGES>
- APPROVE: 0 critical findings, ≤2 total findings
- APPROVE WITH NOTES: 0 critical findings, 3+ findings (all addressable)
- REQUEST CHANGES: 1+ critical findings, or unresolved ASK findings
```

**Note:** `/review-pr` is read-only — it presents findings but does NOT apply fixes. The calling orchestrator (`/review-prs` Phase 2, or the user) handles fix application per the Orchestrator Action Protocol.

If reviewing unstaged changes (no PR number), omit PR-specific sections (PR Hygiene title/description, Verdict) and present as a pre-commit code quality report.

---

## Phase 5: Regression Gate (PR mode only)

If the PR contains code changes (`.cs` files), invoke `/regression_gate` after the review but before issuing the final verdict. If the gate fails, the verdict MUST be `REQUEST CHANGES` regardless of code review findings.

Note: For unstaged changes (pre-commit review mode), the regression gate is the caller's responsibility (e.g., `/commit_push` runs it separately).

---

## Constraints

- **Read-only** — this command never modifies code, switches branches, or pushes changes (except `/regression_gate` which only reads)
- **No GitHub posting** — output is local terminal only
- **Load skills first** — Architecture Philosophy and Testing skills are the compliance reference
- **Search Memory** — domain-specific gotchas inform what to look for
- **Report honestly** — false positives waste the developer's time. When in doubt, don't report.
- **Cite rules** — every finding must reference the specific CLAUDE.md rule, Architecture Philosophy pattern, or Testing convention it violates
- **Full file context** — agents read complete files, not just diff hunks, to judge design quality in context
