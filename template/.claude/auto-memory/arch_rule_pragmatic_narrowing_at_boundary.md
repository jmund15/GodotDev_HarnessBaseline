---
name: arch-rule-pragmatic-narrowing-at-boundary
description: "When \"correct\" refactor cascades >5x touched-file count, evaluate bounded adapter at boundary sites with anchored structural seam."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 3458cc73-6e07-455f-8d44-8d1251b9edee
---

When a refactor's structurally-correct path (widening base contracts, propagating type changes up the call chain) cascades to >5x the file count of the change you're actually trying to ship, evaluate the bounded-adapter alternative: keep the existing surface unchanged, resolve the new capability via an explicit adapter at the cross-cutting boundary sites only. Trade type-system enforcement for blast-radius control.

**Why:** Both extremes silently regress. "Always do the architecturally correct thing" cascades 30-file changes that introduce bugs in untested edges. "Always pick the smallest patch" leaves semantic gaps (the silent-false-negative class — see [[feedback-refactor-parity-audit]]). The adapter is honest when (a) the cross-cutting boundary is small (<5 sites), AND (b) the structural contract is preserved at an anchored seam elsewhere (witness tests, type-system narrowing at the canonical entry point), AND (c) the adapter sites are made explicit in code (named helper, not inline cast).

**How to apply:** When refactor-cascade audit shows the "right" path touches >5x base file count, propose the bounded-adapter alternative in the plan body. Score: (i) cross-cutting site count, (ii) structural seam preserved elsewhere, (iii) explicit intent at adapter site. If all three pass, ship the adapter and add a witness test that pins the structural contract at the seam.

**Concrete:** 2026-05-18 BlackboardGraph plan Step 5. Base-contract widening (`IBlackboard` → `IBlackboardGraphReadOnly` across `UtilityConsideration` + `BaseAIConsideration3D/2D` + 7+7 subclasses + modifiers + `AISteeringProcessor3D` + tests) would have touched ~30 files. Chose `bb.FindParentGraph()` adapter at the 2 cross-scope consideration sites (`HasTagConsideration`, `FormationConsideration3D`) — ~10 files total. Structural seam preserved via `IBlackboardGraphReadOnly.Local` returning `IBlackboardReadOnly` (cross-scope-write rejection witness tests). Related: [[feedback-nullable-return-naming]] (named the adapter `FindParentGraph` for nullable-return clarity).
