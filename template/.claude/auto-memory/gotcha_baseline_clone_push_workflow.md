---
name: gotcha-baseline-clone-push-workflow
description: "Upstreaming via the baseline cache clone: checkout main first (sync ops detach HEAD; detached commit + push silently no-ops), and author memory-file template copies in the harness-normalized frontmatter form"
metadata: 
  node_type: memory
  type: project
  originSessionId: 8d59ce77-9de6-4405-9305-e5e56b10bc71
---

Two verified pitfalls when pushing to the harness baseline from `.claude/.cache/baseline-repo`:

1. **Detached HEAD swallows commits.** `baseline_sync.py` ops (check/pull/update-lock) leave the clone on a detached HEAD. A commit made without `git checkout main && git pull --ff-only` first lands orphaned, and `git push origin main` reports "Everything up-to-date" — a silent no-op. **Verified:** 2026-07-06, commit `ac85db0` landed detached and the push no-op'd; cherry-pick onto main fixed it. Always checkout main immediately before committing in the clone.

2. **Memory-file frontmatter normalization re-drifts un-normalized templates.** The local memory harness rewrites `.claude/auto-memory/*.md` frontmatter on save (adds `node_type`, `originSessionId`, quotes the description). A baseline template copy authored in un-normalized form goes `diverged` on the next local save. Author baseline copies of memory files in the normalized form. **Verified:** 2026-07-06, `gotcha_cross_project_memory_index_autoload` diverged immediately after upstreaming; matching the normalized form (baseline `38291ab`) cleared it.

Related: [[gotcha-cascade-gate-vacuous-without-godot-bin]] (positive-liveness family — a "success" report that can mean "nothing happened").
