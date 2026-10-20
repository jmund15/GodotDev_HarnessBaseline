---
description: Commit and push all dirty files; /commit_push is session-only
allowed-tools: Bash(git add:*), Bash(git status:*), Bash(git commit:*), Bash(git push:*), Bash(git diff:*), Bash(git log:*), Bash(cd *), Bash(python3:*)
---

## Scope

This command ensures the current branch is **completely clean** — all changes committed, submodules synced, and everything pushed to origin. Unlike `/commit_push` (session-scoped), this sweeps up ALL dirty files regardless of when they were modified.

Arguments are those of [`/commit_push`](commit_push.md) §Arguments.

## Context

- Current git status: !`git status`
- Current git diff (staged and unstaged changes): !`git diff HEAD`
- Current branch: !`git branch --show-current`
- Recent commits: !`git log --oneline -10`

## Your task

Follow [`/commit_push`](commit_push.md) steps 2–8 over every dirty or ahead file, with these differences:

- **Step 1 is skipped.** The scope is every dirty file, not this session's.
- **Step 3 trigger.** Read `adaptation.json` `paired_repos` for the list; a listed repo that is dirty or ahead of origin is committed and pushed first per the rule file `adaptation.json` `paired_repo_rules` names for it, whoever changed it.
- **Step 6** pushes through `/commit_push`: to the land branch, or after `harness_tests.py --close` runs in the background; keep that close result.
- **Step 7 done-condition.** This repo and each paired repo are clean and pushed: up to date with origin, or HEAD on the land branch when `/commit_push` step 6 pushed there.
