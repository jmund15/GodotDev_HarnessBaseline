# Project structure rules

This file answers one question: where does a new file live? `SKILL.md` owns code design. The folder-style map is data: each subsystem's `organization` field in [`project_subsystems`](../project_subsystems/SKILL.md). Stack rules (file casing by extension, engine companion files, namespace-to-folder mapping) are project files the repo's `CLAUDE.md` names; this file holds the rules every stack shares. Justified exceptions to any rule come from `adaptation.json` `structure_exceptions`, each `{path, reason}`; prefer moving the file over adding one.

## Organizational model

Each subsystem declares one style:

| Style | Use when | Subfolders mean |
|---|---|---|
| `feature` | independently authored content | one feature or entity; its code, data and assets together |
| `layer` | shared machinery | one role, such as foundation, behaviour, persistence |
| `ui` | presentation | one consuming subsystem |
| `hybrid` | a documented mix no single style fits | the mix that subsystem's details define |

Never infer a style from a folder name: read the registry. A subsystem with no `organization` is a finding, not permission to guess. The style follows who navigates the folder most: people working by entity read feature folders, people working by role read layer folders.

## Rules

### Mechanical

- **R1. One folder-casing convention** for project folders; infrastructure folders keep their established names.
- **R3. The project root holds no loose artifacts:** no `*.old`, `*.bak`, `tmp*`, `nul`, generated reports or plan drafts. Allowed: build manifests, top-level subsystem and infrastructure folders, README, LICENSE and CHANGELOG files.
- **R4. Tests live under the project's tests root** (`adaptation.json` `tests_root`), shared test support in its framework folder.

R2, R5 and R13 are stack rules and live in the project's own file.

### Judgment

- **R6. A feature folder co-locates its files.** Never split one feature across type folders such as Scripts, Scenes and Resources. Hoist a file two features share to a documented shared sibling.
- **R7. A layer folder stays layered:** grouped by role, never by feature. A feature-specific implementation belongs in its feature subsystem.
- **R8. UI lives under the `ui` subsystem** unless it mutates private subsystem state, binds to non-public subsystem types, or is embedded in an entity's own scene or view. Reading through events is not coupling.
- **R9. A single-file folder needs a reason:** flatten it, move it beside its siblings, or name the concrete growth signal.
- **R10. A flat layer folder does not mix concerns.** Mixed code, data and assets are normal in a feature folder; in a layer folder they need role subfolders.
- **R14. Persistence co-locates with its owning domain;** shared serialization primitives go in the reusable framework. A cross-domain aggregate is an owner question.

### Boundaries

- **R11. Reusable framework code never references the project.** Applies to every root in the registry's `conventions.framework_paths`; project defaults enter through a seam project startup fills.
- **R12. Top-level folders match the registry.** Every project top-level folder appears in a subsystem's `paths:`; add the registry row in the change that creates the folder.

## Placing a file

1. Find the owning subsystem and its `organization` in the registry.
2. Apply R1, R3, R4 and the project's stack rules.
3. Apply R6, R7, R8 or R10 by the declared style.
4. A new top-level folder updates the registry in the same change.
5. Ask the owner about ownership, hybrid style and growth judgments.
