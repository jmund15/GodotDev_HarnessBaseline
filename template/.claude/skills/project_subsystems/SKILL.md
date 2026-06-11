---
name: Project Subsystems
description: >-
  Auto-load when scoping subsystem breadth, navigating across 2+ subsystems, or asking
  "where does X live / who owns Y". Triggers: "subsystem", "cross-cutting", "scope this
  change", "where does X live", "what owns X". SKIP for symbol lookups (semantic-search /
  LSP), file placement (`structure_rules`), patterns (`architecture_philosophy`), Jmodot
  internals (`jmodot`), game vision (`game_vision`).
---

# {{PROJECT_NAME}} — Subsystem Registry

<!-- SEED TEMPLATE — this skill is project-owned. Populate the registry as your
     project grows; `/sync_subsystems` proposes updates when the top-level folder
     shape changes. The YAML block below is machine-read — keep its field shape. -->

## Registry (machine-readable)

Consumed by `/sync_subsystems` and the `architecture_brainstorm` subsystem-breadth scope-litmus (≤2 subsystems per implementation session — multi-subsystem work needs a design pass first). `id` is the stable identifier; `paths` is the breadth-calculation token set. `summary` is a one-line at-a-glance gloss — full prose lives in *Subsystem Details* below.

```yaml
subsystems:
  - id: <subsystem-id>
    paths: [<TopLevelFolder/>, <OtherFolder/>]
    summary: <one-line gloss>
  - id: ai
    paths: [AI/]
    summary: Behavior Trees, HSM substrate, blackboard, agent entities.
  - id: movement
    paths: [Movement/]
    summary: MovementProcessor3D, external force receivers, friction strategies (Jmodot-backed).
  - id: stats
    paths: [Stats/]
    summary: Stat sheets and controller configs (Jmodot Attribute + Modifier pipeline).
  - id: visual
    paths: [Visual/, Animation/]
    summary: VFX controllers, sprite/animation orchestrators.
  - id: ui
    paths: [UI/]
    summary: HUD and menus.
```

## Subsystem Details

### <subsystem-id>
<ownership boundaries, key types, invariants, what does NOT belong here.>
