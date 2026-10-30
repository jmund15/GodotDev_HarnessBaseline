---
description: Commit and push this session's changes; /clean_push covers all
allowed-tools: Bash(git add:*), Bash(git status:*), Bash(git commit:*), Bash(git push:*), Bash(git diff:*), Bash(git log:*), Bash(cd *), Bash(python3:*)
---

## Scope

This command commits and pushes **only changes made during the current session**. If you see dirty files that you did NOT modify in this session, **leave them alone** — they belong to a different workflow.

## Arguments

`$ARGUMENTS` — optional. The only recognised flag is `--check-baseline` (see step 5). No arguments is
the normal case. Any other argument: report it and stop before the first Git command — never treat it
as a path or a commit message.

## Context

- Current git status: !`git status`
- Current git diff (staged and unstaged changes): !`git diff HEAD`
- Current branch: !`git branch --show-current`
- Recent commits: !`git log --oneline -10`

## Your task

Based on the above changes:

1. **Identify session files.** Follow the [Session File Identification Procedure](agents/session_file_identification.md) to determine which files belong to this session.
2. Group session changes into **categorical commits** by logical concern (e.g., feat, fix, refactor, chore, data, docs). Each commit should be independently revertable.
3. **Paired repos first** (read `adaptation.json` `paired_repos` for the list; skip if it's empty): if a listed submodule or paired repo has changes you made this session, commit and push it FIRST per the rule file `paired_repo_rules` names for it, then stage its pointer update here in the appropriate commit.
4. For each category: stage only the relevant files, then commit with an appropriate message.
5. **Baseline drift gate — OPT-IN, before any push.** Runs only when the invocation passed `--check-baseline`. Without the flag, skip it entirely and say nothing about the baseline: a commit touching tracked files is not a reason to run it anyway.
   - When flagged and `.claude/baseline.lock.json` exists, run `python3 .claude/tools/baseline_sync.py check --strict` over the committed session files per [`/sync_baseline`](sync_baseline.md) — it owns the mechanism, the classification rules, and the push/fork/ignore decision. A finding stops the command before step 6.
   - Default-off because `/sync_baseline` runs on its own cadence; the commit-guard lock-row requirement (CLAUDE.md §Harness Baseline) still catches an unflagged commit's new files — only an edit to an already-tracked file goes unjudged.
6. After all commits, push to the current branch on origin, or to the land branch under the first bullet's condition. When the push's review-owed line names commits, run `/session_audit --lens sa-harness-quality` over the named range; the line lists only commits the harness-quality admission already admitted.
   - **Land runner** (`adaptation.json` `land_prefix` set, and the current branch contains `origin/<default>`): `git push --recurse-submodules=check origin HEAD:<land_prefix><session id's first 8 chars>`; the check refuses a gitlink naming a submodule commit no remote holds, which step 3 pushes first. Run no local close: the runner runs the close, and the regression gate for non-harness paths, then fast-forwards the default branch. When `adaptation.json` `land_watch` is set, start that command in the background with the land branch and pushed sha: it reports the land's outcome. A red leaves the default branch where it was; the report names the land branch and the watch result.
   - **Otherwise:** start `python3 .claude/scripts/harness_tests.py --close` in the background, or reuse the close run `/session_end` already started for this HEAD. Read its result before push; `rules/harness_tooling.md` owns coverage. Push the commit the close proved, with the `git push` line a green close prints; commits landed after it go out with the next close.
7. Run `git status` (and each paired repo's) to confirm the changes are committed and pushed. Pre-existing dirty files may still appear — that is expected.
8. You have the capability to call multiple tools in a single response. When commits are independent, batch the staging and committing in parallel where possible. Other than the drift gate, step 6's harness proof run, land watch and admitted doctrine review above, do not use any other tools or do anything else; besides the drift-gate report (when it fires), do not send any other text or messages besides these tool calls.
