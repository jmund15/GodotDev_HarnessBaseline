---
name: gotcha_empty_tween_never_fires_finished
description: "A Tween with zero tweeners errors ('started with no Tweeners') and never fires Finished — teardown hung on Finished (QueueFree, callbacks) leaks the node. Count tweeners; take the completion path synchronously when zero."
metadata: 
  node_type: memory
  type: project
  originSessionId: 66931000-c20f-4f54-b483-af3fa3e136e8
---

A `Tween` whose property-collection loops matched nothing errors (`started with no Tweeners`) and **never emits `Finished`**. Anything load-bearing hung off `Finished` (QueueFree, completion callbacks) silently never runs.

**Why:** enemy death fades collected zero tweeners → corpses never freed → encounter kill-detection (`TreeExiting`) never fired → rooms uncompletable.
**How to apply:** count tweeners as you add them; zero → `tween.Kill()` and run the completion path synchronously. Canonical helper: `Jmodot VisualFader3D.StartFadeOut` returns null on nothing-fadeable — callers MUST branch. See [[gotcha_sprite3d_typed_ref_excludes_animatedsprite3d]].
**Verified:** log correlation (each death → Tween error) + EnemyDeathCompletionTests.
