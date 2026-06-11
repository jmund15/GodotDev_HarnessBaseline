---
disable-model-invocation: true
allowed-tools: Bash(git pull:*), Bash(git fetch:*), Bash(git status:*), Bash(git stash:*), Bash(git submodule:*), Bash(git diff:*), Bash(git log:*), Bash(git branch:*), Bash(dotnet build:*), Bash(cd *)
description: Pull all repos up to date — main project and Jmodot submodule
---

## Scope

This command is the inverse of `/clean_push`. It ensures the current branch and all submodules are **fully synchronized** with their remote counterparts — fetching, pulling, updating submodules, and verifying the build compiles.

## Context

- Current branch: !`git branch --show-current`
- Current git status: !`git status`
- Current submodule status: !`git submodule status`
- Recent remote commits: !`git log --oneline origin/main -5`

## Your task

1. **Guard dirty work**: Run `git status` for both {{PROJECT_NAME}} and Jmodot.
   - If there are uncommitted changes, **stash them** (`git stash push -m "clean_pull auto-stash"`).
   - Report what was stashed so the user knows.
   - If clean, proceed directly.

2. **Pull {{PROJECT_NAME}}**:
   - **First capture pre-pull HEAD**: `PRE_PULL_HEAD=$(git rev-parse HEAD)`. Save this — step 5 (reference integrity check) needs it to diff what was pulled.
   - Then `git pull` on the current branch.
   - If the pull fails due to divergence, report the error and stop — do NOT force-pull or rebase without user approval.

3. **Update Jmodot submodule**: Run `git submodule update --init --recursive`.
   - **CRITICAL**: A `git pull` updates the submodule *pointer* but does NOT checkout the new files. This step is mandatory — without it, the build will reference stale Jmodot code and fail with missing types.
   - Verify with `git submodule status` — the output should have NO `+` prefix (which indicates the working tree doesn't match the pointer).

4. **Rebuild**: Run `dotnet build` and verify 0 errors.
   - If the build fails, report the errors clearly. Do NOT attempt to fix them automatically.

5. **Post-rebase reference integrity check**: Catches the PR #64 class of regressions — `.tres`/`.tscn` references to renamed/deleted C# classes that pass `dotnet build`, Logic, AND Sanity but only fail Integration at scene-instantiation time. See `.claude/rules/godot_files.md` — Rebase / merge conflict resolution section.
   - Check if any `.cs` file was renamed or deleted in the pulled changes:
     ```bash
     git diff $PRE_PULL_HEAD HEAD --name-status -- '*.cs' | grep -E '^[RD]'
     ```
   - If output is **non-empty**, automatically invoke `/structure_audit --quick=references`. Surface findings here before proceeding to step 6. Time bound: under 2 minutes.
   - If output is **empty**, skip — no class drift risk introduced by this pull. Note "Skipped (no .cs renames/deletes in pull)" in the summary.

6. **Restore stash** (if applicable): If changes were stashed in step 1, run `git stash pop`.
   - If the pop has conflicts, report them and stop — let the user resolve manually.

7. **Final verification**: Run `git status` for both {{PROJECT_NAME}} AND Jmodot to confirm everything is clean and up to date.

8. **Summary**: Print a concise summary:
   - Branch name
   - Commits pulled (count or "already up to date")
   - Jmodot submodule commit (before → after, or "unchanged")
   - Build status (pass/fail)
   - Reference integrity check (skipped / clean / N findings)
   - Stash status (restored / conflicts / nothing stashed)

You have the capability to call multiple tools in a single response. When steps are independent, batch them in parallel where possible. Do not use any other tools or do anything else. Do not send any other text or messages besides these tool calls.
