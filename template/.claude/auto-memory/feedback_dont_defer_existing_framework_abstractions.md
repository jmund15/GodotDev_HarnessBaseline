---
name: Don't defer framework integration when the abstraction already lives at the framework boundary
description: Resist the "X varies per project — profiles can resolve their own" rationale when writing framework code if the X abstraction already exists in framework headers (e.g. Jmodot.Core interface + BBDataSig key). Integrate it directly; the deferral is a parity bug dressed up as architecture purity.
type: feedback
originSessionId: 2026-04-26-heal-and-status-architecture
---

When implementing framework code (Jmodot or any framework layer), resist the temptation to leave an extension point unimplemented with the rationale "X varies per project, subclasses can override and resolve their own." Before writing that comment, **check whether the X abstraction already exists at the framework boundary**.

**Why:** In the 2026-04-26 session I shipped `BehaviorSuppressedState` (Jmodot framework) with this comment in `ApplyDefaults`:

```csharp
// AnimationOverride is intentionally not applied here yet — animation
// pipeline integration varies per project (the wiring lives on the
// entity, not the framework). Profiles needing animation can override
// OnSuppressionEnter and resolve their own AnimationOrchestrator from BB.
```

The rationale was wrong. `IAnimationOrchestrator` lives in `Jmodot.Core.Visual.Animation.Sprite` and `BBDataSig.AnimationOrchestrator` lives in `Jmodot.Implementation.AI.BB` — both at framework level. The integration was 3 lines:

```csharp
if (BB.TryGet<IAnimationOrchestrator>(BBDataSig.AnimationOrchestrator, out _orchestrator) && _orchestrator != null)
{
    _orchestrator.StartAnim(profile.AnimationOverride);
    _animationStarted = true;
}
```

The Phase 1.5 refactor-parity audit caught the omission as a regression vs the deleted FreezeState. Without the audit, Wizard freeze would have shipped without the "hurt" animation playing.

**How to apply:**

Before writing `// X intentionally not applied — varies per project` (or any equivalent "pluggable extension point" deferral) in framework code:

1. **Grep the framework headers**: search `BBDataSig` and `Jmodot.Core` for any abstraction matching X.
2. **If the abstraction exists at framework level**: integrate it directly. The "varies per project" rationale doesn't apply when the framework has already committed to the abstraction.
3. **If genuinely project-specific**: leave the extension point, but EXPLICITLY note in code what consuming code must implement (link to a sample profile or test harness — don't just say "varies").

**Anti-pattern signal:** any framework-side comment claiming an integration is "project-specific" without naming what specifically varies between consuming projects. If you can't name the variation, the deferral is probably premature scope-trimming, not architectural restraint.

**Companion rule:** This is a sub-case of `feedback_refactor_parity_audit.md` — the deferred-integration comment IS a stub marker (`intentionally not applied yet`) under a different name. Refactor parity audit catches both shapes.
