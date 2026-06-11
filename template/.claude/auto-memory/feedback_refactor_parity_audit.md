---
name: Refactor parity audit before merge
description: When refactoring deletes/replaces existing code, do a line-by-line behavior diff old → new BEFORE merge. Flag any "deferred/stub/follow-up/TODO" markers as merge-blockers requiring explicit user approval.
type: feedback
originSessionId: 2026-04-25-craftsm-wheel-followup
---

When a refactor retires existing code (deletes a file, replaces a class with a new one, restructures a state machine), do a **behavior parity audit** old → new BEFORE shipping the PR.

**Why:** PR #58 (CraftSM held-L2 wheel) shipped with three silent regressions that a 5-minute parity diff against the retired `CraftWheelState.cs` would have caught:
1. Wheel UI never wired up — the new state's docstring literally said "wiring deferred to the wheel-UI integration follow-up." Shipped anyway.
2. Bullet-time dropped — old state had `BulletTimeController` with Enter/Update/Exit; new state had nothing. No migration note.
3. BB resolution placed in `OnInit` (frame 0) when old state did it in `OnEnter` (lazy) — guaranteed null-forever given installer's `CallDeferred` timing.

User: *"really unsatisfied with the quality of this PR... it left out a lot of things and it's not very comprehensive"*

The session_audit + autolearn + self_evaluate stack passed because none of those agents do behavioral feature-parity checks — they audit internal quality of the diff, not what the diff stopped doing.

**How to apply:**

Before merging any refactor PR, do this sequence:

1. **Identify retired code**: `git diff <base>..HEAD --diff-filter=D --name-only` for fully-deleted files; `git log --diff-filter=D --name-only` to find recently-deleted siblings of new files.
2. **For each retired file**, read it and enumerate its public surface: methods, exports, lifecycle hooks (`OnEnter`/`OnExit`/`OnProcessFrame`/etc.), event subscriptions, side effects (engine state, BB writes, signals emitted).
3. **For the replacement code**, verify each retired surface item is either:
   - **Reproduced** with equivalent behavior, OR
   - **Explicitly noted as removed** in PR description with rationale.
4. **Grep changed code for stub markers**: `deferred`, `TODO`, `FIXME`, `follow-up`, `stub`, `not yet wired`, `no-op until`, `placeholder`. ANY hit is a merge-blocker; surface to user and require explicit "ship anyway" approval.
5. **Visual / gameplay-domain regressions** require playtest before merge — log-only verification can't catch dropped bullet-time or invisible UI.

**Counter-pattern to avoid:**
"Ship the architecture, defer the wiring." Treats the merge boundary as a checkpoint to be passed, not a contract to be honored. Deletion is the path of least resistance; without the parity audit, silent regressions accumulate.

**Mechanism proposed for session_audit skill:**
Add a "Phase 0: Refactor Parity Check" before the 3 sub-agents — orchestrator runs greps for stub markers + retired-file enumeration, surfaces hits at TOP of report. Findings here are MERGE-BLOCKER tier (above FIX), not advisory.

**2026-04-26 reinforcement:** Phase 1.5 stub-marker scan caught `// AnimationOverride is intentionally not applied here yet — animation pipeline integration varies per project (the wiring lives on the entity, not the framework)` in shipped framework code (`Jmodot/Implementation/AI/HSM/BehaviorSuppressedState.cs`). The deleted `AI/HSM/Wizard/FreezeState.cs` called `AnimationOrchestrator.StartAnim()` on enter. Without the parity check, Wizard freeze would have lost its "hurt" animation. Confirms: any "intentionally deferred" / "TODO" / "not yet wired" marker in code that REPLACES retired behavior is a parity break, even when phrased as a documented design choice. The "varies per project" rationale is especially dangerous because it sounds like architectural restraint — see `feedback_dont_defer_existing_framework_abstractions.md` for the framework-side variant.
