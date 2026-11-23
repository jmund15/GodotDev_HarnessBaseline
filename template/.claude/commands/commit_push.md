---
description: Commit and push this session's changes; --all sweeps every dirty file
allowed-tools: Bash(git add:*), Bash(git status:*), Bash(git commit:*), Bash(git push:*), Bash(git diff:*), Bash(git log:*), Bash(git merge:*), Bash(cd *), Bash(python3:*)
---

# /commit_push — the one commit-and-push unit

Lands finished work without ending the session: mid-session (before a playtest, after an editor save) or as `/close` step 4. Invoking it is push authority for the active branch, `main` included.

## Arguments

- None: this session's files only. A dirty file this session did not change belongs to other work: leave it.
- `--all`: every dirty or ahead file, a peer's finished work included, under step 1's ownership check.
- Anything else: print `accepts: /commit_push [--all]` and `example: /commit_push --all`, then stop before any git command. An argument is never a path or a commit message.

## Context

- Status: !`git status --short`
- Branch: !`git branch --show-current`
- Recent commits: !`git log --oneline -5`
- Changed files: !`git diff HEAD --stat`

## Steps

1. **Scope.** Default: the session's files per [Session File Identification](agents/session_file_identification.md). `--all`: every file `git status` lists whose author is done: its session is offline in `ListAgents`, or its `/overnight` Close doc is written. An idle interactive session is waiting on the owner, not done: leave its files. A peer file you land names its author in the commit message. Red peer work goes into a patch beside its plan, as a `/drive` halt does, never into a commit: a red tree fails every session's gate. An empty scope commits nothing and goes to step 4 to push any commits ahead of the push target.
2. **Paired repos first.** For each repo in `adaptation.json` `paired_repos` with in-scope changes, commit and push it first per the rule file `paired_repo_rules` names for it. Then stage its pointer here in the matching commit.
3. **Categorical commits.** Group the scope by concern (feat, fix, refactor, chore, data, docs), one revertable commit each. Commit whole files with `git commit -F <msg file> -- <paths>`, with the message file at `.claude/scratch/<name>.txt`. Never commit through the shared index: a peer can stage into it between your calls. A file holding a peer's hunks beside yours goes through `python3 .claude/tools/index_commit.py stage` and `resync` (usage in its docstring).
4. **Push**, once, after the last commit:
   - **Land runner** (`adaptation.json` `land_prefix` set, and the branch contains `origin/<default-branch>`): `git push --recurse-submodules=check origin HEAD:<land_prefix><first 8 characters of the `Session id:` SessionStart printed>`. The check refuses a gitlink to a submodule commit no remote holds; step 2 pushed those first. When `adaptation.json` `land_watch` is set, run it with the land branch and the pushed sha, and wait for its terminal line. Its `LANDED <sha>` names the commit on the default branch, which differs from the pushed one when the runner rebased. From a linked worktree, then fast-forward the shared checkout: `git -C <shared checkout> merge --ff-only <landed sha>`. A refusal names the dirty paths it protects: report it and never force.
   - **Otherwise:** `git push origin <branch>`. The project's main check or CI proves the push after it lands, off this session's path.
5. **Verify.** `git status --short` here and in each paired repo shows the scope committed and the branch level with its push target. Dirty files outside the scope may remain.

Report each commit (sha and subject), the push target and the land watch's terminal line, and nothing else.
