---
description: Classify worktrees and recommend which to remove
---

# /worktree_prune — classify and recommend, never remove

A stale worktree carries a **correctness** cost on top of disk: `.claude/worktrees/` holds whole extra checkouts of this repo, and a recursive `grep -r`/`find` sweeps them (CLAUDE.core.md §9).

## Argument

`/worktree_prune` — classify all. `/worktree_prune <name>` — classify one.

## Procedure

### 1. Enumerate, scoped to THIS checkout

`git worktree list --porcelain`, then keep only entries whose path is under this repo's own `.claude/worktrees/`. Sibling checkouts share the same repo, so an unfiltered list returns other checkouts' worktrees — removing one of those destroys another session's working tree.

Then list the directories under `.claude/worktrees/` that the porcelain output does not name. One whose `.git` file points at a missing admin directory is an **orphan**: git pruned its record, so `git -C <path>` cannot read it and no axis below applies. Bucket it `orphan`. Its removal is `rm -rf .claude/worktrees/<name>` alone, after the owner picks it by name (§4); the delete guard admits it only when git already holds every file's content, and its deny names the first file git lacks.

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

Bucket into: `merged` (branch is in main, nothing uncommitted) · `wip:N` (N tracked uncommitted edits) · `unmerged:N` (N commits not in main) · `detached` · `locked` · `orphan`.

A `*--twin` worktree is an agent twin, which a test runner builds in while an editor holds its source: harness-generated and regenerable, its uncommitted state a copy of its source's, so its `wip:N` is never unique work. Recommend it removable once no run holds it (the twin's checkout lock, which this command does not read), and remove its Jmodot worktree first.

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

The report is done when the table prints. With no worktree under `.claude/worktrees/`, print `No worktrees under .claude/worktrees/.` and stop; when every worktree is `locked` or `wip`, print the table and state that nothing is recommendable.

### 4. The safety rule

**The bucket is advice, not permission.** `wip:N` is N tracked uncommitted edits. Show the diff and get a decision first: removing a clean worktree is recoverable from its branch, but uncommitted work is gone. Never pass `--force`. Never remove a `locked` worktree.

`git worktree remove` refuses any worktree carrying a submodule; `orchestration` reference `worktrees.md` owns the removal recipe for those and the delete guard's rules.

Removal happens only after the owner picks which ones through one `AskUserQuestion` whose question names each worktree, with a remove option; the delete guard reads that pick and no typed reply. Armed `/overnight` is the exception: `commands/overnight.md` §Step 2 decides it. This command's own output is not the confirmation.

## Don'ts

- Don't run `git worktree prune` as a substitute. It only clears administrative records for directories already deleted; it classifies nothing and is not what a stale-but-present worktree needs.
- Don't recommend a worktree whose branch has unpushed commits without saying so in the Note column.
