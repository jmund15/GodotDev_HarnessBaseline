---
disable-model-invocation: true
allowed-tools: Bash(gh pr view:*), Bash(gh pr diff:*), Bash(gh pr edit:*), Bash(gh pr merge:*), Bash(gh pr list:*), Bash(gh label:*), Bash(git stash:*), Bash(git checkout:*), Bash(git pull:*), Bash(git rebase:*), Bash(git push:*), Bash(git log:*), Bash(git diff:*), Bash(git branch:*), Bash(dotnet build:*), Bash(gdunit4:*), Glob, Grep, Read, Edit, Task
description: "Batch review, test, and merge multiple PRs"
---

Batch orchestrator for reviewing, testing, and merging multiple PRs. Delegates code analysis to `/review-pr` and merge lifecycle to `/merge-pr`.

## Arguments
- `$ARGUMENTS` — Optional: PR numbers to review (e.g., "5 7 8"). Defaults to ALL open PRs.

## Workflow Overview

```
Phase 1: PARALLEL REVIEW + CONFLICT DETECTION  (delegate to /review-pr + overlap analysis)
Phase 2: FIX CYCLE                              (present findings, apply fixes per PR)
Phase 3: SEQUENTIAL MERGE                       (delegate to /merge-pr per PR, in order)
Phase 4: CLEANUP & CHANGELOG                    (final tests, changelog, branch cleanup)
```

---

## Phase 1 — Parallel Review + Conflict Detection

### 1a. Discover PRs
```bash
gh pr list --state open --json number,title,headRefName,body,additions,deletions,changedFiles,createdAt
```
If `$ARGUMENTS` is provided, filter to only those PR numbers. Otherwise review all.

### 1b. Launch parallel agents

**Concurrency limit: 15 agents max per batch.** Each review agent spawns 4-7 sub-agents internally (depending on conditional agents), so N review agents = N×(4-7) total sub-agents. To prevent resource exhaustion:
- Calculate total agent count: `N_review_agents × 6 (avg sub-agents) + 1 (conflict detection)`
- If total exceeds **15**, split review agents into sequential batches of `floor(15 / 6) = 2` PRs each
- Launch each batch as a single message (parallel within the batch), wait for all to return, then launch the next batch
- The conflict-detection agent always runs in the **first** batch since it has no sub-agents

Within each batch, spawn agents **in parallel** using the Task tool in a **single message** (one tool call per agent).

**Agent waiting strategy:** Launch all agents in a batch **without** `run_in_background` in a single message. They execute in parallel and all results return together when the slowest agent finishes — no polling or manual checking needed. Do NOT use `run_in_background: true` followed by `TaskOutput` polling.

**N review agents** (one per PR, batched per concurrency limit) — each executes the `/review-pr` procedure:

> You are reviewing PR #<N> ("<title>", branch: `<headRefName>`, +<additions> -<deletions>, <changedFiles> files, created <createdAt>).
>
> Read `.claude/commands/review_pr.md` for the complete review procedure, then execute it step by step for this PR using `full` aspect scope. Skip the `gh pr view` call in Phase 1a — use the metadata provided above.
>
> **CRITICAL — Sub-agent delegation is MANDATORY:**
> Phase 2 of `review_pr.md` requires you to spawn 4-7 specialized sub-agents (code-reviewer, test-analyzer, error-hunter, type-reviewer, pool-lifecycle, data-integrity, transcript-auditor) using the Task tool with the prompt templates from `.claude/commands/agents/review_agents.md`. You MUST spawn these as separate Task sub-agents with the specified model (opus, sonnet, or haiku). Do NOT perform the review inline — the multi-agent architecture ensures each review lens gets dedicated depth and attention.
>
> After all sub-agents return, merge their findings and produce the final structured report including verdict.

**1 conflict-detection agent** — runs alongside reviews:
> Analyze file overlap across these PRs: <list of PR numbers>.
> For each PR, run `gh pr diff <N> --name-only` to get changed file lists.
> Build an overlap matrix. For each pair of PRs, flag:
> - **Direct conflicts:** Same file modified in multiple PRs
> - **Semantic conflicts:** Different files that reference each other (e.g., registry + statsheet)
> - **Submodule conflicts:** Multiple PRs changing the Jmodot submodule pointer. Each {{PROJECT_NAME}} branch `claude/<name>` has a paired Jmodot branch `jmodot/<name>`.
>
> Return a structured conflict report and a proposed merge order using these rules:
> 1. **Jmodot-first rule:** PRs with Jmodot submodule changes merge first (Memory: `Git_Submodule_PR_Merge_Strategy`)
> 2. **Conflict-aware ordering:** When PRs overlap on files, the earlier/simpler PR merges first; later PRs rebase after
> 3. **Chronological** for non-conflicting PRs

