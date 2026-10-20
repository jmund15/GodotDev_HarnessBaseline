---
disable-model-invocation: true
---

# /create_pr — Jmodot pairing, test plan and cross-references

Read by `/create_pr` when `adaptation.json` `paired_repos` lists the Jmodot submodule. `rules/jmodot_submodule.md` owns the branch and merge-order rules; this file owns the PR steps. The body-file pattern holds for every PR body here: never `--body "$(cat <<'EOF' ...)"`; `Write` the body to a temp file (session scratchpad), pass `--body-file`, `rm` the temp after.

`<jmodot-remote>` comes from `git -C Jmodot remote get-url origin`; never hardcode it.

## After Step 1: detect Jmodot changes

```bash
git diff main..HEAD -- Jmodot
```

A submodule pointer change means a Jmodot PR is required.

## Before Step 3-Create: create or find the Jmodot PR

1. Verify the Jmodot branch exists on the remote: `git -C Jmodot ls-remote --heads origin jmodot/<worktree-name>`. If it is missing, stop and ask the user to run `/commit_push` or `/clean_push` first.
2. Look for an open PR: `gh pr list --repo <jmodot-remote> --head jmodot/<worktree-name> --state open --json number,title`.
3. With none, create one: `gh pr create --repo <jmodot-remote> --base master --head jmodot/<worktree-name> --title "<main PR title>" --body-file "<temp-file>"`. Body:

   ```markdown
   Paired with {{PROJECT_NAME}} branch `claude/<worktree-name>`

   **Merge this FIRST** before the {{PROJECT_NAME}} PR.

   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   ```
4. With one, note its number for the cross-references.

## Main PR body

- **Jmodot dependency** section, only when the pointer changed (omit it otherwise):

  ```markdown
  ## Jmodot dependency
  Requires Jmodot PR [<jmodot-remote>#<N>](<url>) — <brief description of Jmodot changes>. **Merge Jmodot first.**
  ```
- **Test plan** replaces `## Verification` and is always included. Mark automated items `[x]` and manual items `[ ]`; take the regression gate counts from recent `/regression_gate` output or commit messages.

  ```markdown
  ## Test plan
  - [x] <automated test count> unit/integration tests
  - [x] Full regression gate: <total>/0 (Logic: <n>, Integration: <n>, Sanity: <n>)
  - [ ] Manual playtest for <subjective aspects>
  ```
- **UPDATE mode:** the cached Test plan stays verbatim like the Summary. Refresh its checkboxes only for items the new commits affect, and add a `Full regression rerun before merge` bullet when HSM, framework or public-API plumbing changed. The update body carries `<refreshed Test plan>` after the review-pass section.

## After Step 3: update the Jmodot PR

When a Jmodot PR was created or found, write its body to a temp file and run `gh pr edit <jmodot-pr-number> --repo <jmodot-remote> --body-file "<temp-file>"`:

```markdown
## Summary
- <Jmodot-specific changes summary>

Paired with {{PROJECT_NAME}} PR [<project-remote>#<project-pr-number>](<project-pr-url>)
Paired with {{PROJECT_NAME}} branch `claude/<worktree-name>`

**Merge this FIRST** before the {{PROJECT_NAME}} PR.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

Cross-reference both directions: the {{PROJECT_NAME}} body links the Jmodot PR and the Jmodot body links the {{PROJECT_NAME}} PR. The Step 4 summary adds a `Jmodot: #<N> — <title>` line (`N/A` without one) and a `Cross-refs` line.
