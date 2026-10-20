---
name: gotcha-baseline-wrong-repo-url-silent-fallback
description: "A clean /sync_baseline check proves sync only with the commit it read. baseline_sync.py v2 refuses a --baseline-dir or cache whose origin differs from baseline_repo, and --baseline-dir reads that checkout's HEAD, not --ref or its working files."
metadata: 
  node_type: memory
  type: feedback
---

A `check` that reports clean proves sync with the baseline commit it read, nothing more.

**Current behavior (engine v2, 2026-09-15):**
- **Origin must match.** `--baseline-dir D` must be a git repository whose `origin` equals the lock's `baseline_repo` after URL normalization; otherwise the engine exits 1 (`baseline origin mismatch`). The default cache clone with a different origin is re-cloned, or refused when linked author worktrees hang off it. The v1 fallback to a non-repo staging copy cannot happen.
- **`--baseline-dir` reads that checkout's `HEAD`.** It ignores `--ref` and never reads working files. A checkout with uncommitted or unmerged work, such as a publish worktree before its commit, yields the old commit's content.
- **`watch` is retired.** v2 rows are `tracked`, `local`, `forked` or `composed`; `check --strict` exits 1 on any leftover v1 `watch` row. Forked blind spots are in [[gotcha-baseline-forked-file-drifts-behind]].

**How to apply:**
- The authoritative sync state is `check` without `--baseline-dir`: it reads the pinned commit fetched from `baseline_repo`.
- Use `--baseline-dir` for a checkout whose `HEAD` is the commit you mean. "The sync only works with `--baseline-dir`" is still a red flag that `baseline_repo` is wrong: verify with `git ls-remote <url>`.
- To test a tree that is not committed yet, commit it (a snapshot commit through a temporary index works) and point `--baseline-dir` at a worktree at that commit.

**Verified:**
- 2026-06-14 (v1): the lock named `<owner>/wrong-baseline` (404); the real repo is `<owner>/published-baseline`. The staged `harness-baseline/` directory was 22 files ahead of the remote. A second consumer (<other-project>) had `baseline_repo` pointed at its own game repo. v1 `watch` rows were never hash-compared, so a universal command under `watch` fell a whole section behind while `check` read clean twice.
- 2026-09-15 (v2): the integration publish dry run failed the bootstrap proof because bootstrap's `--baseline-dir` read the materialized worktree's `HEAD`, still the old baseline main, instead of its uncommitted publication tree. The proof now bootstraps a snapshot commit's worktree.
