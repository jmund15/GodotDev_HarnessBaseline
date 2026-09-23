---
description: Classify every worktree under .claude/worktrees/ by size, age, merge state and uncommitted work; report and recommend, never remove without confirmation.
---

# /worktree_prune — classify and recommend, never remove

`orchestration` §7 creates worktrees; nothing prunes them. A stale worktree carries a **correctness** cost on top of disk: `.claude/worktrees/` holds whole extra checkouts of this repo, and a recursive `grep -r`/`find` sweeps them — measured, 261 phantom hits where the gitignore-aware `Grep` returned 0 (CLAUDE.md §Tool Routing).

## Argument

`/worktree_prune` — classify all. `/worktree_prune <name>` — classify one.

## Procedure

### 1. Enumerate, scoped to THIS checkout

`git worktree list --porcelain`, then keep only entries whose path is under this repo's own `.claude/worktrees/`. Sibling checkouts share the same repo, so an unfiltered list returns other checkouts' worktrees — removing one of those destroys another session's working tree.

**No-op gate:** zero matches → print `No worktrees under .claude/worktrees/.` and exit. No table, no recommendations.

### 2. Classify each

Per worktree, gather:

| Axis | How |
|---|---|
| Size | `du -sh <path>` |
| Age | commit date of its HEAD (`git -C <path> log -1 --format=%cr`) |
| Branch | branch name, or `detached` |
| Merge state | `git branch --merged main` membership, or `git -C <path> log --oneline main..HEAD \| wc -l` for the unmerged-commit count |
| Uncommitted | `git -C <path> status --porcelain` — count **tracked** modifications separately from untracked files |
| Locked | `locked` in the porcelain output — a locked worktree is deliberately pinned; never recommend it |

Bucket into: `merged` (branch is in main, nothing uncommitted) · `wip:N` (N tracked uncommitted edits) · `unmerged:N` (N commits not in main) · `detached` · `locked`.

**No-op gate, second form:** every worktree lands in `locked` or `wip` → print the table and state that nothing is recommendable, rather than emitting an empty recommendation list.

### 3. Report

```
## Worktrees under .claude/worktrees/  (<N> found, <total> on disk)

| Worktree | Branch | Age | Size | Bucket | Note |
|---|---|---|---|---|---|
| audio_system | audio-system | 3 weeks | 1.2G | merged | branch is in main |
| blood-proto | prototype/blood-spray-splat | 2 days | 900M | wip:4 | 4 tracked edits |

### Recommended for removal (confirm each)
1. audio_system — merged into main, 3 weeks idle, 1.2G. `git worktree remove .claude/worktrees/audio_system`
```

### 4. The safety rule

**The bucket is advice, not permission.** `wip:N` is N tracked uncommitted edits. Show the diff and get a decision first: removing a clean worktree is recoverable from its branch, but uncommitted work is gone. Never pass `--force`. Never remove a `locked` worktree.

`git worktree remove` refuses any worktree carrying the Jmodot submodule. For those, run `rm -rf .claude/worktrees/<name>` alone, then `git worktree prune`. The delete guard (`is_retired_worktree` in `hooks/_regenerable_clone.py`) decides which worktrees qualify; ignored scratch evidence or local config blocks it until moved or deleted.

Removal happens only after the user names which ones — this command's own output is not the confirmation.

## Don'ts

- Don't run `git worktree prune` as a substitute. It only clears administrative records for directories already deleted; it classifies nothing and is not what a stale-but-present worktree needs.
- Don't recommend a worktree whose branch has unpushed commits without saying so in the Note column.
- Don't remove another checkout's worktree — step 1's scoping is the guard.
