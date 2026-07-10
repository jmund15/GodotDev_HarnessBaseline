---
name: gotcha-baseline-forked-file-drifts-behind
description: "A forked baseline file is excluded from drift checks, so it silently stops receiving upstream template fixes — audit forks and reconcile via pull+track."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 01c8633d-c4e3-4b41-88df-21aa59609efb
---

A `forked` entry in `.claude/baseline.lock.json` is excluded from ALL drift checks (`baseline_sync.py check` reports it only as a count). Consequence: the local copy silently stops receiving upstream `template/` improvements and can fall behind a real fix while `check` keeps reporting "clean". The danger is sharpest when the forked file is itself a tool (`baseline_sync.py`) — the copy that *runs* the sync diverges from upstream and you never get told.

**Why:** forks are often created in a *bulk* reconciliation pass (e.g. commit message `... fork 10`), not a per-file deliberate decision — so the fork rationale may be stale or nonexistent. "Forked" ≠ "intentionally different forever."

**How to apply:**
- When touching a forked baseline file, FIRST diff local vs template: `git diff --no-index harness-baseline/template/<relpath> <relpath>` (the engine's own `diff` subcommand skips nothing by status). If local is strictly *behind* an upstream improvement and the fork has no live project-specific rationale, reconcile.
- **Un-fork sequence:** `baseline_sync.py pull <relpath>` (writes forward-subbed template → local, sets the lock hash) THEN `baseline_sync.py track <relpath>` (flips status forked→tracked, keeps the hash). `pull` alone leaves status `forked` — `track` is the second half. After both, `check` classifies it `in-sync`.
- A bug fix that must reach other projects goes in BOTH the local copy AND `harness-baseline/template/<relpath>` by hand even while forked, since `materialize` would otherwise overwrite the template with the full forked content.
- Periodically audit the forked set (`baseline_sync.py paths --status forked`) for stale-behind drift.

**Verified:** 2026-06-14 — `baseline_sync.py` was bulk-forked in `4ed93582` and had drifted behind the template's word-boundary `reverse_sub` safety fix; reconciled (pull+track) in `00a86aa9`. Related: the engine's `diff`/`check` crashed on Windows cp1252 stdout for non-ASCII glyphs until the UTF-8 fix in `05c8d9c8`.
