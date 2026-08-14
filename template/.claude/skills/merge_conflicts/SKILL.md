---
name: merge_conflicts
description: >-
  Use when a git merge/rebase is in progress and the working tree carries conflict markers
  (`<<<<<<<` / `=======` / `>>>>>>>`), or when asked to resolve an in-progress integration.
  Resolves each conflict by reconstructing both sides' intent — never pick-a-side by default.
  SKIP when there are no unresolved conflicts to resolve.
---

# Resolving Merge Conflicts

1. **See the current state.** Run `git status` for the unmerged files and `git log --merge -p <file>` to see what both sides changed on each conflict hunk. Confirm the merge target (`MERGE_HEAD`/`ORIG_HEAD`) and the branch being merged in.

2. **Resolve by intent — the core beat.** For each conflict, reconstruct what EACH side was trying to do: read the commits both branches touched on the hunk, their messages, and the surrounding change. Write the resolution that satisfies both intents; when incompatible, take the one matching the merge's stated goal and record the trade-off. Never pick-a-side by default, and never invent new behaviour.

3. **Abort only on a user decision to abandon.** `git merge --abort` mid-merge discards the resolution state you have built — it is not a "start over" escape hatch. Abort only when the user explicitly decides the merge should not happen; otherwise keep resolving in place.

4. **Gate before concluding.** If the merge touched `.cs` files, run `/regression_gate` before the merge commit is finalized — a merge is not a carve-out from the mandatory gate. A gate failure is not automatically "the merge introduced it": run the failing test on the merge TARGET's live tree first (throwaway `git worktree add --detach <target>`), per `feedback_verify_merge_failures_against_target_not_baseline.md` — the target's green baseline can mask pre-existing reds.

5. **Godot resource files are not hand-mergeable.** `.tscn`/`.tres` are order-sensitive — do not resolve their conflicts line-by-line. Take one side wholesale (`git checkout --ours`/`--theirs`), then re-apply the other side's change through the editor or MCP, and verify the scene/resource still loads.

6. **Finish.** Stage the resolved files and commit. If rebasing, continue until all commits are replayed.
