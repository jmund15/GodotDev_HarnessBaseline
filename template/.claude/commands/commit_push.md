---
allowed-tools: Bash(git add:*), Bash(git status:*), Bash(git commit:*), Bash(git push:*), Bash(git diff:*), Bash(git log:*), Bash(cd *)
---

## Scope

This command commits and pushes **only changes made during the current session**. If you see dirty files that you did NOT modify in this session, **leave them alone** — they belong to a different workflow.

## Context

- Current git status: !`git status`
- Current git diff (staged and unstaged changes): !`git diff HEAD`
- Current branch: !`git branch --show-current`
- Recent commits: !`git log --oneline -10`

## Your task

Based on the above changes:

1. **Identify session files.** Follow the [Session File Identification Procedure](agents/session_file_identification.md) to determine which files belong to this session. Files not identified by the procedure are pre-existing dirty — **skip them**.
2. Group session changes into **categorical commits** by logical concern (e.g., feat, fix, refactor, chore, data, docs). Each commit should be independently revertable.
3. **Jmodot submodule**: If the Jmodot submodule has changes you made this session:
   - `cd` into `Jmodot/` and handle it FIRST (Jmodot must be pushed before {{PROJECT_NAME}} can reference its commit).
   - Follow the [Jmodot Submodule Procedure](agents/jmodot_submodule_procedure.md) for branch checkout, commit, and push.
   - Then back in {{PROJECT_NAME}}, `git add Jmodot` to update the submodule pointer and include it in the appropriate commit.
4. For each category: stage only the relevant files, then commit with an appropriate message.
5. After all commits, push to the current branch on origin.
6. Run `git status` to confirm session changes are committed. Pre-existing dirty files may still appear — that is expected.
7. **Baseline drift gate**: if `.claude/baseline.lock.json` exists:
   - **Modified tracked files** — intersect the committed session files with `python3 .claude/tools/baseline_sync.py paths`. On overlap, report the hits and classify each change per `/sync_baseline` *Classification Rules*: universal → propose `/sync_baseline push`; project-specific → propose `/sync_baseline fork <file>` (or accept the standing local diff).
   - **New `.claude/` artifacts** — if the session added files under `.claude/`, run `python3 .claude/tools/baseline_sync.py candidates`. For each candidate, judge once: universal-shaped (new hook/command/skill/rule with no project content) → propose upstreaming via `/sync_baseline` (copy into the baseline `template/`, regen manifest, then `track`); project-specific → `python3 .claude/tools/baseline_sync.py ignore <file>` so it never re-fires.
   - Tracked-file changes and new universal artifacts must never fork the shared baseline silently.
8. You have the capability to call multiple tools in a single response. When commits are independent, batch the staging and committing in parallel where possible. Other than the drift gate above, do not use any other tools or do anything else; besides the drift-gate report (when it fires), do not send any other text or messages besides these tool calls.
