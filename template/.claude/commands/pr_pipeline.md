---
disable-model-invocation: true
description: Review, gate and merge a batch of open PRs in conflict-aware order, with the project's own steps from adaptation.json
argument-hint: "[<PR number>...]"
---

# PR Pipeline

Drives open PRs from review to merge in one batch. The loop below is every project's; each project's extra steps are the `pr_pipeline` hook points of `adaptation.json`. Per-PR work belongs to `/review_pr` and `/merge_pr`, and this command adds no second path to either.

## Arguments

- `/pr_pipeline`: every open PR.
- `/pr_pipeline <N>...`: only those PRs.

A token that is not a positive integer, or a PR number not in `gh pr list --state open`, is dropped with a named warning, and nothing guesses a substitute. When none is left, print `accepts: [<PR number>...]` and `example: /pr_pipeline 5 7 8`, then stop.

## Hook points

`python3 .claude/tools/adaptation.py get pr_pipeline.<point>` prints a point's entries one per line; an empty point prints nothing and the step runs without it. Run an entry from the repo root. An entry starting with `/` is a command: run it as one. Any other entry is a shell command line. Before running, replace `{pr}` with the PR number, `{head}` with its head SHA, `{base}` with its base branch name, and `{worktree}` with the checkout holding its head (`/review_pr` §1 finds it). An entry that exits non-zero or reports a failure is a finding in that PR's row and blocks its merge until fixed or the owner accepts it in words.

| Point | Runs | Per |
|---|---|---|
| `discover` | after §1 lists the PRs | batch |
| `sync` | in place of §3's default sync | PR |
| `pre_review` | before §4 | PR |
| `gate` | §5 | PR |
| `checklist` | §6 | PR |
| `post_merge` | after each merge in §8 | PR |

## 1. Discover

Run `gh pr list --state open --json number,title,headRefName,headRefOid,baseRefName,additions,deletions,changedFiles,createdAt`, filter to the arguments, then run each `discover` entry once. An entry's output joins the board as notes, and an output line `order: <A> before <B>` is a merge-order constraint.

An empty set prints `no open PRs selected` and stops: nothing else runs.

## 2. Order

Per PR, `gh pr diff <N> --name-only`. For each pair, flag the same file touched by both. Merge order: constraints from `discover` first, then the PR that touches fewer files of the overlap, then the older `createdAt`. Print the board (PR, title, files changed, overlaps, order) and the proposed order, and ask the owner to approve or reorder it before §3.

## 3. Sync

Per PR in order, `gh pr view <N> --json mergeable,baseRefName`. A `CONFLICTING` PR runs the `sync` entries, or `/merge_pr`'s Step 0 worktree and Step 1 sync when `sync` is empty. Re-sync a later PR after each merge in §8, since the base moved. Done when `gh pr view <N> --json mergeable` prints `MERGEABLE`; a PR that stays conflicting is deferred with its reason.

## 4. Review

Per PR, run each `pre_review` entry, then `/review_pr <N>`. It reviews through `/review` diff mode and reports `APPROVE` or `REVISE` with the changes each kept finding asks for.

Apply each asked change on the PR's branch in its `{worktree}`, then commit and push it there. An owner fork stays open until the owner answers it. A `REVISE` PR re-enters `/review_pr` only when `/review` §4's round test admits a second round; otherwise it is deferred.

## 5. Gate

Per PR, serially, run each `gate` entry in the PR's `{worktree}`. A gate that boots the app or runs a single-flight tool never runs for two PRs at once. A red gate returns the PR to §4's fix step; a second red defers it.

## 6. Checklist

Per PR, run each `checklist` entry. Print each PR's state as `READY` (review `APPROVE` or fixes applied, gate green, checklist entries clean) or `NOT READY: <reason>`.

A `checklist` entry may need the owner's hands (a playtest, a manual check). Its PR stays `NOT READY: owner check` until the owner says it passed.

## 7. Report before merging

Print the final order, each PR's state, deferred PRs with reasons, and the changes applied. When no PR is `READY`, print `no merges` and stop. Otherwise stop and ask the owner which `READY` PRs to merge, in what order; merging starts on that answer.

## 8. Merge

Per PR the owner named, in their order, run `/merge_pr <N>`; it asks the owner before the merge itself. After each merge, run each `post_merge` entry, then re-run §3 for the PRs left. A PR the owner removes from the queue at any point is noted in the report.

## 9. Report

Print per PR: merged, deferred or failed, with its reason and the merge SHA. Run `git fetch origin` and `gh pr list --state open` once more: a PR that opened or moved during the run is listed as not covered. Add a dated entry to `CHANGELOG.md` when the repo has one: `feat` titles under Added, `fix` under Fixed, `refactor` and `chore` under Changed, merged PRs only.

## Constraints

- Never force-push, and never merge without the owner's word: `/merge_pr` owns both.
- The `pr_pipeline` block is the project's whole addition. A project step missing from it is a hook-point entry to add, never prose to add here.
