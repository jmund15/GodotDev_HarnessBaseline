---
paths:
  - "**/*.tscn"
  - "**/*.tres"
  - "project.godot"
---

# Scene Authoring (`.tscn`) and Scene-vs-Programmatic Construction

**Context:** Godot scenes are the designer's primary canvas. When code creates nodes that could have been authored in the `.tscn`, designer tuning moves out of the Inspector and teardown code gains hidden ownership cases. This rule owns scene structure, programmatic carve-outs, and scene-backed UI.

## Scene philosophy

- **Heuristic:** Scenes are for **Representation** (Mesh, Audio, Collision), not Logic.
- **Logic Root:** The Root node usually holds the `MainController.cs`.
- **Wiring:** Prefer code-based wiring (`_Ready`) over `.tscn` signal connection syntax to keep logic searchable in C#.

## Scene anatomy — what a good scene looks like

The Inspector and scene tree are an API surface: exports and node structure carry the same contract discipline as public code. Each principle carries a litmus:

1. **Role-pure nodes.** Every node has one nameable role — if stating its job needs "and", split it. *Litmus:* can you state the node's job in one clause? *Exception (verified, not hypothesized):* two roles that never ship separately — checked against the actual roster — may share a component.
2. **Structure authored, behavior coded.** The scene owns what exists and where; code owns what happens. Wiring split: anything with an automatic/procedural resolution (signal connections, component lookup) lives in code and stays invisible to the designer; the hand-authored remainder IS the designer surface — keep it minimal, obvious, and validated.
3. **One authored value, one home.** A second surface derives the value (computed property, stat resolution, lookup over the owning collection) — it never re-authors it. *Litmus:* if a designer changed this in one place and shipped, what still reads the old value? Any answer but "nothing" → derive. *Anti-shapes:* a sensor radius authored beside the stat that governs it; a parallel identity→asset dictionary beside the identity's own asset field; the same node block repeated across N sibling scenes.
4. **Every visible export is read.** An export inert in any context an author can reach is a defect, not clutter (canon incident: `arch_rule_shared_config_resource_no_dead_exports` in auto-memory). *Litmus:* for every export reachable from this scene/`.tres`, name the code that reads it in THIS context; can't → delete it or narrow the type per context.
5. **Required dependency fails loud — three rungs.** (1) Authoring time: `_GetConfigurationWarnings` on every component with required deps/config (yellow triangle in the scene dock). (2) Load/initialize time: throw or `Error` once. (3) Lint time, where machine-checkable. A per-use WARNING is never the mechanism — it converts one config error into N noise lines indistinguishable from N failures. *Corollary:* any `X.Instance`-style system decides its ownership seam (scene node / autoload / lazily created) at design time and records it.
6. **Shared wiring lives in one scene.** At the second copy, extract to a template/inherited or instanced sub-scene — the forget-to-update failure arrives with the first divergent edit. Instancing constraint: instanced children are NOT configurable from the host scene (editable-children is not an accepted answer), so a shared scene puts every designer knob on its ROOT (forwarding exports or one config Resource); genuinely per-nested-node configuration → scene INHERITANCE, which overrides nested nodes in place. On inherited scenes, configure by overriding the template node — adding a sibling with the same role is a duplicate-provider defect, not a customization.
7. **The visual is a promise; the collider must keep it.** Whatever the art communicates about reach, timing, and growth is the contract the player plays against. If the visual changes size or position over its lifetime, the volume is driven over the same lifetime (shared curve/duration) — or the art is re-authored honest to the fixed volume. *Litmus:* at frame 1 and at the last frame, does the collider match what a player would predict from the art?

8. **Size world-space visuals against the camera that renders them.** Engine defaults do not know the project's unit scale or camera framing, and content/lifecycle tests cannot judge legibility. Compare a new `SpriteBase3D` or `Label3D` with a known-readable sibling in the real camera, then test a derived world-size or screen-coverage contract rather than the raw authored number.

**Component contract (summary — canonical home: `architecture_philosophy` §Component Contract):** per-entity divergence is expressed by which components an entity composes plus exported Resource data — never structural rewiring of shared blocks. Inter-component dependencies are one-directional and always communicated: either an explicit exported slot the author must assign, or (house default) auto-resolution via Blackboard/ENCI paired with `_GetConfigurationWarnings` and a loud initialize.

## Programmatic vs Scene-Tree Node Construction

