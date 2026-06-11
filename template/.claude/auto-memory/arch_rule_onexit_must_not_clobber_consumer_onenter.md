---
name: OnExit must not clobber state consumer's OnEnter reads
description: HSM ordering — producer's OnExit runs BEFORE consumer's OnEnter; clearing producer-managed BB in OnExit clobbers the release-frame snapshot
type: feedback
originSessionId: 81f31487-7a69-4d7a-b408-557d35f9bd8d
---
In an HSM transition A→B, state A's `OnExit` runs to completion before state B's `OnEnter` starts. So any BB key A writes during its active phase that B needs to read on entry **must not be cleared in A.OnExit**. Clearing there means B always reads the cleared value — the live snapshot A computed just before transition is gone.

**Why:** Stale-prevention reasoning ("clear so the next cycle can't read stale") is correct in intent but wrong in placement. The cleanup belongs at the **start of the NEXT cycle** (A.OnEnter or B.OnExit), not the end of the current one — because between "end of current" and "start of next" the consumer reads.

**How to apply:** When you write `BB.Set(key, 0/null/sentinel)` in any `OnExit`, ask: "does the immediate next state read this same key in its OnEnter?" If yes, move the clear to either (a) THIS state's OnEnter (fresh slate per cycle, consumer reads the last live frame) or (b) the consumer's OnExit (cleanup after final read). Symptom shape when violated: consumer always reads the cleared sentinel and silently falls back via `?? default` to a "valid-looking" value, masking the bug.

**Concrete instance:** PR #80 — `CastChargeState.OnExit` cleared `BBDataSig.CastDistanceAtRelease=0f` immediately before `CastReleaseState.OnEnter` read the same key. Consumer's `castDist > 0f` guard failed, `EnvironmentDepositBehavior` fell back to `AimStrategy.MinRange` (2m), pillar always spawned at MinRange regardless of player aim. Fixed by relocating the clear to `CastChargeState.OnEnter` (commit `fe3a91dc`).
