---
disable-model-invocation: true
allowed-tools: Bash(git pull:*), Bash(git fetch:*), Bash(git status:*), Bash(git stash:*), Bash(git diff:*), Bash(git log:*), Bash(git branch:*), Bash(git rev-parse:*)
description: Pull the current branch fully up to date, guarding dirty work with a stash
---

## Scope

This command is the inverse of `/clean_push`. It brings the current branch **fully
synchronized** with its remote counterpart — fetching, pulling, and restoring any
stashed work — without losing uncommitted changes.

A layer or project with paired repos, builds, or post-pull integrity checks inserts those
steps between steps 2 and 3 (e.g. the godot layer adds submodule update + build +
reference-integrity here) and adds the tool grants those steps need to `allowed-tools`. Read the
paired repos themselves from `adaptation.json` `paired_repos` at that step — never hardcode a path here.

## Context

- Current branch: !`git branch --show-current`
- Current git status: !`git status`

## Your task

1. **Guard dirty work**: Run `git status`.
   - If there are uncommitted changes and a peer session is live in the checkout (`ListAgents`), stop and report; do not stash the shared tree ([`CLAUDE.core.md`](../CLAUDE.core.md) §Core Principles owns peer-work preservation).
   - If there are uncommitted changes and no peer is live, **stash the dirty tree** with `git stash push -m "clean_pull-<branch>-<timestamp>"`, then read its SHA with `git stash list --format='%H %gs'` (match the tag) and report the tag and SHA. Every worktree shares one stash stack, so the SHA is how step 3 finds this entry.
   - If clean, proceed directly.

2. **Pull**:
   - First run `git rev-parse HEAD` and record the printed SHA in your reply as the pre-pull HEAD; step 4 counts commits from that SHA to HEAD (`git log --oneline <sha>..HEAD`).
   - Then `git pull` on the current branch.
   - If the pull fails due to divergence, report the error and stop — do NOT force-pull or rebase without user approval.

3. **Restore stash** (if applicable): If changes were stashed in step 1, run `git stash apply <sha>` with the SHA from step 1; once it succeeds, drop it with `git stash drop <ref>`, where `<ref>` is the `%gd` field of the row whose tag and SHA both match in `git stash list --format='%gd %H %gs'`.
   - If the apply conflicts, resolve by intent (never take a side blindly); the entry stays in the stack until dropped. Ask the user only on an intent fork.

4. **Summary**: Done when the branch matches its upstream. Print a concise summary that cites the post-pull `git status`, and gives:
   - Branch name
   - Commits pulled (the `<sha>..HEAD` count, or "already up to date")
   - Stash status (restored / conflicts / nothing stashed)

Batch independent steps in parallel. Do only the steps above and report only the step-4 summary. `allowed-tools` pre-approves tools; it does not bound actions inside them (`git stash drop`, `git branch -D`).
