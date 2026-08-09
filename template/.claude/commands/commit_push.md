---
description: Commit and push only this session's changes; leave unrelated dirty files untouched.
allowed-tools: Bash(git add:*), Bash(git status:*), Bash(git commit:*), Bash(git push:*), Bash(git diff:*), Bash(git log:*), Bash(cd *)
---

## Scope

This command commits and pushes **only changes made during the current session**. If you see dirty files that you did NOT modify in this session, **leave them alone** — they belong to a different workflow.

## Arguments

`$ARGUMENTS` — optional. The only recognised flag is `--check-baseline` (see step 7). No arguments is
the normal case. An unrecognised argument is reported and otherwise ignored — never treat it as a
path or a commit message.

## Context

- Current git status: !`git status`
- Current git diff (staged and unstaged changes): !`git diff HEAD`
- Current branch: !`git branch --show-current`
- Recent commits: !`git log --oneline -10`

## Your task

Based on the above changes:

1. **Identify session files.** Follow the [Session File Identification Procedure](agents/session_file_identification.md) to determine which files belong to this session. Files not identified by the procedure are pre-existing dirty — **skip them**.
2. Group session changes into **categorical commits** by logical concern (e.g., feat, fix, refactor, chore, data, docs). Each commit should be independently revertable.
3. **Jmodot submodule**: If the Jmodot submodule has changes you made this session:
   - `cd` into `Jmodot/` and handle it FIRST (Jmodot must be pushed before {{PROJECT_NAME}} can reference its commit).
   - Follow the [Jmodot Submodule Procedure](agents/jmodot_submodule_procedure.md) for branch checkout, commit, and push.
   - Then back in {{PROJECT_NAME}}, `git add Jmodot` to update the submodule pointer and include it in the appropriate commit.
4. For each category: stage only the relevant files, then commit with an appropriate message.
5. After all commits, push to the current branch on origin.
6. Run `git status` to confirm session changes are committed. Pre-existing dirty files may still appear — that is expected.
7. **Baseline drift gate — OPT-IN.** Runs only when the invocation passed `--check-baseline`. Without the flag, skip it entirely and say nothing about the baseline: a commit touching tracked files is not a reason to run it anyway.
   - When flagged and `.claude/baseline.lock.json` exists, run the drift check over the committed session files per [`/sync_baseline`](sync_baseline.md) — it owns the mechanism, the classification rules, and the push/fork/ignore decision.
   - Default-off because `/sync_baseline` is normally invoked on its own cadence; re-running a classification pass on every commit re-bills judgment the user already owns. **`/sync_baseline` is therefore the sole enforcement point** — an unflagged commit can land a tracked-file edit or a new universal artifact without classifying it, which is the accepted cost of the default.
8. You have the capability to call multiple tools in a single response. When commits are independent, batch the staging and committing in parallel where possible. Other than the drift gate above, do not use any other tools or do anything else; besides the drift-gate report (when it fires), do not send any other text or messages besides these tool calls.
