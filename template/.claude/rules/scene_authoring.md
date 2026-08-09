---
paths:
  - "**/*.tscn"
  - "**/*.tres"
  - "project.godot"
  - "UI/**/*.cs"
---

# Scene Authoring (`.tscn`) and Scene-vs-Programmatic Construction

**Context:** Godot scenes are the designer's primary canvas. When code creates nodes that *could* have been authored in the `.tscn`, designer tuning moves out of the Inspector into source — and `ClearSlots`-style teardown logic produces silent footguns. This rule codifies the scene-authoring preference, the programmatic carve-outs, and the UI-specific failure modes. Auto-loads on `.tscn` reads, `project.godot` reads, and edits under `UI/`.

## Scene philosophy

- **Heuristic:** Scenes are for **Representation** (Mesh, Audio, Collision), not Logic.
- **Logic Root:** The Root node usually holds the `MainController.cs`.
- **Wiring:** Prefer code-based wiring (`_Ready`) over `.tscn` signal connection syntax to keep logic searchable in C#.

## Scene anatomy — what a good scene looks like

The Inspector and scene tree are an API surface: exports and node structure carry the same contract discipline as public code. Seven principles, each with a litmus:

1. **Role-pure nodes.** Every node has one nameable role — if stating its job needs "and", split it. *Litmus:* can you state the node's job in one clause? *Exception (verified, not hypothesized):* two roles that never ship separately — checked against the actual roster — may share a component.
2. **Structure authored, behavior coded.** The scene owns what exists and where; code owns what happens. Wiring split: anything with an automatic/procedural resolution (signal connections, component lookup) lives in code and stays invisible to the designer; the hand-authored remainder IS the designer surface — keep it minimal, obvious, and validated.
3. **One authored value, one home.** A second surface derives the value (computed property, stat resolution, lookup over the owning collection) — it never re-authors it. *Litmus:* if a designer changed this in one place and shipped, what still reads the old value? Any answer but "nothing" → derive. *Anti-shapes:* a sensor radius authored beside the stat that governs it; a parallel identity→asset dictionary beside the identity's own asset field; the same node block repeated across N sibling scenes.
4. **Every visible export is read.** An export inert in any context an author can reach is a defect, not clutter (canon incident: `arch_rule_shared_config_resource_no_dead_exports` in auto-memory). *Litmus:* for every export reachable from this scene/`.tres`, name the code that reads it in THIS context; can't → delete it or narrow the type per context.
5. **Required dependency fails loud — three rungs.** (1) Authoring time: `_GetConfigurationWarnings` on every component with required deps/config (yellow triangle in the scene dock). (2) Load/initialize time: throw or `Error` once. (3) Lint time, where machine-checkable. A per-use WARNING is never the mechanism — it converts one config error into N noise lines indistinguishable from N failures. *Corollary:* any `X.Instance`-style system decides its ownership seam (scene node / autoload / lazily created) at design time and records it.
6. **Shared wiring lives in one scene.** At the second copy, extract to a template/inherited or instanced sub-scene — the forget-to-update failure arrives with the first divergent edit. Instancing constraint: instanced children are NOT configurable from the host scene (editable-children is not an accepted answer), so a shared scene puts every designer knob on its ROOT (forwarding exports or one config Resource); genuinely per-nested-node configuration → scene INHERITANCE, which overrides nested nodes in place. On inherited scenes, configure by overriding the template node — adding a sibling with the same role is a duplicate-provider defect, not a customization.
7. **The visual is a promise; the collider must keep it.** Whatever the art communicates about reach, timing, and growth is the contract the player plays against. If the visual changes size or position over its lifetime, the volume is driven over the same lifetime (shared curve/duration) — or the art is re-authored honest to the fixed volume. *Litmus:* at frame 1 and at the last frame, does the collider match what a player would predict from the art?

**Component contract (summary — canonical home: `architecture_philosophy` §Component Contract):** per-entity divergence is expressed by which components an entity composes plus exported Resource data — never structural rewiring of shared blocks. Inter-component dependencies are one-directional and always communicated: either an explicit exported slot the author must assign, or (house default) auto-resolution via Blackboard/ENCI paired with `_GetConfigurationWarnings` and a loud initialize.

## Programmatic vs Scene-Tree Node Construction

**Rule:** Prefer scene-tree authoring for Godot nodes that meet BOTH criteria:
1. **Fixed count per parent** (exactly one, known at design time)
2. **Has designer-tunable parameters** (radius, layers, shapes, ranges, references)

