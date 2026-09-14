---
name: OnExit must not clobber state consumer's OnEnter reads
description: HSM ordering — producer's OnExit runs BEFORE consumer's OnEnter; clearing producer-managed BB in OnExit clobbers the release-frame snapshot
type: feedback
---
In an HSM transition A→B, state A's `OnExit` runs to completion before state B's `OnEnter` starts. So any BB key A writes during its active phase that B needs to read on entry **must not be cleared in A.OnExit**. Clearing there means B always reads the cleared value — the live snapshot A computed just before transition is gone.

**Why:** Stale-prevention reasoning ("clear so the next cycle can't read stale") is correct in intent but wrong in placement. The cleanup belongs at the **start of the NEXT cycle** (A.OnEnter or B.OnExit), not the end of the current one — because between "end of current" and "start of next" the consumer reads.

**How to apply:** When you write `BB.Set(key, 0/null/sentinel)` in any `OnExit`, ask: "does the immediate next state read this same key in its OnEnter?" If yes, move the clear to either (a) THIS state's OnEnter (fresh slate per cycle, consumer reads the last live frame) or (b) the consumer's OnExit (cleanup after final read). Symptom shape when violated: consumer always reads the cleared sentinel and silently falls back via `?? default` to a "valid-looking" value, masking the bug.

**Concrete shape:** state A publishes a release-frame snapshot. State B reads it in `OnEnter`. Clear the key in A's next `OnEnter` or B's `OnExit`; clearing it in A's current `OnExit` destroys the handoff value.
