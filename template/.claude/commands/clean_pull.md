---
disable-model-invocation: true
allowed-tools: Bash(git pull:*), Bash(git fetch:*), Bash(git status:*), Bash(git stash:*), Bash(git diff:*), Bash(git log:*), Bash(git branch:*), Bash(git rev-parse:*), Bash(git ls-tree:*), Bash(git ls-files:*), Bash(git submodule:*), Bash(git -C:*), Bash(mv:*)
description: Pull the current branch fully up to date, guarding dirty work with a stash
---

## Scope

This command is the inverse of `/clean_push`. It brings the current branch **fully
synchronized** with its remote counterpart — fetching, pulling, and restoring any
stashed work — without losing uncommitted changes.

A layer or project with builds or post-pull integrity checks inserts those steps after step 2b
and adds the tool grants they need to `allowed-tools`. Paired repos are read from `paired_repos`
in `.claude/skills/project_subsystems/adaptation.json` — never hardcode a path here.

## Context

- Current branch: !`git branch --show-current`
- Current git status: !`git status`

## Your task

1. **Guard dirty work**: Run `git status`.
   - If there are uncommitted changes and a peer session is live in the checkout (`ListAgents`), stop and report; do not stash the shared tree ([`CLAUDE.core.md`](../CLAUDE.core.md) §Core Principles owns peer-work preservation).
   - If there are uncommitted changes and no peer is live, **stash the dirty tree** with `git stash push -m "clean_pull-<branch>-<timestamp>"`, then read its SHA with `git stash list --format='%H %gs'` (match the tag) and report the tag and SHA. Every worktree shares one stash stack, so the SHA is how step 3 finds this entry.
   - If clean, proceed directly.

2. **Pre-pull checks, then pull**:
   - First run `git rev-parse HEAD` and record the printed SHA in your reply as the pre-pull HEAD; step 4 counts commits from that SHA to HEAD (`git log --oneline <sha>..HEAD`).
   - For each path in `paired_repos`, record its gitlink (`git ls-tree HEAD <path>`) and its checkout HEAD (`git -C <path> rev-parse HEAD`). Step 2b compares them.
   - Untracked blockers: run `git fetch`, then list the paths upstream adds with `git diff --name-only --diff-filter=A HEAD @{u}`. Intersect that list with local untracked files from `git ls-files --others --exclude-standard`. Git refuses the pull when a local untracked file has an upstream path. Before renaming any match, check every match for an existing `<path>.bak`: if one exists, stop and report it, since `mv` would overwrite it. Otherwise rename each match to `<path>.bak` with `mv` and report the list. If `@{u}` is unset, report it and stop.
   - Then `git pull` on the current branch.
   - If the pull fails due to divergence, report the error and stop — do NOT force-pull or rebase without user approval.

2b. **Sync paired repos** (after the pull): for each `paired_repos` path, compare its pre-pull checkout HEAD with its pre-pull gitlink.
   - Both equal and `git -C <path> status --porcelain` empty: run `git submodule update --init --recursive -- <path>`.
   - Otherwise leave the checkout alone and report its HEAD, its gitlink and its dirty state. A dirty, ahead or diverged checkout holds work; the repo's `paired_repo_rules` entry in `adaptation.json` names the rule that owns it.

3. **Restore stash** (if applicable): If changes were stashed in step 1, run `git stash apply <sha>` with the SHA from step 1; once it succeeds, drop it with `git stash drop <ref>`, where `<ref>` is the `%gd` field of the row whose tag and SHA both match in `git stash list --format='%gd %H %gs'`.
   - If the apply conflicts, resolve by intent (never take a side blindly); the entry stays in the stack until dropped. Ask the user only on an intent fork.

4. **Summary**: Done when the branch matches its upstream. Print a concise summary that cites the post-pull `git status`, and gives:
   - Branch name
   - Commits pulled (the `<sha>..HEAD` count, or "already up to date")
   - Stash status (restored / conflicts / nothing stashed)
   - Renamed untracked blockers (`.bak` list, or none)
   - Paired repo sync (per repo: synced, or left alone with the reason)

Batch independent steps in parallel. Do only the steps above and report only the step-4 summary. `allowed-tools` pre-approves tools; it does not bound actions inside them (`git stash drop`, `git branch -D`).
