---
paths:
  - "**/*.tscn"
  - "**/*.tres"
  - "project.godot"
  - "UI/**/*.cs"
---

# Scene Authoring (`.tscn`) and Scene-vs-Programmatic Construction

**Context:** Godot scenes are the designer's primary canvas. When code creates nodes that *could* have been authored in the `.tscn`, designer tuning moves out of the Inspector into source — and `ClearSlots`-style teardown logic produces silent footguns.

## Scene philosophy

- **Heuristic:** Scenes are for **Representation** (Mesh, Audio, Collision), not Logic.
- **Logic Root:** The Root node usually holds the `MainController.cs`.
- **Wiring:** Prefer code-based wiring (`_Ready`) over `.tscn` signal connection syntax to keep logic searchable in C#.

## Scene anatomy — what a good scene looks like

The Inspector and scene tree are an API surface: exports and node structure carry the same contract discipline as public code. Each principle carries a litmus:

1. **Role-pure nodes.** Every node has one nameable role — if stating its job needs "and", split it. *Litmus:* can you state the node's job in one clause? *Exception (verified, not hypothesized):* two roles that never ship separately — checked against the actual roster — may share a component.
2. **Structure authored, behavior coded.** The scene owns what exists and where; code owns what happens. When choosing a node's creation path, load [`scene_construction.md`](../reference/scene_construction.md). Wiring split: anything with an automatic/procedural resolution (signal connections, component lookup) lives in code and stays invisible to the designer; the hand-authored remainder IS the designer surface — keep it minimal, obvious, and validated.
3. **One authored value, one home.** A second surface derives the value (computed property, stat resolution, lookup over the owning collection) — it never re-authors it. *Litmus:* if a designer changed this in one place and shipped, what still reads the old value? Any answer but "nothing" → derive. *Anti-shapes:* a sensor radius authored beside the stat that governs it; a parallel identity→asset dictionary beside the identity's own asset field; the same node block repeated across N sibling scenes.
4. **Every visible export is read.** An export inert in any context an author can reach is a defect, not clutter. *Litmus:* for every export reachable from this scene/`.tres`, name the code that reads it in THIS context; can't → delete it or narrow the type per context.
5. **Required dependency fails loud — three rungs.** (1) Authoring time: `_GetConfigurationWarnings` on every component with required deps/config (yellow triangle in the scene dock). (2) Load/initialize time: throw or `Error` once. (3) Lint time, where machine-checkable. A per-use WARNING is never the mechanism — it converts one config error into N noise lines indistinguishable from N failures. *Corollary:* any `X.Instance`-style system decides its ownership seam (scene node / autoload / lazily created) at design time and records it.
6. **Shared wiring lives in one scene.** At the second copy, extract to a template/inherited or instanced sub-scene — the forget-to-update failure arrives with the first divergent edit. Instancing constraint: instanced children are NOT configurable from the host scene (editable-children is not an accepted answer), so a shared scene puts every designer knob on its ROOT (forwarding exports or one config Resource); genuinely per-nested-node configuration → scene INHERITANCE, which overrides nested nodes in place. On inherited scenes, configure by overriding the template node — adding a sibling with the same role is a duplicate-provider defect, not a customization.
7. **The visual is a promise; the collider must keep it.** Whatever the art communicates about reach, timing, and growth is the contract the player plays against. If the visual changes size or position over its lifetime, the volume is driven over the same lifetime (shared curve/duration) — or the art is re-authored honest to the fixed volume. *Litmus:* at frame 1 and at the last frame, does the collider match what a player would predict from the art?

8. **A world-space visual is sized by the camera that sees it — and no test can see that.** `SpriteBase3D` and its `Label3D`/`Sprite3D` subclasses render `size × pixel_size` metres, depth-tested, with per-class defaults (`Label3D.pixel_size` 0.005, `SpriteBase3D` 0.01) chosen for neither this project's scale nor its orthographic camera. An unauthored world-space label is a fraction of a metre, occluded by the floor it spawns on, and **passes every suite that asserts its content, colour and lifecycle** — the failure is invisible to tests and to a reviewer reading two plausible numbers. Size it against a measured sibling already known readable in-game, and pin the DERIVED world height against a legibility floor rather than the authored number. *Litmus:* world height ÷ the run camera's `OrthoSize` — under ~2% is a handful of screen pixels. Derive or author world-space geometry (sizes, surface heights), never guess it. When entities and environment encode the pixel-to-metre anchor differently (a factor in the transform basis versus `pixel_size` at scale 1), read the encoding before comparing numbers, and size an attached visual by measuring its host's sprite height, never by copying the host's `pixel_size`.

