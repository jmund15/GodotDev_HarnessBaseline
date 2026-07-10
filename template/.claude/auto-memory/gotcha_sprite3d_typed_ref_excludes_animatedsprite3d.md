---
name: gotcha_sprite3d_typed_ref_excludes_animatedsprite3d
description: "Sprite3D-typed exports/discovery silently exclude AnimatedSprite3D (siblings under SpriteBase3D) — node_paths wiring resolves to null, typed searches find nothing. Target SpriteBase3D for any tint/fade/scale code."
metadata: 
  node_type: memory
  type: project
  originSessionId: 66931000-c20f-4f54-b483-af3fa3e136e8
---

`Sprite3D` and `AnimatedSprite3D` are SIBLINGS under `SpriteBase3D` (which owns `Modulate`). A `Sprite3D`-typed `[Export]` **silently nulls** an `AnimatedSprite3D` wired via node_paths, and `TryGetFirstChildOfType<Sprite3D>` skips it — no error, feature just dies. Any code that tints/fades/scales "the sprite" must take `SpriteBase3D`.

**Why:** one type filter disabled hit flash AND death fades on every enemy (all wired `../AnimSprite` correctly).
**How to apply:** widen the export/discovery type — existing `Sprite3D` wirings stay valid. See [[gotcha_empty_tween_never_fires_finished]].
**Verified:** playtest 2026-07-06 log + EnemyDeathCompletionTests/HitFlashComponentTests red→green on type widen alone.