**Scope:** Conflict detection only checks the specified PRs against each other (not other open PRs outside the review set).

### 1c. Present consolidated results
After all agents complete, present:

**Review Dashboard:**
```
╔══════════════════════════════════════════════════════════╗
║                   PR REVIEW DASHBOARD                    ║
╠══════════════════════════════════════════════════════════╣
║ PR #5  │ feat: Split project overview  │ LOW  │ ✅ APPROVE  ║
║ PR #6  │ feat: Launch jitter           │ MED  │ ⚠️  CHANGES ║
║ PR #7  │ feat: Cast recoil             │ MED  │ ⚠️  CHANGES ║
║ PR #8  │ refactor: Spell scenes        │ HIGH │ ✅ APPROVE  ║
╚══════════════════════════════════════════════════════════╝
Issues: X Critical, Y Important
```

**Conflict Report:**
```
╔═══════════════════════════════════════════════╗
║            CONFLICT DETECTION                  ║
╠═══════════════════════════════════════════════╣
║ PR #6 ↔ PR #7                                 ║
║   ⚠️ base_spell_statsheet.tres  (both modify) ║
║   ⚠️ PushinPotionRegistry.cs    (both modify) ║
║   → Recommend: merge #6 first, rebase #7      ║
╠═══════════════════════════════════════════════╣
║ PR #5 ↔ PR #8                                 ║
║   ✅ No overlapping files                      ║
╚═══════════════════════════════════════════════╝
```

**Proposed Merge Order:**
Present the conflict-detection agent's proposed merge order to user for approval before proceeding.

---

## Phase 2 — Fix Cycle

**Precondition:** The orchestrator starts on `main` with a clean working tree. If there are uncommitted changes on `main`, the user must commit or stash them before running this command. Phase 2 does NOT stash — only `/merge-pr` manages stash for its own lifecycle.

Follow the **Orchestrator Action Protocol** defined in [`/.claude/commands/agents/orchestrator_action_protocol.md`](agents/orchestrator_action_protocol.md).

Present each PR's detailed findings **sequentially** (in proposed merge order). After each:

1. Show the full `/review-pr` report for this PR
2. Ask user: **apply suggested fixes**, **skip all fixes**, or **defer PR** (remove from merge queue)
3. If applying fixes:
   - Checkout PR branch: `git checkout <branch>` then `git submodule update --init --recursive`
   - **FIX findings:** Apply mechanically using the OLD/NEW code snippets from the review report. User can exclude specific fixes before confirmation. Summarize each applied change.
   - **ASK findings:** Present each ASK finding with its question and proposed options. User provides direction → apply per user guidance. If user says "skip" → noted as "deferred finding" in final summary.
   - **PLAN findings:** Present as summary. User can create an Obsidian TODO, enter plan mode, or dismiss.
   - Build to verify: `dotnet build`
   - Commit all fixes for this PR: `git commit -am "fix(review): apply review fixes for PR #<N>"`
   - Push to PR branch: `git push`
   - Return to main: `git checkout main` then `git submodule update --init --recursive`
4. If skipping all fixes: move to next PR (fixes remain as notes in the review report)
5. If deferring: remove from merge queue, note in final summary

**Responsibility:** `/review-pr` PROPOSES fixes (read-only analysis with FIX/ASK/PLAN classification). This orchestrator (Phase 2) APPLIES fixes per the Orchestrator Action Protocol.

---

### Early Exit: All PRs Deferred

If all PRs were deferred in Phase 2 (merge queue is empty), skip Phases 3 and 4. Present a summary:

```
╔══════════════════════════════════════════════════════════╗
║                  PR REVIEW COMPLETE                      ║
╠══════════════════════════════════════════════════════════╣
║ All PRs deferred — no merges performed.                  ║
║ Deferred: PR #<N> (<reason>), PR #<M> (<reason>), ...   ║
╚══════════════════════════════════════════════════════════╝
```

---

## Phase 3 — Sequential Merge

For each PR in the approved merge order (excluding deferred PRs):

**Delegate to `/merge-pr`:**
> You are merging PR #<N>. Read `.claude/commands/merge_pr.md` for the complete merge procedure, then execute it step by step for this PR.

