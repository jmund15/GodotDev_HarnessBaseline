---
paths:
  - "Jmodot/**"
  - ".gitmodules"
---

# Jmodot Submodule Rules

Jmodot is a git submodule. These rules are **authoritative** — all command files (`commit_push`, `clean_push`, `create_pr`, `merge_pr`, `review_pr`, `review_prs`) follow them.

**Jmodot's primary branch is `master`.** It contains the full codebase. All feature branches are temporary and must be based on `master`.

1. **Branch strategy (two cases):**
   - **PP feature worktree** (`claude/<name>`): create a paired `jmodot/<name>` branch from `origin/master`. PR it to `master` when done, then delete.
   - **PP main branch** (no worktree): commit Jmodot changes directly to `master`. No intermediate branch needed.
2. **CRITICAL — Verify base before committing:** Before making any Jmodot commits, run `git -C Jmodot branch -r --contains HEAD`. If `origin/master` is NOT in the output, you are on a partial feature branch — STOP and rebase onto `origin/master` first. A branch that exists locally is NOT guaranteed to have the full codebase.
3. **Detached HEAD in worktrees:** `git submodule update` puts Jmodot in detached HEAD state. You MUST create/checkout a branch before committing — otherwise commits are local-only and invisible to GitHub.
4. **Branch checkout (handles first AND subsequent pushes):** Check local → check remote → create/track/switch. Never bare `git checkout -b` (fails if branch exists). See `commit_push.md` for the 5-step procedure.
5. **Jmodot PR creation (worktree only):** Handled by `/create_pr`. Every Jmodot feature branch with changes must have a paired PR on the Jmodot remote, created alongside the PP PR with bidirectional cross-references.
6. **Merge order (worktree only):** Jmodot branches merge to master FIRST, then {{PROJECT_NAME}} PRs that reference them. The PP PR will show an unresolvable submodule reference if Jmodot isn't merged. **After merging Jmodot, do NOT update the PP branch's submodule pointer** — leave it pointing at its original commit. GitHub resolves submodule pointers at PP merge time. Updating to Jmodot's latest master pulls in unrelated PRs' API changes and breaks the build.
7. **Mandatory submodule update:** Run `git submodule update --init --recursive` after EVERY branch transition (`git checkout`, `git pull`, rebase). `git checkout` updates the pointer but NOT the working tree — without this, builds fail with missing types.
8. **Branch cleanup:** After a Jmodot feature branch merges to master, notify the user to delete the paired branch from the remote and close its PR manually. Do NOT delete branches yourself.
9. **`git mv` from PP into the submodule path dual-tracks.** `git -C <PP> mv <pp-path> Jmodot/<sub-path>` stages the moved files at the `Jmodot/` path *in PP's index* (shown `RM`/`R`) AND stages the `Jmodot` gitlink as **deleted** (`D Jmodot`), while the submodule sees the same files **untracked**. Untangle before committing: (a) `git -C <PP> reset HEAD -- Jmodot/<paths>` → PP now records only the source-side deletions; (b) `git -C Jmodot add <paths> && commit` to master; (c) `git -C <PP> add Jmodot` to restage the gitlink as a **pointer bump** (verify the staged diff shows `160000` mode, not file tracking), then commit PP. Cross-repo moves don't preserve file history; carry each `.cs.uid` in the same mv so Godot uid resolution survives `--headless --import`.