**Rule:** Prefer scene-tree authoring for Godot nodes that meet BOTH criteria:
1. **Fixed count per parent** (exactly one, known at design time)
2. **Has designer-tunable parameters** (radius, layers, shapes, ranges, references)

**Programmatic construction is legitimate for:**
- Variable-count runtime composition
- Ephemeral per-event nodes such as transient visual anchors
- Pool components requiring rebuild on reset
- Shape/collision clones from already-resolved runtime data
- Runners that need internal Node features such as Timers or Tweens on ephemeral runtime objects

**Anti-pattern:** programmatic construction of designer-tunable fixed-per-parent infrastructure (e.g., a detection `Area3D` with hardcoded radius on a component's parent). Moves designer tuning out of Inspector into code edits. Breaks `ValidateRequiredExports` gating (code-constructed children skip `_Ready()` validation contracts). Invisible to Inspector → breaks data-first iteration.

**Scene-tree authoring wins:**
- Visible hierarchy in editor (designer sees what's there)
- Inspector-editable parameters (no recompile to tune)
- Proper `_Ready()` lifecycle (export validation fires)
- Children automatically scoped by `EntityNodeComponentsInitializer` (IBlackboardProvider, IComponent)

**Catch-yourself cue:** writing `new Area3D()` / `new CollisionShape3D()` in an installer? Re-check the two criteria above before proceeding.

## UI-specific corollary

This rule bites hardest in UI because the `Control` tree IS the designer's primary canvas. Every `Control`/`Container` whose existence is known when the scene opens belongs in the `.tscn`, not in `BuildChildren()` called from `_Ready`:

- Panels, containers, layout anchors, chip backgrounds — authored.
- Buttons, labels, progress bars, and prompt displays with known slots — authored.
- Persistent widgets — authored or instanced from a scene-authored `PackedScene`, not spawned by an installer hidden from the designer.
- Programmatic creation is reserved for truly variable-count content such as a data-sized list, runtime notification, or per-selection icon.
- **Design-time-known `Control`s are authored, full stop.** Programmatic children are invisible to the designer, collapse under container sizing, and fall off teardown whitelists into `ObjectDisposedException` (`feedback_scene_compose_over_programmatic_ui`).

**PackedScene middle ground:** when a widget needs to appear N times (N runtime-known) or be reusable across scenes, author it as its own `.tscn` and instance via `[Export] PackedScene` — designer still tunes the template, code owns the replication count.

## Teardown-whitelist anti-pattern (UI diagnostic)

If a container clears every child except a name/type whitelist, each new persistent programmatic child can be freed on a later open/close cycle. Do not extend the whitelist; choose one ownership rule:

1. **Scene-author the persistent child** (its `Owner` points at the scene root, naturally distinguishing it from transient children).
2. **Filter by Owner**: `foreach (var child in GetChildren()) if (child.Owner == null) child.Free();` — scene-authored = `Owner` set by scene loader; programmatic = `Owner` null unless explicitly set. No whitelist to maintain.

## Decision heuristic (UI-extended)

Before `AddChild(new Control ...)` / `AddChild(new Label ...)` / similar, ask:
1. Is this node's existence known at design time? If yes → scene-author.
2. Would a designer reasonably want to reposition, resize, or restyle this? If yes → scene-author (ideally with per-slot Inspector exports like `StaticLabelOverride`).
3. Does this node persist across session/state cycles beside teardown logic? If yes → scene-author (avoids whitelist maintenance).

If all three are "no" (truly variable-count, purely internal, purely transient), programmatic is correct.

**Scene-authored does not mean code-free.** Code still wires runtime behavior, subscriptions, animations, and dynamic values. Scripts find nodes through exported references or `NodeExts`; they do not create design-time-known structure.

## Project Settings (`project.godot`)

- **Source:** `project.godot` is a text file (INI format).
- **Rule:** Edit this text file directly for project-wide settings.
- **Autoloads:** Define them under `[autoload]`. Keep scripted content graphs out of autoload scene `ext_resource` closures; load them by path at runtime. The editor instantiates autoloads before every C# resource type is guaranteed healthy, so a failed bind can be written back as stripped data. Pin this with a test that rejects scripted content-graph resources reachable from an autoload scene.

## Touchpoints

- [`architecture_philosophy/SKILL.md`](../skills/architecture_philosophy/SKILL.md) covers the logical design patterns that drive the data-first preference.
