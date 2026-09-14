---
name: Project Subsystems
description: >-
  Auto-load when scoping work across subsystems or finding which subsystem owns a path.
  Skip symbol lookup, file-placement rules, reusable-framework internals, and game vision.
---

# {{PROJECT_NAME}} — Subsystem Registry

<!-- PROJECT-OWNED SEED. Replace placeholders as the project grows. The YAML block is
     machine-read by /sync_subsystems and /structure_audit; keep its field shape. -->

## Registry (machine-readable)

- `id` is stable.
- `paths` contains project-relative ownership roots.
- `organization` is `feature`, `layer`, `ui`, or `hybrid` as defined by
  [`structure_rules.md`](../architecture_philosophy/structure_rules.md).
- `domain` is the default `logic`, `gameplay`, `data`, `meta`, or `framework` PR/test route; classify
  changed behavior when a subsystem mixes domains.
- `summary` is one line. Put boundaries and exceptions under *Subsystem Details*.

```yaml
conventions:
  project_folder_case: PascalCase
  namespace_root: "{{PROJECT_NAME}}"
  framework_paths: [<FrameworkRoot/>]
  playtest_scenario_paths: [<ScenarioRoot/>]
  reserved_paths: [.claude/, .git/, .godot/, addons/, bin/, obj/, Tests/]
subsystems:
  - id: <subsystem-id>
    paths: [<TopLevelFolder/>, <OtherFolder/>]
    organization: <feature|layer|ui|hybrid>
    domain: <logic|gameplay|data|meta|framework>
    summary: <one-line ownership gloss>
```

## Subsystem Details

### <subsystem-id>

<Ownership boundaries, key types, invariants, organization exceptions, and exclusions.>
