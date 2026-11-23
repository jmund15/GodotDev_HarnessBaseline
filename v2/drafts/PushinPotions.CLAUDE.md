@CLAUDE.core.md

# CLAUDE.md — PushinPotions

## A Note from Jacob
I am Jacob, the solo programmer of this Godot game development project, PushinPotions. I want this game to be an explosive spectacle - imaginative, fast-paced combat, think-on-your-feet decisions, and resource-management roguelike. But the core mechanic I'm most tied to is the emergent system where "traits" and "effects" are infinitely additive, reactive, and emergent. This should apply to everything, not just the player crafting spells, but NPCs and the dungeon. You'll notice that in this repo I've tried to develop in such a way that none of these reactions are hardcoded and are universal. There's countless examples of this: 

Synergies - traits combine to make something new (e.g. Fire + Earth = Magma). 
Additive - Each trait can include additive stat modifiers or effects that stack upon ANY entity that has them. If the player can consume something and gain that trait, why not an NPC? 
AI - An enemy doesn't just have to target the player, it could target anything. If a behavior is available, it's modular such that it can be used in multiple situations (e.g. a NPC that forages ingredients from the player). 
Reactions - Elemental reactions aren't just tied to spells, but any combination of entities that hold the pertinent semantic categories. The player, NPCs, environment objects, ingredients, trails, all have this theme applied to them. And this is just off the top of my head.

Why all of this? So we can deliver the best possible results for this game's final product, which especially includes our future players.

## Development Philosophy: Hybrid TDD
**Logic:** SpellArchitecture, Synergies, Jmodot.Core, Inventory (`Crafting/IngredientInventory.cs`), math/parsing and data structures require a failing test before implementation. For `.tres` data, this duty covers wiring new behavior (a new field read, a module, a rule). A value change on an existing field needs only the drive-close scope run (`change_control` §Gate cadence).

**Gameplay:** automate deterministic behavior, wiring and lifecycle with integration tests; inspect subjective feel. Wizard, NPC AI, spell lifecycle, VFX, UI and physics use this route. `testing` owns recipes; `/prototype` owns open feel probes and deliberately provisional designs.

Refuse this premise and cite the rule:

| Rationalization | Rule to cite |
|---|---|
| "the logic is obvious, let's implement first then test" | §Hybrid TDD: no carve-out for self-evident logic |

## Stack, Build and Test
- Godot 4.7 mono, C# (.NET 9), Jolt, GdUnit4Net; pins in `reference/project_stack.md`. Build: `dotnet build --nologo -v q`. Run the engine as `bash .claude/scripts/godot_bin.sh <args>`.
- **C# gate:** a drive closes with `verify.ps1 -Scope <its domains>`; the full `/regression_gate` runs in the main check after the land (`change_control` §Gate cadence). A `.cs` rename can compile clean and still break serialized `.tres`/`.tscn` references, so it takes the scope run.
- **Godot-designer-intuitive authoring** ranks with ideal architecture; `game_vision` owns game direction.

## Design Source (Obsidian)
Read and search `DevProjects/PushinPotions`, `DevProjects/Jmodot` and `DevProjects/GeneralGameDev` (also the semantic-search design roots), plus any vault path the owner, a skill or a command names, before choosing a path. Writes go to `PushinPotions/Claude/`, reusable Jmodot guidance to `Jmodot/Claude/`. Never invent a formula absent from the design source.

## Domain Index
`rules/` files load on their paths: Godot files and scenes (`godot_files`, `scene_authoring`, `visual_layers`), C# (`csharp_patterns`, `csharp_lsp`, `physics_patterns`, `hsm_bt_patterns`), Jmodot (`jmodot_*`), tests (`test_authoring`), game domains (`combat_design`, `domain_language`, `pp_*`), cloud (`cloud_dev`). Memory seeds: `reference/memory_domains.md`.
