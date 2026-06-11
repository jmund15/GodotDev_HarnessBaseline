---
name: gotcha-editor-reserialize-value-export-null-strip
description: "Godot editor resave (especially when ANY new Export field is added to a script) silently rewrites every `.tres` referencing that script to add explicit `null` lines for value-type Exports that were previously omitted. Value-type null deserializes as type-zero (`int` → 0, `float` → 0.0, `bool` → false, `enum` → 0) — silently breaking `>=` thresholds, conditional branches, and ratio math. Build stays green, no warning fires, no test catches it unless one happens to exercise the threshold boundary."
metadata: 
  node_type: memory
  type: project
  originSessionId: ef7557fc-8db8-47e5-a37b-143f92fd4874
---

When a `[GlobalClass]` script grows a new `[Export]` field, opening the project in the Godot editor triggers a bulk resave of every `.tres` that references that script. Godot writes the new field as `<NewField> = null` — and ALSO writes ALL the other Exports on the resource as explicit lines, including value-type Exports that were previously omitted (relying on their C# field-init default). The value-types get serialized as `null`. On next load, `null` casts to the value type's default zero — NOT the C# field-init default.

**Concrete instance (2026-05-28):** commit `9b16d361 "data(tres): serialize new StageRule exports"` resaved 20+ `.tres` files when StageRule pipeline fields were added to Modifier scripts. It inadvertently null-stripped `StrengthRequired` (int Export on TraitTier) on 7 trait-tier `.tres` files. Result: `StrengthRequired = null` → loads as 0 → `actual_strength >= 0` is true for any strength including 0 → Fire trait activates at strength 0 against intended threshold of 1. The follow-up fix commit `4c004813 "restore .tres fields dropped by 9b16d361 editor re-serialize"` restored only 2 of N affected files — the remaining 5 stayed broken until detected via the threshold-regression test on PR #81 merge gate (and the gate only caught it because the baseline-stamp-trust check exposed the regression that had been masked by hand-stamped baseline).

**Why:**
- Godot's GDExtension/script binding has no way to distinguish "field omitted from `.tres`, use C# default" from "field explicitly set to null." Both deserialize the same: type-zero.
- Editor resave is non-idempotent re: omitted-default Exports. Once written as explicit `= null`, future opens preserve the explicit null.
- The build doesn't parse `.tres` — schema drift between script Exports and `.tres` content is invisible until runtime.
- `JmoLogger.Error` doesn't fire on null-cast-to-zero — it's a silent type coercion, not an exception.

**How to apply:**
- Audit after any commit shaped like *"editor resave"* / *"serialize new exports"* / *"editor re-save noise"* / *"UID additions, field reordering"*: `grep -rn "^[A-Z][a-zA-Z]+Required = null\|^[A-Z][a-zA-Z]+ = null" --include="*.tres" ` — flag every value-type Export that appears as `= null`. Reference-type Exports (Resource, Node, NodePath, Script) can legitimately be null and should be excluded by name.
- Only **non-nullable value-types with a non-zero C# default** are behavior-changing — zero-default value-types (`OverridePriority = 0`, `InstabilityContribution = 0f`) and reference-types (Dictionary/Array/Resource/StringName) deserialize null harmlessly; don't restore those. Classify each flagged field against its C# declaration (type + default) before fixing.
- After such a commit, hand-restore each affected file to its design-intent value. **DO NOT delete the line** — the editor will re-strip it on next save, returning the null. Set an explicit value (e.g. `StrengthRequired = 1`).
- Pattern-companion of [[arch_rule_packedscene_resource_inline_copy]] and [[gotcha_export_enum_out_of_range_silent_false]] — all three are *.tres data-vs-schema drift* failure modes that pass the build but break at runtime.
- Prevention control (BUILT 2026-05-28): `.claude/hooks/tres_nullstrip_guard.py` flags *added* `+<Name> = null` lines on numeric-intent Export stems in staged `.tres` (diff-scoped, so already-committed nulls don't trip it) — wired into `/regression_gate` as pre-flight step `1b`. Permanent in-suite sentinel: `Tests/Logic/Data/TresValueTypeNullStripTests.cs` textually forbids `= null` on the 5 confirmed non-nullable-value-type-with-nonzero-default Exports (`DefaultCritMultiplier`/`KnockbackVelocityScaling`/`MaxRange`/`MaxAngleDegrees`/`StabilityScalingFactor`). The 9b16d361 collateral (crit/knockback/range floats, never restored by the `*Required`-only follow-ups) was restored to C# defaults 2026-05-28.
- The "editor resave touches a file" signal isn't just commit-message-detectable: if a single PR touches 10+ `.tres` files with no functional design intent change, suspect editor-resave-induced null-strip. Run the audit grep against the PR's file list before merge.

The fix shape (restore explicit values) is what [[gotcha_export_enum_out_of_range_silent_false]] already memorialized for enum Exports — this entry generalizes to every value-type Export and codifies the audit recipe.
