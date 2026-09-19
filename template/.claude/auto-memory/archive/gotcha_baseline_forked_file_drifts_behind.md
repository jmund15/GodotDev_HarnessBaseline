---
name: gotcha-baseline-forked-file-drifts-behind
description: "A forked baseline file stops receiving upstream fixes. baseline_sync.py v2 records the fork base and check --strict reports forked-upstream-moved or forked-base-unknown; reconcile with pull --force then track."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 01c8633d-c4e3-4b41-88df-21aa59609efb
---

A `forked` row keeps its local copy instead of the upstream `template/` file, so the copy can fall behind a real upstream fix. The risk is sharpest when the forked file is itself a tool, such as `baseline_sync.py`: the copy that runs the sync diverges from upstream.

**Current behavior (engine v2, 2026-09-15):**
- `fork <relpath>` records `base`, the upstream sha at the pinned commit when forked.
- `check` reports `forked-upstream-moved` once upstream passes that base, and `forked-base-unknown` when `base` is null, as on every fork migrated from v1. `check --strict` exits 1 on both.
- `triage` surfaces forked rows whose `judged` is null.

**Why forks go stale:** forks are often created in a bulk reconciliation pass (e.g. commit message `... fork 10`), not a per-file decision, so the fork rationale may be stale or absent. "Forked" does not mean "intentionally different forever."

**How to apply:**
- On `forked-upstream-moved` or `forked-base-unknown`, run `baseline_sync.py diff <relpath>`. When local is strictly behind an upstream improvement and the fork has no live project rationale, un-fork.
- **Un-fork:** `baseline_sync.py pull --force <relpath>` (a forced forked row stays `forked` with the upstream `hash`), then `baseline_sync.py track <relpath>` (forked to tracked, keeping that hash). `pull` alone leaves the row forked.
- A fix that must reach other projects while the file stays forked is authored upstream: `baseline_sync.py author start`, edit and commit in that worktree, then `publish --from-worktree <path>`.

**Verified:** 2026-06-14 (v1): `baseline_sync.py` was bulk-forked in `4ed93582` and had drifted behind the template's word-boundary `reverse_sub` safety fix; reconciled with pull and track in `00a86aa9`. v1 excluded forked rows from drift checks entirely; the v2 `base` field and states replaced that (plan `.claude/plans/sync-baseline-v2.md` §1, §7). Related: [[gotcha-baseline-wrong-repo-url-silent-fallback]].
