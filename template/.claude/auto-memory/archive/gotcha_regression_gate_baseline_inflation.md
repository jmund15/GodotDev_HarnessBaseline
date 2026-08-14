---
name: gotcha-regression-gate-baseline-inflation
description: "A regression-gate current<baseline drop can be an inflated baseline (a concurrent session's uncommitted tests), not a regression — check the baseline's git history first."
metadata: 
  node_type: memory
  type: reference
  originSessionId: f600a36e-4310-4101-a0e0-f70155255a7c
---

A `/regression_gate` result of `current.passed < baseline.passed` is NOT always a regression. It can be an **inflated baseline**: when a concurrent session's *uncommitted* test files were present in the shared working tree at the moment a prior gate stamped the baseline, the stamped count includes tests that were never committed to `main`. The next honest run on a clean checkout then reads *fewer* tests and looks like a drop.

**The tell** — inspect the baseline's own history:
- `git log -p -- Tests/regression_baseline.json | grep -E '"passed"|^commit'` shows a large `passed` jump in a single commit, AND
- `git show <that-commit> --stat | grep Tests/` shows that commit added only a few test files (a +N baseline bump for a +1-or-2-test commit is the inflation signature).

**Verified this session (2026-07-03):** Integration baseline jumped `750 → 786` (+36) in a commit whose entire test delta was ONE file (`FloorDriverTests.cs`, 1 test). The honest count was 753 = 750 + the session's 3 legit additions.

**Decision rule before treating a gate drop as a regression:** the drop is a real regression ONLY if `Failed>0` OR a test class stopped compiling/discovering (build error). If `Failed:0`, `Skipped:0`, the count is self-consistent, AND `honest-count == prior-real-count + your session's additions` exactly → the baseline was inflated. Correct it DOWN to the honest count (the gate flags negative deltas — user-confirm the lowering; do not silently auto-lower). Sibling: `gotcha_concurrent_session_hazards.md` (the staging-collision face of the same concurrent-session-shared-checkout hazard).
