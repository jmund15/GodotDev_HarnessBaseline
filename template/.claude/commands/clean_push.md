---
allowed-tools: Bash(git add:*), Bash(git status:*), Bash(git commit:*), Bash(git push:*), Bash(git diff:*), Bash(git log:*), Bash(cd *)
---

## Scope

This command ensures the current branch is **completely clean** — all changes committed, submodules synced, and everything pushed to origin. Unlike `/commit_push` (session-scoped), this sweeps up ALL dirty files regardless of when they were modified.

## Context

- Current git status: !`git status`
- Current git diff (staged and unstaged changes): !`git diff HEAD`
- Current branch: !`git branch --show-current`
- Recent commits: !`git log --oneline -10`

## Your task

1. **Jmodot submodule first**: Check if Jmodot has uncommitted changes (`cd Jmodot && git status`).
   - Follow the [Jmodot Submodule Procedure](agents/jmodot_submodule_procedure.md) for branch checkout, commit, and push.
   - If dirty: commit changes. Use categorical commits if changes span multiple concerns.
   - If Jmodot is ahead of origin: push it.
2. **{{PROJECT_NAME}}**: Group ALL remaining dirty files into categorical commits by logical concern (e.g., feat, fix, refactor, chore, data, docs).
   - If Jmodot was committed/pushed in step 1, include `git add Jmodot` to update the submodule pointer.
   - Each commit should be independently revertable.
3. **Push**: Push all commits to the current branch on origin.
4. **Verify**: Run `git status` for both {{PROJECT_NAME}} AND Jmodot to confirm both are clean and up to date with origin.
5. **Baseline drift gate**: if `.claude/baseline.lock.json` exists:
   - **Modified tracked files** — intersect the files committed in steps 1–2 with `python3 .claude/tools/baseline_sync.py paths`. On overlap, report the hits and classify each change per `/sync_baseline` *Classification Rules*: universal → propose `/sync_baseline push`; project-specific → propose `/sync_baseline fork <file>` (or accept the standing local diff).
   - **New `.claude/` artifacts** — if the commits added files under `.claude/`, run `python3 .claude/tools/baseline_sync.py candidates`. For each candidate, judge once: universal-shaped (new hook/command/skill/rule with no project content) → propose upstreaming via `/sync_baseline` (copy into the baseline `template/`, regen manifest, then `track`); project-specific → `python3 .claude/tools/baseline_sync.py ignore <file>` so it never re-fires.
   - Tracked-file changes and new universal artifacts must never fork the shared baseline silently.
6. You have the capability to call multiple tools in a single response. When commits are independent, batch the staging and committing in parallel where possible. Other than the drift gate above, do not use any other tools or do anything else; besides the drift-gate report (when it fires), do not send any other text or messages besides these tool calls.
