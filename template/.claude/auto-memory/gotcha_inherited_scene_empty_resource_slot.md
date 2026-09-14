---
name: gotcha_inherited_scene_empty_resource_slot
description: Godot base .tscn leaves a sub-resource slot (e.g. CollisionShape3D.Shape) empty for inherited scenes to fill; instancing the base directly yields a half-built entity.
metadata: 
  node_type: memory
  type: reference
---

A sub-resource authored in a base `.tscn` (e.g. a `CollisionShape3D.Shape`) is owned by the base — inherited scenes override exported props but **cannot re-author a parent-owned sub-resource**. So the base leaves the slot **empty** for each leaf to fill. Instancing the **base directly** yields a half-built entity (a shapeless body → invisible to `Area3D.GetOverlappingBodies`). Tests/spawners must instance a **concrete leaf scene**, never the abstract base.

**How to apply:** before `Instantiate()`-ing a `.tscn` in a test/spawner, confirm it's a concrete leaf, not an abstract base with empty slots.

**Concrete:** `material_template.tscn` (empty body `CollisionShape3D`, abstract) vs `apple.tscn` (inherits it + adds a radius-0.25 sphere). Real materials spawn via the concrete leaf.
