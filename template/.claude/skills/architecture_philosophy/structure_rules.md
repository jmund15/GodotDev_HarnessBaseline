# Project Structure Rules

> Project-owned seed, and the rulebook for the project's structure audit where it ships one. Fill the `organization` field in
> [`project_subsystems`](../project_subsystems/SKILL.md); that registry is the folder-style map.

This file answers: **Where should this new file live?** `SKILL.md` owns code design.

## Organizational model

Each subsystem declares one style:

| Style | Use when | Subfolders mean |
|---|---|---|
| `feature` | independently authored content | one feature/entity; co-locate code, scenes, data, and assets |
| `layer` | shared engine or domain machinery | one role, such as `Foundation/`, `Behavior/`, or `Reactions/` |
| `ui` | presentation controls | one consuming subsystem |
| `hybrid` | a documented mix that cannot use one rule | define the allowed mix in that subsystem's details |

Do not infer style from a folder name. Read the machine-readable subsystem registry. A missing
`organization` value is an audit finding, not permission to guess.

## Rules

### Tier 1 — mechanical

**R1. Folder casing matches the declared project convention**
- Project-owned system folders use one consistent convention, normally `PascalCase` for C# Godot projects.
- Infrastructure folders use their established lowercase names, such as `addons/`, `logs/`, and `script_templates/`.
- A consumer may replace the project-folder convention here; the audit applies the stated value.

**R2. File casing follows extension convention**
- `.cs`: `PascalCase`.
- `.tscn` and `.tres`: `snake_case`.

**R3. Keep the project root free of loose artifacts**

Allowed: build/engine manifests, top-level subsystem and infrastructure folders, `README.md`,
`LICENSE`, and `CHANGELOG.md`.

Forbidden: `*.old`, `*.bak`, `tmp*`, `*-cwd`, `nul`, generated reports, loose `.tscn`/`.tres`,
and plan drafts that belong in the project's design-doc or `.claude/plans/` store.

**R4. Tests live under `Tests/`**
- `*Test.cs`, `*TestSuite.cs`, and `*Tests.cs` live under `Tests/Logic/`, `Tests/Integration/`, or `Tests/Sanity/`.
- Shared test support lives under `Tests/Framework/`.

**R5. Scene-attached scripts have companion `.cs.uid` files**

Every `.cs` referenced as an `ext_resource` in a scene/resource has a sibling `.cs.uid`.

**R13. C# namespaces mirror project-relative folders**
- Expected namespace = the registry's `conventions.namespace_root` plus PascalCase-folded folder segments.
- Compare case-insensitively; R1 owns folder casing.
- Keep the folder-to-namespace alias table empty unless a consumer records a justified exception below.
- If a type name equals the namespace leaf (`Example.Example`), rename the type rather than leaving it in the global namespace.
- If a namespace shadows a framework type, qualify the framework type at the colliding call sites.
- Namespace changes are ASK, never an automatic FIX; they can touch every consumer `using`.
- Treat leaf-collapse and foreign namespaces as low-confidence ASK. A framework `partial class`
  extension may legitimately use the framework namespace from a project-owned folder.
- Exclude tests, dependencies, generated output, plugins, and tool-only roots listed by the consumer.

#### Folder → namespace aliases

Justified exceptions come from `adaptation.json` `structure_exceptions` — each `{path, reason}`.
Prefer renaming the folder or namespace over adding one.

| Folder root | Namespace root | Why renaming is not viable |
|---|---|---|

### Tier 2 — judgment

**R6. Feature folders co-locate their resources**
- A `feature` subsystem keeps each feature's `.cs`, `.tscn`, scene-exclusive `.tres`, and assets together.
- Do not split one feature into `Scripts/`, `Scenes/`, and `Resources/`.
- Hoist resources shared by two or more features to a documented shared sibling.

**R7. Layer folders stay layered**
- A `layer` subsystem groups by role, not by content feature.
- A feature-specific implementation belongs in its owning `feature` subsystem.

**R8. UI lives under the `ui` subsystem by default**

A UI class may stay outside that root only when it:
1. mutates private subsystem state,
2. is tightly bound to non-public subsystem types, or
3. is world-space UI embedded in an entity scene.

Reading via signals/events is not coupling. Scene-embedded UI is not screen-space UI.

**R9. A single-file folder needs a reason**

Flatten it, move it beside its siblings, or record the concrete sibling-growth signal. The audit asks;
it does not guess future growth.

**R10. Flat layer folders do not mix concerns**

Mixed code, scenes, data, and assets are expected in a `feature` folder. The same mix in a `layer`
folder needs role subfolders or relocation. A `hybrid` subsystem follows its documented details.

**R14. Persistence files co-locate with their owning domain**
- A save repository and its persisted data live with the domain that owns them.
- Shared serialization primitives belong in the reusable framework/library.
- A cross-domain aggregate is ASK because ownership is a design decision.

### Tier 3 — boundaries

**R11. Reusable framework code must not reference the project namespace**
- Apply this rule to every root in the registry's `conventions.framework_paths`.
- Project defaults enter through a framework-owned seam populated by project startup code.

**R12. Top-level folders match `project_subsystems`**
- Every project-owned top-level folder appears in a subsystem `paths:` list.
- Each subsystem declares `organization: feature|layer|ui|hybrid`.
- Add the registry row in the same change that creates a top-level folder.
- The registry's reserved list owns infrastructure exclusions.

## Applying the rules

1. Read the subsystem registry and find the owning path and `organization` value.
2. Apply R1–R5 and R13.
3. Apply R6, R7, R8, or R10 from the declared style.
4. For a new top-level folder, update the registry in the same change.
5. Use ASK for ownership, namespace, hybrid-style, and growth judgments.

Run the project's structure audit, where it ships one, after a structural change or as an explicit hygiene pass.
