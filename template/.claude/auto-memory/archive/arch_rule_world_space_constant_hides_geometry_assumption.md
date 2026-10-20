---
name: arch-rule-world-space-constant-hides-geometry-assumption
description: "A world-space constant that encodes an assumption about scene geometry (surface height, origin plane, bounds) is invisible to unit tests and to review, and breaks silently when a second variant of that geometry exists"
metadata: 
  node_type: memory
  type: project
  modified: 2026-08-18T01:22:17.857Z
---

A constant that encodes an assumption about scene geometry — the walkable surface sits at y≈0, the origin plane is the floor, the arena fits in these bounds — is a coupling with no declared endpoint. Unit tests pass because they supply the same assumption; review passes because the constant reads as a tuning number. It breaks the moment a second variant of that geometry exists with a different value, and the break is a quiet mis-placement rather than an error.

**Why:** the constant's real dependency is on a scene, which nothing in the type system, the test fixture, or the diff makes visible. Every reader sees a magnitude and no one sees the contract.

**How to apply:** derive the value from the geometry at runtime (query the surface, the collider, the navmesh) or make it an authored field on the entity that owns that geometry, so a second variant carries its own value. When a literal is genuinely correct for now, name the assumed geometry in the identifier or beside it, so the second variant's author is forced to see it. [[arch-rule-convention-binds-only-authored-artifacts]]
