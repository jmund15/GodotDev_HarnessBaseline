---
disable-model-invocation: true
---

# Jmodot Submodule Procedure

<!-- Single source of truth for Jmodot submodule branch management. -->
<!-- Referenced by: /commit_push, /clean_push, /merge_pr -->
<!-- See also: .claude/rules/jmodot_submodule.md (path-scoped on Jmodot/**) for the authoritative rule set. -->

## Branch Naming

Derive the Jmodot branch name from the {{PROJECT_NAME}} branch: `jmodot/<worktree-name>`.
Example: {{PROJECT_NAME}} branch `claude/naughty-mayer` → Jmodot branch `jmodot/naughty-mayer`.

## Detached HEAD Warning

**CRITICAL:** In worktrees, `git submodule update` puts Jmodot in detached HEAD state. You MUST get onto a branch before committing — otherwise commits are local-only and invisible to GitHub. The {{PROJECT_NAME}} PR will show an unresolvable submodule reference.

## Branch Checkout (5-Step)

Handles first AND subsequent pushes. Run inside the Jmodot directory:

1. Check if local branch exists: `git branch --list jmodot/<worktree-name>`
2. If no local branch, check remote: `git ls-remote --heads origin jmodot/<worktree-name>`
3. **No local, no remote** → `git checkout -b jmodot/<worktree-name>` (create new)
4. **Remote exists, no local** → `git checkout -b jmodot/<worktree-name> origin/jmodot/<worktree-name>` (track existing)
5. **Local exists** → `git checkout jmodot/<worktree-name>` (switch to it)

## Push

After committing: `git push -u origin jmodot/<worktree-name>`

Then back in {{PROJECT_NAME}}: `git add Jmodot` to update the submodule pointer.
