---
name: arch-rule-convention-binds-only-authored-artifacts
description: "A convention enforced at the authoring surface (.tscn/.tres/attribute) binds only artifacts the project authors — generated, vendored, or server-level paths are outside it by construction and fail silently"
metadata: 
  node_type: memory
  type: project
  modified: 2026-08-18T01:22:10.944Z
---

A project convention enforced at the authoring surface — a scene-file field, a resource field, an attribute on a declared type — binds only artifacts the project itself authors. Anything produced by a generator, a vendored addon, or a direct server/RID call has no authoring surface to carry the opt-in, so it is outside the convention **by construction**. No sweep of the authored files can reach it, and its failure mode is the inverse of the convention's designed one: nothing is missing from any file a reviewer reads, so the defect is silent and can survive the whole life of the feature.

**Why:** conventions are usually designed around a loud failure ("author forgets the opt-in → visibly wrong at playtest"), and that loudness argument is only valid inside the authored set. Stating the failure mode without scoping it to that set makes the uncovered class look covered, which is worse than having no convention there at all.

**How to apply:** when writing or reviewing a convention, name the set it binds and name the render/spawn/registration paths that sit outside it, each with the equivalent knob at its own level (e.g. `RenderingServer.instance_set_layer_mask` is the RID-level `VisualInstance3D.layers`). Give the outside set its own audit, not a line in the sweep. Treat "generated / vendored / server-level" as the recurring shape of that outside set. [[arch-rule-world-space-constant-hides-geometry-assumption]]

**Verified:** 2026-09-04 memory-claim audit — `RenderingServer.xml` documents `instance_set_layer_mask`; `VisualInstance3D.xml:59` `layers` has `setter="set_layer_mask"`.