`/merge-pr` handles: branch checkout, build, tests, user testing handoff (if Gameplay), PR hygiene (labels + title/description), merge with user confirmation, and return to main.

**Between merges (opportunistic rebase):** If the NEXT PR in queue has file overlaps with the just-merged PR (from the Phase 1 conflict report), preemptively rebase it onto the updated main:
```bash
git checkout <next-branch>
git submodule update --init --recursive
git rebase main
git push --force-with-lease
git checkout main
git submodule update --init --recursive
```

> **Note:** This rebase is opportunistic — it prevents predictable conflicts before `/merge-pr` starts. `/merge-pr` Step 1 also checks mergeability and offers to rebase independently. The redundancy is intentional: this preemptive pass catches overlap-driven conflicts early, while `/merge-pr`'s check is a safety net for any remaining issues (e.g., conflicts introduced by fix commits in Phase 2).

---

## Phase 4 — Cleanup & Changelog

### 4a. Final verification
```bash
git checkout main
git pull
git submodule update --init --recursive
```
Then invoke `/regression_gate` to run all test suites. See [`regression_gate.md`](regression_gate.md) for the canonical procedure.

### 4b. Generate changelog entry
Prepend a dated entry to `CHANGELOG.md` at the repo root. Create the file if it doesn't exist.

**Insertion point:** Insert the new entry at the top of the file, after any existing title/header line (e.g., `# Changelog`). Newest entries appear first, per [Keep a Changelog](https://keepachangelog.com/) convention. Use `Edit` tool to prepend.

**Format:**
```markdown
## [Unreleased] - YYYY-MM-DD

### Added
- <feat PRs: one bullet per PR> (#<number>)

### Changed
- <refactor/chore PRs: one bullet per PR> (#<number>)

### Fixed
- <fix PRs: one bullet per PR> (#<number>)
```

- Only include sections that have entries
- Derive category from conventional commit prefix in PR title (feat->Added, fix->Fixed, refactor/chore->Changed)
- Do NOT include deferred/skipped PRs

### 4c. Clean up local branches
```bash
git branch --merged main | grep -v main | xargs git branch -d 2>/dev/null || true
```

### 4d. Present final summary
```
╔══════════════════════════════════════════════════════════╗
║                  PR REVIEW COMPLETE                      ║
╠══════════════════════════════════════════════════════════╣
║ PR #5  │ Merged ✅  │ meta     │ 0 issues found          ║
║ PR #6  │ Merged ✅  │ logic    │ 2 fixes applied          ║
║ PR #7  │ Merged ✅  │ logic    │ 1 fix applied            ║
║ PR #8  │ Deferred ⏸ │ logic   │ Needs design discussion  ║
╠══════════════════════════════════════════════════════════╣
║ Changelog: CHANGELOG.md updated                          ║
║ Labels: 3 PRs labeled                                    ║
║ Tests: ALL PASSING ✅                                     ║
╚══════════════════════════════════════════════════════════╝
```

Ask user if they want to commit the changelog update.

---

## Constraints

- **Not a dynamic `Workflow` (intentional):** Phase 1 is *nested* fan-out — each per-PR agent runs `/review-pr`, which itself spawns 4–7 sub-agents. A `Workflow` `agent()` guard reaches only the top-level fanned agent, not those sub-sub-agents, so the single-flight protections (no `/regression_gate`, no csharp-ls LSP) would silently lapse one level down. Also, per-PR grouping fights the `review_fanout` engine's global `file:line` dedup — cross-PR same-line edits are *conflicts to surface*, not duplicates to collapse. Kept Claude-orchestrated like `/plan_check`; only the batch math + conflict-detection are deterministic, and those are cheap to keep here.
- **Delegate reviews to `/review-pr`** — this command orchestrates, does not define review criteria
- **Delegate merges to `/merge-pr`** — this command does not inline merge logic
- **Never force-push** to PR branches (except `--force-with-lease` for post-rebase pushes)
- **Never merge without user confirmation** — `/merge-pr` handles this
- **One branch at a time for testing** — user can only test one branch in Godot at a time
- **Respect submodule merge order** — Jmodot branches merge first (Memory: `Git_Submodule_PR_Merge_Strategy`)
- **Don't run game during user test** — wait for user to test independently (CLAUDE.md: "Invisibility Workflow")
- **Changelog at repo root** — `CHANGELOG.md` follows Keep a Changelog format
- **Labels are additive** — never remove existing labels
- **Clean working tree required** — this command assumes `main` has no uncommitted changes. Phase 2 does not stash.