**Decal receivers:** ground / terrain / floor / wall meshes that should receive shadows or aiming-preview decals set `layers = 3` (`Default | DecalReceiver`) on the `MeshInstance3D` in the `.tscn`. For entity meshes, decal nodes, typed constants or generated geometry, load [`visual_layers.md`](visual_layers.md).

**Resource diagnosis:** when a `.tres` loses properties, compare the removed values with the C# defaults, then check writer and DLL provenance and autoload closure before blaming the last build. Dictionary-export and uid-less picker traps: `design_litmus.md` #10.

## Clip, orientation or intent? (animation-vs-facing routing)

Disjoint authoring surfaces answer "what does the sprite look like while moving/aiming" — route the concern before touching any of them:

- **WHICH CLIP plays** → the claim/orchestrator surface: animation claims, the resolver's fallback profile, and the orchestrator's `DirectionSet` (null = one-directional base clips, the default; assign an 8-direction `DirectionSet3D` only when the scene genuinely authors 8-directional clip variants — suffixed clip names are the tell).
- **How that clip's ART is ORIENTED** → the facing surface: a `FacingProfile3D` (mirror channel = which way the sheet faces, rotation channel = neutral-angle offset) assigned to the consumer node (`FacingFlipController`, `HostFacingMirror3D`, or another facing consumer). Null profile = right-facing art, 0° offset.
- **WHICH WAY the entity faces**, so which direction bucket the `DirectionSet` picks → the intent surface: a State's `Face Target` setting. Face makes the state's subtree face the live primary target while fallback owns the body; Inherit (default) defers to ancestors; Don't Face stops an ancestor's facing for this subtree. `Face Target Key` optionally names another memory; empty (the common case) means the primary target. The intent reaches the body only through an `AnimationClaimResolver` with a fallback profile on the same entity; other wirings warn in the scene dock.

The surfaces never overlap: a facing profile never selects a clip, a `DirectionSet` never mirrors or rotates art, and a State's `Face Target` never picks a clip. A scene that "faces wrong" is a facing-surface fix; a scene that "plays the wrong animation" is a claim/clip fix. Rotation writers compose onto a cached authored basis (`node.Basis = cachedBasis * inPlane`) — never assign `Rotation` on a tilted node.

**Component contract (summary — canonical home: `architecture_philosophy` §Component Contract):** per-entity divergence is expressed by which components an entity composes plus exported Resource data — never structural rewiring of shared blocks. Inter-component dependencies are one-directional and always communicated: either an explicit exported slot the author must assign, or (house default) auto-resolution via Blackboard/ENCI paired with `_GetConfigurationWarnings` and a loud initialize.

## Project Settings (`project.godot`)

- **Source:** `project.godot` is a text file (INI format).
- **Rule:** Edit this file directly for Global/Project settings.
- **Autoloads:** Define under `[autoload]`. Ensure the C# class has `partial class` and `[GlobalClass]` if relevant, though Autoloads are usually scene-based or pure C# statics in this architecture. **An autoload scene references a content graph (dungeon/floor/encounter definitions — anything with scripted sub-resources) only as a `PropertyHint.File` path loaded at runtime, never as an `ext_resource`.** The editor instantiates autoloads at boot, before its C# registry is healthy, so every resource reachable from one deserializes bind-failed and the next flush writes it back stripped — the sole load root of the encounter-config strip. Exemplar: `AppLifecycleManager.DungeonPath`; shallow leaf resources (input profiles, audio defaults) stay allowed. Pinned by `AutoloadIsolationTests`.

## Touchpoints

- Companion: [`architecture_philosophy/SKILL.md`](../skills/architecture_philosophy/SKILL.md) covers the *logical* design patterns (Resource Strategy Hierarchies, Composable Configuration Resources) that drive the data-first preference.

<!-- retire-when: review-by: 2027-02-24 -->
