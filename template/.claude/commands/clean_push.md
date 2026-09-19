---
description: Commit and push every dirty file on the branch, paired repos first, until the tree is clean.
allowed-tools: Bash(git add:*), Bash(git status:*), Bash(git commit:*), Bash(git push:*), Bash(git diff:*), Bash(git log:*), Bash(cd *), Bash(python3:*)
---

## Scope

This command ensures the current branch is **completely clean** — all changes committed, submodules synced, and everything pushed to origin. Unlike `/commit_push` (session-scoped), this sweeps up ALL dirty files regardless of when they were modified.

## Arguments

`$ARGUMENTS` — optional. The only recognised flag is `--check-baseline` (see step 3). No arguments is
the normal case. Any other argument: report it and stop before the first Git command — never treat it
as a path or a commit message.

## Context

- Current git status: !`git status`
- Current git diff (staged and unstaged changes): !`git diff HEAD`
- Current branch: !`git branch --show-current`
- Recent commits: !`git log --oneline -10`

## Your task

1. **Paired repos first** (read `adaptation.json` `paired_repos` for the list; skip if it's empty): if a listed submodule or paired repo is dirty or ahead of origin, commit and push it FIRST per its own procedure.
2. **{{PROJECT_NAME}}**: Group ALL remaining dirty files into categorical commits by logical concern (e.g., feat, fix, refactor, chore, data, docs).
   - If a paired repo was pushed in step 1, stage its pointer update in the appropriate commit.
   - Each commit should be independently revertable.
3. **Baseline drift gate — OPT-IN, before any push.** Runs only when the invocation passed `--check-baseline`. Without the flag, skip it entirely and say nothing about the baseline: a commit touching tracked files is not a reason to run it anyway.
   - When flagged and `.claude/baseline.lock.json` exists, run `python3 .claude/tools/baseline_sync.py check --strict` over the files committed in steps 1–2 per [`/sync_baseline`](sync_baseline.md) — it owns the mechanism, the classification rules, and the push/fork/ignore decision. A finding stops the command before step 4.
   - Default-off because `/sync_baseline` runs on its own cadence; the commit-guard lock-row requirement (CLAUDE.md §Harness Baseline) still catches an unflagged push's new files — only an edit to an already-tracked file goes unjudged.
4. **Push**: Push all commits to the current branch on origin.
5. **Verify**: Run `git status` (and the paired repo's, if any) to confirm clean and up to date with origin.
6. You have the capability to call multiple tools in a single response. When commits are independent, batch the staging and committing in parallel where possible. Other than the drift gate above, do not use any other tools or do anything else; besides the drift-gate report (when it fires), do not send any other text or messages besides these tool calls.
