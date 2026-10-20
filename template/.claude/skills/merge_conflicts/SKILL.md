---
name: merge_conflicts
description: >-
  Use to resolve conflicts in an in-progress merge, rebase or integration, including a resource-receipt resume.
---

# Resolving Merge Conflicts

## Arguments

`/merge_conflicts` resolves the current in-progress integration. `/merge_conflicts <worktree> --resource-receipt <path>` resumes a sync that stopped on a conflicted authored resource in that worktree and updates only the named receipt. Any other flag stops before mutation.

1. **Resolve by intent — the core beat.** For each conflict, reconstruct what EACH side was trying to do: read `git log --merge -p <file>` for the commits both branches touched on the hunk, their messages, and the surrounding change. Confirm the merge target (`MERGE_HEAD`/`ORIG_HEAD`) and the branch being merged in. Write the resolution that satisfies both intents; when incompatible, take the one matching the merge's stated goal and record the trade-off. Never pick-a-side by default, and never invent new behaviour.

2. **Abort only on a user decision to abandon.** `git merge --abort` mid-merge discards the resolution state you have built — it is not a "start over" escape hatch. Abort only when the user explicitly decides the merge should not happen; otherwise keep resolving in place.

3. **Gate before concluding.** A merge commit is a commit, so it takes the gate `/regression_gate` §When Required states, before the merge commit is finalized. A gate failure is not automatically "the merge introduced it": run the failing test on the merge TARGET's live tree first (throwaway `git worktree add --detach <target>`) — the target's green baseline can mask pre-existing reds.

4. **Godot resource conflicts use the external authored-reapplication receipt.** Never leave conflict markers or a Git side selection as the final resource. For `--resource-receipt`, verify its head/blob hashes, start from one parent's whole blob, reapply the other intent by hand per `rules/godot_files.md`, order properties as the C# class declares its exports, prove the load through the build and the gate's import gate, and record each saved path/hash. Set `status=authored` only when every receipt row is saved and no unmerged row remains; head drift or unverifiable intent stays blocked.

5. **Finish.** Before the merge commit, compare base, ours, theirs and the result at the actual merge: a clean merge can drop an incoming addition, and path-filtered history alone does not show it. Done when no unmerged path remains and the merge commit exists or the rebase has replayed every commit.