**Programmatic construction is legitimate for:**
- Variable-count runtime composition (spell effect providers, per-wave enemies)
- Ephemeral per-event nodes (explosion anchors, transient VFX spawners)
- Pool components requiring rebuild on reset
- Shape/collision clones from already-resolved runtime data (e.g., copying a spell's hitbox shape to a force area)
- Runners that need internal Node features (Timers, Tweens) attached to ephemeral runtime entities (status effect runners)

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
- Buttons, labels, progress bars, prompt displays with **known slots** (e.g., "4 glyphs: select/confirm/undo/cancel") — authored.
- Persistent HUD widgets (health bar, instability bar, prompt rows) — authored or instanced from a scene-authored `PackedScene`, **not** spawned by a C# installer that the designer can't see or inspect.
- Programmatic creation is reserved for **truly variable-count** content: inventory slot ring where N depends on session state, per-enemy health floaters, runtime toast notifications, per-selection icons.

**PackedScene middle ground:** when a widget needs to appear N times (N runtime-known) or be reusable across scenes, author it as its own `.tscn` and instance via `[Export] PackedScene` — designer still tunes the template, code owns the replication count.

## Teardown-whitelist anti-pattern (UI diagnostic)

If a container has a `ClearSlots()`-style method that iterates children and frees non-whitelisted ones, every new **programmatic persistent child** is a footgun. Example bite: `RadialCraftingWheel` whitelisted 4 scene-authored children; the 5th (programmatically-created `_glyphPromptsRow`) fell through to `child.Free()` on every Close→Open cycle, and `_selectDisplay.PlayPressFeedback()` then threw `ObjectDisposedException` on the next press. **The fix is NOT extending the whitelist** — that just defers the problem to the 6th child. The correct fixes are:

1. **Scene-author the persistent child** (its `Owner` points at the scene root, naturally distinguishing it from transient children).
2. **Filter by Owner**: `foreach (var child in GetChildren()) if (child.Owner == null) child.Free();` — scene-authored = `Owner` set by scene loader; programmatic = `Owner` null unless explicitly set. No whitelist to maintain.

## Canonical UI incidents (all resolved by scene-authoring migration)

1. **Instability bar spawner** — designer couldn't verify placement or style without running the game. Fix: instance the `.tscn` directly in the arena scene.
2. **C3 glyph prompt row** (2026-04-22) — `BuildGlyphPromptsRow()` created a row + 4 `InputPromptDisplay` children at `_Ready`. Fell out of `ClearSlots()`'s whitelist → `ObjectDisposedException`. Fix: scene-author all 5 nodes in `radial_crafting_wheel.tscn`, designer now tunes row position in Inspector.
3. **Overworld prompt panel** (2026-04-22) — `BuildChildren()` created 3 `InputPromptDisplay` Control children programmatically. When the inner widget was still a `Control` wrapper, minsize didn't propagate → zero-width collapse, all three prompts overlapped at x=0. Fix: scene-author the 3 children + make `InputPromptDisplay` inherit `HBoxContainer` directly.

## Decision heuristic (UI-extended)

Before `AddChild(new Control ...)` / `AddChild(new Label ...)` / similar, ask:
1. Is this node's existence known at design time? If yes → scene-author.
2. Would a designer reasonably want to reposition, resize, or restyle this? If yes → scene-author (ideally with per-slot Inspector exports like `StaticLabelOverride`).
3. Does this node persist across session/state cycles beside teardown logic? If yes → scene-author (avoids whitelist maintenance).

If all three are "no" (truly variable-count, purely internal, purely transient), programmatic is correct.

**Scene-authored ≠ no code.** Code still wires runtime behavior: `BindToProfile`, event subscriptions, tween animations, press-feedback, dynamic label overrides. **The scene owns structure; code owns behavior.** Scripts should *find* nodes (via `[Export]` refs or `NodeExts`), not *create* them when they're design-time-known. Per-slot scene-authored exports (like `[Export] string StaticLabelOverride`) push even more configuration into the Inspector, further reducing runtime wiring code.

## Project Settings (`project.godot`)

- **Source:** `project.godot` is a text file (INI format).
- **Rule:** Edit this file directly for Global/Project settings.
- **Autoloads:** Define under `[autoload]`. Ensure the C# class has `partial class` and `[GlobalClass]` if relevant, though Autoloads are usually scene-based or pure C# statics in this architecture.

## Touchpoints

- `RadialCraftingWheel` — incident #2.
- `OverworldPromptPanel` — incident #3.
- `InstabilityBar` — incident #1.
- Companion: [`architecture_philosophy/SKILL.md`](../skills/architecture_philosophy/SKILL.md) covers the *logical* design patterns (Resource Strategy Hierarchies, Composable Configuration Resources) that drive the data-first preference.
