---
disable-model-invocation: true
allowed-tools: Bash(git pull:*), Bash(git fetch:*), Bash(git status:*), Bash(git stash:*), Bash(git diff:*), Bash(git log:*), Bash(git branch:*)
description: Pull the current branch fully up to date, guarding dirty work with a stash
---

## Scope

This command is the inverse of `/clean_push`. It brings the current branch **fully
synchronized** with its remote counterpart — fetching, pulling, and restoring any
stashed work — without losing uncommitted changes.

PROJECT-CONFIG: a layer or project with paired repos, builds, or post-pull
integrity checks inserts those steps between steps 2 and 3 (e.g. the godot layer
adds submodule update + build + reference-integrity here).

## Context

- Current branch: !`git branch --show-current`
- Current git status: !`git status`
- Recent remote commits: !`git log --oneline origin/main -5`

## Your task

1. **Guard dirty work**: Run `git status`.
   - If there are uncommitted changes, **stash them** (`git stash push -m "clean_pull auto-stash"`).
   - Report what was stashed so the user knows.
   - If clean, proceed directly.

2. **Pull**:
   - First capture pre-pull HEAD: `PRE_PULL_HEAD=$(git rev-parse HEAD)` — the summary diffs against it.
   - Then `git pull` on the current branch.
   - If the pull fails due to divergence, report the error and stop — do NOT force-pull or rebase without user approval.

3. **Restore stash** (if applicable): If changes were stashed in step 1, run `git stash pop`.
   - If the pop has conflicts, report them and stop — let the user resolve manually.

4. **Final verification**: Run `git status` to confirm everything is clean and up to date.

5. **Summary**: Print a concise summary:
   - Branch name
   - Commits pulled (count or "already up to date")
   - Stash status (restored / conflicts / nothing stashed)

You have the capability to call multiple tools in a single response. When steps are independent, batch them in parallel where possible. Do not use any other tools or do anything else. Do not send any other text or messages besides these tool calls.
