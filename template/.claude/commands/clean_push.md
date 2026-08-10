---
description: Commit and push every dirty file on the branch, paired repos first, until the tree is clean.
allowed-tools: Bash(git add:*), Bash(git status:*), Bash(git commit:*), Bash(git push:*), Bash(git diff:*), Bash(git log:*), Bash(cd *)
---

## Scope

This command ensures the current branch is **completely clean** — all changes committed, submodules synced, and everything pushed to origin. Unlike `/commit_push` (session-scoped), this sweeps up ALL dirty files regardless of when they were modified.

## Arguments

`$ARGUMENTS` — optional. The only recognised flag is `--check-baseline` (see step 5). No arguments is
the normal case. An unrecognised argument is reported and otherwise ignored — never treat it as a
path or a commit message.

## Context

- Current git status: !`git status`
- Current git diff (staged and unstaged changes): !`git diff HEAD`
- Current branch: !`git branch --show-current`
- Recent commits: !`git log --oneline -10`

## Your task

1. **Paired repos first** (PROJECT-CONFIG: skip if the project has none): if a submodule or paired repo is dirty or ahead of origin, commit and push it FIRST per its own procedure.
2. **{{PROJECT_NAME}}**: Group ALL remaining dirty files into categorical commits by logical concern (e.g., feat, fix, refactor, chore, data, docs).
   - If a paired repo was pushed in step 1, stage its pointer update in the appropriate commit.
   - Each commit should be independently revertable.
3. **Push**: Push all commits to the current branch on origin.
4. **Verify**: Run `git status` (and the paired repo's, if any) to confirm clean and up to date with origin.
5. **Baseline drift gate — OPT-IN.** Runs only when the invocation passed `--check-baseline`. Without the flag, skip it entirely and say nothing about the baseline: a commit touching tracked files is not a reason to run it anyway.
   - When flagged and `.claude/baseline.lock.json` exists, run the drift check over the files committed in steps 1–2 per [`/sync_baseline`](sync_baseline.md) — it owns the mechanism, the classification rules, and the push/fork/ignore decision.
   - Default-off because `/sync_baseline` is normally invoked on its own cadence; re-running a classification pass on every push re-bills judgment the user already owns. **`/sync_baseline` is therefore the sole enforcement point** — an unflagged push can land a tracked-file edit or a new universal artifact without classifying it, which is the accepted cost of the default.
6. You have the capability to call multiple tools in a single response. When commits are independent, batch the staging and committing in parallel where possible. Other than the drift gate above, do not use any other tools or do anything else; besides the drift-gate report (when it fires), do not send any other text or messages besides these tool calls.
