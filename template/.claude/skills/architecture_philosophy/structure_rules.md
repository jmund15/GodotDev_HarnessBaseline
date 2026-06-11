# Project Structure Rules

> Codified ruleset for project file/folder organization. Authoritative source for `/structure_audit` and any future structure tooling. Companion to [SKILL.md](SKILL.md).

This file answers the question: **"Where should this new file live?"**

It does *not* replace SKILL.md. SKILL.md covers code-level architectural patterns (HSM, Resources, Blackboard). This file covers physical layout (folders, naming, co-location, boundaries).

---

## Organizational Philosophy: Hybrid

{{PROJECT_NAME}} deliberately uses **two organizational styles** depending on what a folder contains. Honoring this duality is the single most important rule.

| Style | When | Top-level examples | Subfolders are |
|-------|------|---------------------|----------------|
| **Feature-based** | The folder holds **content** — discrete game entities, each independently authored | `Spells/`, `Ingredients/`, `Spells/Fire/`, `Ingredients/Apple/` | One per entity (Fire, Ice, Apple). Each subfolder co-locates its `.cs` + `.tscn` + scene-exclusive `.tres` |
| **Layer-based** | The folder holds **engine code** — roles/layers of a single system | `SpellArchitecture/`, `AI/`, `Movement/`, `Combat/`, `Stats/`, `Animation/` | One per role (Foundation, Behavior, Reactions). Per-feature subfolders are NOT introduced here |
| **UI-by-subsystem** (special) | The folder holds presentation Controls | `UI/` | One per subsystem (Player/, Crafting/, Game/) |

### Why not pick one style globally?

Feature-based wins for content because designers and content-creators navigate by *entity*: "show me the Fire spell" should land in one folder. Layer-based wins for engine code because engineers navigate by *role*: "show me how spells initialize" should land in `SpellArchitecture/Foundation/`. Forcing either onto the other inverts the navigation cost for the people who actually use that folder most.

---

## The Rules

Rules are organized into three tiers matching the audit's FIX / ASK / PLAN action tiers. Tier-1 violations are mechanically fixable. Tier-2 violations need judgment. Tier-3 violations are framework-critical.

### Tier 1 — Mechanical (FIX-tier when violated)

**R1. Folder casing matches layer convention**
- Game-system folders use `PascalCase` (e.g., `SpellArchitecture/`, `Wizard/`, `UI/`).
- Infrastructure/tooling folders use lowercase (e.g., `addons/`, `logs/`, `script_templates/`).
- *Why:* Established by CLAUDE.md ("Files: `snake_case` directories, `PascalCase` files/classes" — interpreted as "snake_case OR PascalCase consistently per layer", with the project converging on PascalCase for game systems).

**R2. File casing follows extension convention**
- `.cs` files: `PascalCase` (e.g., `SpellCrafter.cs`, `WaveHUD.cs`).
- `.tscn` and `.tres` files: `snake_case` (e.g., `wave_hud.tscn`, `fire_x2.tres`).
- *Why:* Godot community convention; matches CLAUDE.md.

**R3. No clutter at project root**
Project root permits only:
- `.cs` / `.csproj` / `.sln` (build manifests)
- `project.godot`, `icon.png/svg`, `*.import` (Godot manifests)
- Top-level system folders, infrastructure folders, `Jmodot/`, `addons/`
- `README.md`, `LICENSE`, `CHANGELOG.md`

Forbidden at root:
- `*.old`, `*.bak`, `tmp*`, `*-cwd`, `nul`
- Generated reports: `*_warnings.txt`, `build_output.txt`, `InspectCodeExport*.json`
- Loose `.tscn` / `.tres` (e.g., `test_scene.tscn` belongs in `Tests/` or a feature folder)
- Plan/draft markdown files (`*_PLAN.md`, `*-plan.md`) — belong in Obsidian or `.claude/plans/`

