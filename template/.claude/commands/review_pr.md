---
disable-model-invocation: true
description: Review one PR, or the uncommitted changes, through /review diff mode and report a merge verdict
argument-hint: "[<PR number>]"
---

# Review PR

Reviews one pull request, or the working tree's uncommitted changes, through `/review` diff mode with the code and test checklists, then adds the PR's hygiene and a merge verdict. It reports and applies nothing: the owner, or the batch command that called it, owns the PR's branch and applies the fixes.

## Arguments

- `/review_pr <N>`: PR mode, over PR `<N>`.
- `/review_pr`: pre-commit mode, over the uncommitted changes of the current checkout.

An argument that is not a positive integer prints these two lines and stops. A PR `gh pr view` cannot find is named above the same usage, and nothing is dispatched.

## 1. Fix the subject and its root

- **PR mode.** Run `gh pr view <N> --json number,title,body,baseRefName,headRefOid,additions,deletions,changedFiles`. The root is the checkout whose `HEAD` is `headRefOid` in `git worktree list`. When none is, run `git fetch origin pull/<N>/head` and `git worktree add --detach .claude/worktrees/pr<N> <headRefOid>`; `/worktree_prune` removes it later. The range is `<base>..<headRefOid>`, where `<base>` is `git merge-base origin/<baseRefName> <headRefOid>`.
- **Pre-commit mode.** The subject is `--files` with every path `git status --porcelain` lists, and the root is the current checkout. When it lists nothing, print `nothing to review` and stop.
- **A re-review after fixes** runs only when `/review` §4's round test admits a second round. Its range starts at the head the first round reviewed.

The root is passed to `/review` §3 as the claim-check root. A PR's quotes checked against another branch read as fabricated.

## 2. Review

Run `/review` diff mode §1 to §4 on that subject. The brief adds:

- **This command's checklists:** `.claude/skills/architecture_philosophy/SKILL.md`, `.claude/commands/checklists/code_quality.md` and `.claude/commands/checklists/test_quality.md`.
- **PR mode:** the PR's title and body, verbatim, as the author's stated intent.

`/review` §4 decides each finding here and applies none. Each kept finding goes into the report with the change it asks for.

## 3. PR hygiene (PR mode)

Check three items against the `gh pr view` output and the diff:

- The title states the change.
- The body states what changed and how it was tested.
- A change to logic ships the tests that drive it.

## 4. Report

Print `/review` §5's report, then the hygiene items, then the verdict:

- **REVISE** when a kept finding stands unfixed or an owner fork is open. Name each one.
- **APPROVE** otherwise.

Pre-commit mode prints `/review` §5's report alone. It has no hygiene and no verdict, because nothing is being merged.

The report stays in the terminal and the run directory. Nothing is posted to GitHub.
