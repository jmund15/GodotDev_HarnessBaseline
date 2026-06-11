---
name: gotcha_component_caches_bb_dependency_post_init_injection_noop
description: "An IComponent that caches a BB dependency at Initialize can't be driven by post-init BB injection in E2E; add a"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 88e67137-dcb2-439d-b03d-fe166e29ee2b
---

When an `IComponent` resolves a Blackboard dependency once at `Initialize` (e.g.
`bb.TryGet<IIntentSource>(BBDataSig.IntentSource, out _intentSource)`) and then reads
the **cached field** every frame, an E2E that loads the real scene (`ISceneRunner.Load`)
**cannot** drive that component by injecting a fake into the BB after load — the
production pipeline already ran `Initialize` during scene load and the component holds
the *real* cached reference. Re-`Set`-ting `BBDataSig.X` on the BB afterward no-ops:
the component never re-reads the BB.

**Symptom:** the plan says "inject a `FakeIntentSource` into the wizard BB post-load, then
`_Process`" — the drive silently does nothing; the component still uses the real source it
cached at init. (Bit Batch A's `WizardInteractDispatchTest` for `InteractorComponent3D`.)

**Verified:** `InteractorComponent3D.cs:152` caches `_intentSource` at `Initialize`; `:70`
reads the cached field each `_Process` (never re-reads the BB) — confirmed by source, so a
post-init `bb.Set` is unreachable by the component.

**Fix (preferred):** add a `#if TOOLS` test-helper that writes the cached field directly,
mirroring the file's existing helpers — `internal void SetIntentSourceForTesting(IIntentSource v) => _intentSource = v;`.
Call it AFTER load (so the real `Initialize` still proves the scene-wiring keystone), then
drive `_Process`. Cleaner than re-calling `Initialize(testBb)`, which re-runs
`OnPostInitialize` and double-subscribes signal handlers.

**Note:** `#if TOOLS` not `#if DEBUG` (Godot defines `TOOLS` during `dotnet test`); the
helper is stripped from exported builds. See `csharp_patterns.md` Test Helper Setters.
Companion to [[feedback_strict_tdd_for_integration_regressions]] and the IComponent
init-caching pattern in [[jmodot_combat_factory_defaults_seam]].