**R4. Test files live in Tests/**
- Any file matching `*Test.cs`, `*TestSuite.cs`, `*Tests.cs` MUST live under `Tests/Logic/`, `Tests/Integration/`, or `Tests/Sanity/`.
- Exception: shared test framework code lives in `Tests/Framework/`.

**R5. Companion `.cs.uid` files exist for scripts attached to scenes**
- Every `.cs` file referenced as `ext_resource` in a `.tscn`/`.tres` must have a `.cs.uid` companion next to it.
- *Why:* Godot resolves script references via UID; missing UIDs cause "dependencies missing" errors.

**R13. C# namespace mirrors folder path under `{{PROJECT_NAME}}.`, modulo casing**
- Expected namespace = `{{PROJECT_NAME}}.` + each folder segment from project root, PascalCase-folded. Folder root **==** namespace root for every top-level folder — there are **no active aliases** (the remap table below is intentionally empty).
- **Casefold before comparing — folder casing is R1's jurisdiction, not R13's.** `Potion/slot_modifiers` → `{{PROJECT_NAME}}.Potion.SlotModifiers` is NOT an R13 finding (the namespace correctly PascalCases the snake_case folder); the folder name itself is R1's concern. Comparing case-insensitively keeps one root cause from firing two rules.
- **Folder→Namespace Alias Table — intentionally empty (deprecated escape hatch).** Folder-root ≠ namespace-root remaps are a last resort, not a blessed pattern; the goal state is a clean mirror. The two historical aliases were eliminated 2026-05-30 by fixing the *structure*, not the mapping:
  - `SpellArchitecture/` (was aliased to `{{PROJECT_NAME}}.Spells`) now declares its own `{{PROJECT_NAME}}.SpellArchitecture.*`. The engine folder keeps its descriptive name and a matching namespace; the feature-organized `Spells/` content folder keeps `{{PROJECT_NAME}}.Spells.*`. The two folders no longer share a namespace, so no alias is needed.
  - `NPCs/` (was aliased to `{{PROJECT_NAME}}.Critters`) now declares `{{PROJECT_NAME}}.NPCs.*`. The folder name was kept and the **namespace** was renamed to match it — `NPCs` is the project's game-wide term for non-player entities. (`Critter`/`CritterEntity` remain as specific entity *class* names under the `NPCs` umbrella; `NPCs` is the organizing term, not a per-class rename.)

  If a future case genuinely needs a remap, add a row here AND justify why renaming the folder or the namespace isn't viable — but treat it as a smell to revisit, not a convention to follow.
- **Type-name == namespace-leaf collision (CS0118).** A class whose name equals its folder/namespace leaf yields `{{PROJECT_NAME}}.Foo.Foo`; a bare `Foo` type-reference from any other `{{PROJECT_NAME}}.*` namespace binds to the *namespace* (a namespace member shadows even a `global using` alias). Resolve by **renaming the type** so it differs from the leaf — precedent: the player entity class `Wizard` → `WizardCharacter` (2026-05-30), which let the whole `Wizard/` folder adopt `{{PROJECT_NAME}}.Wizard.*`. Expression-context uses (`Foo.Instance`) tolerate the stutter; type-context uses (`Action<Foo>`, casts, generics) do not. Do NOT leave the class in the global namespace to dodge the collision — that is itself an R13 violation.
- **Namespace that shadows a framework type.** A folder whose namespace leaf equals a heavily-used framework type (e.g. `Global/Input/` → `{{PROJECT_NAME}}.Global.Input` shadows Godot's `Input`) is still correct under R13; resolve the shadow by fully-qualifying the framework type (`Godot.Input.X`) at the few colliding call sites, not by abandoning the mirrored namespace.
- **Action tier: ASK (exception to Tier-1-is-FIX).** Namespace renames touch every consumer `using` — never auto-fix. Emit ASK with options: (1) rename namespace to expected, (2) rename the folder, (3) rename a colliding type. Escalate to PLAN only if an entire folder cluster drifts.
- **Tolerated as ASK-low-confidence, not silent-pass:** *leaf-collapse* (a single-class object/enum subfolder sharing its parent's namespace, e.g. `Environment/Objects/SloshStation/` → `…Objects`) and *foreign namespaces* (a PP-folder file declaring a non-`{{PROJECT_NAME}}.*` namespace). The canonical legitimate foreign namespace is a **framework `partial class` extension**: `AI/BB/BBDataSig.cs` declares `Jmodot.Implementation.AI.BB` to extend Jmodot's `partial class BBDataSig` with PP-specific blackboard keys — moving it would split the class and break every `BBDataSig.X` reference. Confirm such cases are deliberate; don't flag them.
- **Scope:** `.cs` under the {{PROJECT_NAME}} root only. Exclude `Tests/`, `Jmodot/`, `addons/`, `gdunit4_testadapter_v5/`, `Tools/`, `script_templates/`, and `bin/`/`obj/`/`.godot/`.
- *Why no LSP:* detection is pure path↔string comparison (Grep `^namespace` + folder walk), so it runs identically on cloud where LSP is unavailable.

---

### Tier 2 — Philosophy (usually ASK-tier — needs judgment)

**R6. Feature folders co-locate their resources**
For folders organized feature-based (`Spells/<type>/`, `Ingredients/<name>/`):
- Each subfolder contains its own `.cs` + `.tscn` + scene-exclusive `.tres` + assets.
- No `Scripts/` / `Scenes/` / `Resources/` type-splitting *inside* a feature folder.
- Resources used by 2+ features get hoisted to a sibling shared folder (e.g., `Spells/Visuals/`).

**R7. Layer folders stay layered**
For folders organized layer-based (`SpellArchitecture/`, `AI/`, `Movement/`, `Combat/`, `Stats/`, `Animation/`):
- Subfolders are roles/layers (`Foundation/`, `Behavior/`, `Reactions/`).
- Do NOT introduce per-feature subfolders here (no `SpellArchitecture/Fire/`).
- If a feature needs its own logic carved out, it goes in `Spells/Fire/` or `SpellEffects/Fire/`, not inside the engine code.

**R8. UI rubric — UI lives in `UI/<subsystem>/` by default**

UI base classes (`Control`, `CanvasLayer`, `Container` derivatives) MUST live under `UI/<subsystem>/` unless one of three exceptions applies:

| Exception | Justification | Example placement |
|-----------|---------------|-------------------|
| **(a) Mutates** subsystem state | The UI writes to subsystem internals (not just observes via signals) | `Waves/Editor/WaveEditorUI.cs` (writes to `WaveSetResource`) |
| **(b) Tightly bound** to internal types | The UI references classes that aren't part of the subsystem's public API | `AI/HSM/Debug/StateTransitionOverlay.cs` |
| **(c) Scene-embedded** | World-space UI physically anchored to an entity (not screen-space HUD) | `Wizard/Visual/HealthFloater.cs` |

Critically: UI that *only reads* via signals/events still belongs in `UI/`. Observation is not coupling. Mutation is.

**R9. No single-file folders (without growth justification)**
A folder containing exactly one file is either:
- A folder created in anticipation of growth that never came → flatten.
- A misplaced singleton that belongs with siblings → relocate.
- An intentional home for forthcoming siblings → accept (audit re-checks next run).

**R10. No mixed concerns in a flat folder**
A folder containing UI + logic + data + assets in a flat structure indicates either:
- A feature folder that should explicitly co-locate everything (acceptable for `Wizard/` and `Spells/Fire/`-style content folders — these are *meant* to bundle).
- A layer folder that has accreted off-domain files (NOT acceptable; relocate the off-domain files).

The distinction lives in R6 vs R7 above. Audit asks: *Is this folder meant to be feature-organized? If so, mixing is fine. If layer-organized, mixing is a smell.*

**R14. Persistence files co-locate with their owning domain**
Save repositories (`ISaveRepository<T>` implementations) and their persisted data Resources live in the owning domain's folder, not a central save folder:
- `SettingsRepository` / `SettingsConfig` → `Settings/`; `MetaProgressionRepository` / `MetaProgressionData` → `Hub/Meta/`; `PlayerProfileRepository` / `PlayerProfile` → `Hub/Profile/`.
- Shared serialization infrastructure (`ISaveRepository<T>`, `AtomicResourceFile`, `AtomicConfigFile`, `SchemaMigrator`) is framework-general → lives in `Jmodot/…/Persistence/` (per the Jmodot tiebreaker), never a PP folder.
- Centralizing all repositories into one folder is the rejected `SaveSystem`-god-object — the foundation Persistence Subsystem (`game-foundation-pillars/arch.md §2`) chose per-domain repositories (approach P-B) deliberately.
- *Why:* keeps a domain's data + repository + consumers navigable together; mirrors the feature/layer co-location philosophy above.
- *Action tier: ASK.* The owning domain for a cross-cutting save aggregate (a profile spanning currency + equippables + stake) is a judgment call — surface options, don't auto-relocate.

---

### Tier 3 — Boundaries (FIX-tier when violated)

**R11. Framework boundary — Jmodot/ MUST NOT reference {{PROJECT_NAME}}.\***
- No `using {{PROJECT_NAME}}.*;` in any `Jmodot/` file.
- For project-wide defaults that Jmodot needs, introduce a static seam class in `Jmodot.Core.*` and have {{PROJECT_NAME}} populate it at autoload `_EnterTree`.
- *Source:* memory entry `jmodot_framework_boundary_rule.md`; SKILL.md "Default Value Pattern" section's framework boundary caveat.

**R12. Top-level folders match the project_subsystems SKILL**
- Every top-level folder must be documented in [project_subsystems SKILL](../project_subsystems/SKILL.md)'s `subsystems:` YAML registry.
- New top-level folders MUST be added to that table in the same change that introduces them.
- *Why:* Undocumented top-level folders signal organic drift; the doc is the source of truth for "what systems exist."

---

## Folder → Organizational Style Map

This is the audit's grading rubric. When examining a folder, look up its row to know which philosophy applies.

### Feature-organized

| Folder | Style | Subfolders meaning |
|--------|-------|---------------------|
| `Spells/` | Feature | One per spell archetype (Fire, Ice, Snowball, Smoothie...). Plus shared sibling folders (`Base/`, `LobAiming/`, `PhysicsBodies/`, `Visuals/`) |
| `SpellEffects/` | Feature | One per effect family (e.g., `Homing/`) |
| `Ingredients/` | Feature | One per ingredient (Apple, MooYum...) plus shared (`Modifiers/`, `Visual/`, `Spawning/`) |
| `Spells/<type>/` | Feature (nested) | Each spell co-locates `.cs` + `.tscn` + `.tres` + assets |
| `Ingredients/<name>/` | Feature (nested) | Each ingredient co-locates its data + visuals |
| `Synergies/` | Feature | One per synergy specialty + tier definitions |
| `Environment/` | Feature | One per environment type (Ground, Wall, Objects, Components, Visuals, Templates...) |

### Layer-organized

| Folder | Style | Subfolders meaning |
|--------|-------|---------------------|
| `SpellArchitecture/` | Layer | Roles: Foundation, Behavior, Casting, Collision, Reactions, Visuals... Namespace `{{PROJECT_NAME}}.SpellArchitecture.*` (mirrors the folder; the feature-organized `Spells/` content folder owns `{{PROJECT_NAME}}.Spells.*` separately). |
| `AI/` | Layer | BT, HSM, Blackboard subsystems |
| `Movement/` | Layer | Processors, ExternalForceReceivers, Strategies |
| `Combat/` | Layer | Reactions, damage resolution, status effects |
| `Stats/` | Layer | StatSheets, controller configs |
| `Animation/` | Layer | Orchestrators |
| `Interactions/` | Layer | Cross-system interaction components |
| `Global/` | Layer | Autoloads, registries, categories, data |

### Special

| Folder | Style | Subfolders meaning |
|--------|-------|---------------------|
| `UI/` | UI-by-subsystem | One per subsystem the UI serves (Player, Crafting, Game...) — see R8 |
| `Visual/` | Layer-leaning hybrid | Effect families (Effects, Explosion, Particles, OneShot, Wizard, Ingredients) — VFX is content-like but groups by family rather than per-spell |
| `Wizard/` | Feature (single-entity) | Co-locates the player entity's scripts, sprites, scenes, and component code. Mixed concerns are intentional |
| `Minions/`, `Grabbables/` | Feature (single-entity each) | Same shape as Wizard/ |
| `NPCs/` | Layer (AI subsystem) | `AI/` (actions, conditions, components), `Casting/`, `Enemies/`, `Data/`, `Visual(s)/`, plus per-entity scenes under `Critters/`. Namespace `{{PROJECT_NAME}}.NPCs.*`. |
| `Tests/` | Layer | `Logic/`, `Integration/`, `Sanity/`, `Framework/` |
| `Tools/` | Layer | Dev utilities by purpose |

### Infrastructure (not subject to philosophy rules)

`addons/`, `logs/`, `script_templates/`, `Jmodot/` (submodule), `.claude/`, `.config/`, `.godot/`, `bin/`, `obj/`, `TestResults/`, `gdunit4_testadapter_v5/`

---

## How To Apply The Rules When Adding A New File

1. **Identify the file type and intent.** Is this engine code, content, UI, or data?
2. **Find the home folder using the map above.** If unsure between feature-folder and layer-folder, ask: *who navigates here most often, and how do they think about it?* Designer-content navigation → feature. Engineer-machinery navigation → layer.
3. **Check Tier-1 rules.** Casing, no root clutter, tests in `Tests/`, `.cs.uid` companion if the script attaches to a scene.
4. **For UI: apply R8.** Default to `UI/<subsystem>/`. Justify any subsystem-folder placement with one of (a/b/c).
5. **For new top-level folders:** update [project_subsystems SKILL](../project_subsystems/SKILL.md)'s `subsystems:` YAML registry in the same commit (R12). `/sync_subsystems` proposes the edit interactively.

---

## Audit Cadence

- Run `/structure_audit` periodically (suggested: at end of any session that adds 3+ files or a new folder).
- Not wired into `/session_end` by default — full-project scan is too noisy for per-session use. May add an opt-in `--quick` mode later.
- See `.claude/commands/structure_audit.md` for the audit command itself.
