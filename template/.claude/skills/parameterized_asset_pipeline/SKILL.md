---
name: parameterized_asset_pipeline
description: >-
  Bootstrap a style-appropriate procedural asset pipeline for a NEW project
  (or a new track in an existing one) from the proven source-project
  methodology — entity sprites (Track A), UI chrome/icons/motion (Track C),
  external-asset conform. Triggers: "set up the art pipeline", "new project
  art bootstrap", "instantiate the asset pipeline", "style spec", "procedural
  art methodology", "conform external assets". SKIP for authoring individual
  assets inside an existing pipeline (project-local skills like
  sprite_authoring own that) and for 3D (Track B — not designed; see the
  GeneralGameDev Parameterized-Asset-Pipeline index).
---

# Parameterized Asset Pipeline — cross-project methodology

Extracted from the reference project after both 2D tracks shipped end-to-end (design: `GeneralGameDev/Research/Parameterized-Asset-Pipeline/arch-2d-extraction.md`). The originating project remains the **reference implementation** — when a recipe here feels ambiguous, read the corresponding reference-project file; its test suites are the living verification of these templates.

## 0. The style-spec-first rule

Do NOT write generator code until the style spec is filled. Read [`references/style_spec.md`](references/style_spec.md); produce the project's `palette.json` + `theme_spec.json` (+ `icon_spec.json` if the icon track is used). Every downstream decision (canvas sizes, ramps, chrome geometry, tiers, sourcing) is a spec lookup, never an inline choice.

## 1. Pipeline substrate (all tracks)

Copy from [`templates/`](templates/) into the project's `art_pipeline/`:

| Template | Role |
|---|---|
| `palette.py` | ramp+index contract: `get/shade/is_on_palette/element()` |
| `lint_core.py` | on-palette, banding, 9-patch margins, contrast gates |
| `contact_sheet.py` | render-review compose (the inspect gate) |

House conventions the substrate assumes: pure-Python sidecar (Pillow only, **no pytest** — stdlib `unittest` scripts run standalone); generators emit PNG + resolved-JSON manifest; the engine consumes manifests verbatim (no palette logic crosses the language boundary).

## 2. Track A — entity sprites (methodology, not templates)

Creature/entity generator code is genre- and style-shaped — author it per-project following the composition model (full detail: the reference project's `creature-archetype-methodology.md`):

1. **Two-axis composition** — unit = `[upper body] × [locomotion base] × [attack archetype]`. Independent axes; never subclass per-unit.
2. **Part library protocol** — locomotion/wing/prop parts are stateless singletons with a `draw()` contract, registered in one dict; adding a part never edits consumers.
3. **Contracts every part satisfies** — palette-only colors; banding lint; cross-unit silhouette distinctness (IoU gate read by an engine-side contract test); byte-identity regeneration (hash baseline).
4. **Add-a-creature recipe** — spec dict entry → attack archetype assignment (engine data) → form bake if new → regenerate + verify. **Add-a-base recipe** — new part class → registry entry → assign via spec → two-phase verification (extract with zero pixel change, THEN flip one part).
5. **Demos before roster** — new capabilities ship as demo bakes until a real unit consumes them.

## 3. Track C — UI chrome, icons, motion

1. **Chrome** — instantiate `templates/theme_gen.py`: spec roles → 9-patch tiles → `theme_manifest.json` with resolved hexes. Variant axes (element/faction/rarity chrome) are loops over spec maps producing extra tiles + type-variation entries — same geometry, different ramps.
2. **Runtime split** — instantiate `templates/ThemeManifest.cs` + `templates/ThemeBuilder.cs` (engine-pure; replace `{{PROJECT_NAMESPACE}}`). Python owns pixels + manifest; C# consumes verbatim. Game-domain color accessors (element/rarity/faction) are manifest lookups — no hex literal ever lives in engine code (the single-source recipe; the reference project's `ThemeService.ElementColor` is the worked example).
3. **Icons** — instantiate `templates/icons.py`: 16-grid glyph DSL, integer-scaled tiers, per-icon sourcing dial with a **bake-enforced CREDITS ledger** for external sources. Compose the manifest `icons` section from theme_gen (single-writer manifests — two writers on one manifest is an anti-pattern).
4. **Motion** — classify each juice primitive framework-general vs project-specific before writing it (hover/press/slide/ticker/pulse/fade are framework-shaped; game-semantic composites stay project-side). Fire-and-forget statics with node-meta kill-safe re-entry; explicit opt-in wiring, no global scanning. (Jmodot projects: `Jmodot.Implementation.UI.Motion.UiMotion` already ships this.)

## 4. External-asset conform (shared)

`templates/conform_external.py`: curated CC0/CC-BY asset → posterize to N luminance levels → map each level to a chosen palette ramp step → relaxed lint profile (`source: external`) → mandatory license ledger entry. Use when a fidelity tier demands artist-quality bases the procedural track can't hit.

## 5. The verification loop (every change, every track)

1. Regenerate (`python art_pipeline/<batch>.py`) — generators are deterministic (seeded).
2. **Read the output** (render/screenshot review — the inspect gate; subjective quality is user-gated).
3. Hash-baseline compare — byte-identity where no change was intended.
4. Lint gates + engine-side art-contract test (manifest-vs-data-sheet agreement, referenced PNGs exist on disk).
5. Categorical commits (assets ride with the generator change that produced them).

## Anti-patterns

| Rationalization | Reality |
|---|---|
| "Compute a shade — it's close to the ramp color" | Palette contract: ramp+index lookups only. Computed shades are unlintable and drift. |
| "Generalize the creature generator now, a second game will want it" | Rig code is genre-shaped. Extract methodology, not generators, until a second concrete case proves the seams. |
| "The icon/theme generator can each write their manifest section" | Two-writer manifests corrupt silently. One composer writes the file; sub-generators return entries. |
| "Skip the contact-sheet review — the lint is green" | Lint proves contract compliance, not that it reads. The inspect gate is mandatory. |
| "Cap the lint scan for speed and don't mention it" | Silent caps read as full coverage. Log every truncation. |

## Cross-links

- Schema: [`references/style_spec.md`](references/style_spec.md) · Templates: [`templates/`](templates/)
- Design + taxonomy: `GeneralGameDev/Research/Parameterized-Asset-Pipeline/` (`_Index.md`, `arch-2d-extraction.md`)
- Reference implementation (originating project's vault): `creature-archetype-methodology.md`, `ui-art-system.md`, `arch-ui-followups.md`
