---
name: Inspect existing abstractions before designing new ones
description: Before inventing a new class/strategy/subclass, grep for the abstract base class family in the same domain. If a hierarchy already exists and has multiple subclasses, extending that family is almost always correct — inventing a parallel abstraction is almost always wrong
type: feedback
originSessionId: 263988e3-3600-4062-bbc5-af93d0e10901
---
Before designing a new class, strategy, or subclass in a given domain, check if an abstract base class with multiple existing subclasses already covers the concern. If yes, extend the existing family. Do NOT invent a parallel abstraction — even under context pressure, and even if the new name "reads nicer."

**Signal:** The design instinct "I'll add a new `FooStrategy` for this case" should trigger a grep for `abstract class .*Strategy`, `abstract partial class`, `interface I<Concern>`, or a domain-keyword search in the relevant directory. If the result is a base class with 2+ subclasses, that family is the seam.

**Why:** User explicitly corrected two plan iterations this session (2026-04-21, grenade lob aim refactor):
1. I proposed `PointAtLobStrategy` as a new `LobAimStrategy` subclass for mouse aim when `VectorBindingBase` (with `VectorActionBinding` and `VectorMouseCursorBinding` subclasses) was the actual seam — the whole input abstraction was already designed for exactly this.
2. I proposed a `Jmodot.Core.Input.VectorInputSemantic` enum *alongside* pretending the magnitude heuristic was the discriminator — the user made me step back and "look at how mouse aiming is currently implemented in the game."

Both times I had jumped to new code without inventorying the existing hierarchy. The existing one was better designed than my proposals.

**How to apply:**
- Before designing a new `*Strategy`/`*Behavior`/`*Handler` class: grep for `abstract class`, `abstract partial class`, and the domain keyword in `Jmodot/Core/`, `SpellArchitecture/`, and relevant top-level directories.
- If a base class with 2+ subclasses exists, read at least 2 of the subclasses to understand the shape before proposing anything new.
- If the proposed change crosses an existing abstraction (e.g., new intent type, new strategy output), read the consuming sites to understand the contract — the existing family may already support the use case via a small extension.
- **Meta-rule for plan mode:** "Where does this fit in the existing abstraction tree?" is a mandatory question before drafting any plan that introduces new types.

**Adjacent rule — read source before speculating about API (added 2026-04-28):**
When a plan reasons about the API of an existing class (its method signatures, return types, query semantics, lifecycle), READ the source file before writing the plan. Do NOT infer the API from related files, call sites, or the class name. Inference produces plans with subtly-wrong assumptions that get caught at execution time and force rework.

Concrete: planned around `CombatLog`'s "recent events query" assuming it might need an extension API; actually had `GetAllCombatResultsWithinCombatTime<T>(seconds)`, `HasEvent<T>(predicate)`, `GetEvents<T>()`, plus pruning helpers — fully sufficient out of the box. User correction was direct: "please actually comprehensively look through combatlogger, don't guess. then design the full plan."

**Trigger:** Plan mentions a class by name and proposes design around its API. Stop. Read that class's source file. Then write the plan.

**Distinction from the parent rule:** Parent rule = "before *inventing* a new abstraction, find the existing one." This rule = "before *reasoning about* an existing one's API, read its source." Both fire during plan mode, both fail with the same shape (rework on execution), but the diagnostic ("did I invent a parallel?" vs "did I infer an API?") differs.

**Refinement — `Array<X>` and `Dictionary<X, Y>` fields on Resource types are themselves potential homes (added 2026-04-30):**

Before proposing a new abstraction, also grep the related Resource hierarchy for existing collection-type fields. If the new abstraction would hold a list of Xs or a map of Y→Z, the existing field may already accept it.

**Trigger:** Plan proposes a new `*Profile` / `*Config` Resource on a parent type to hold a list-of-effects, list-of-stats, or map-of-keys-to-values. Stop. Read the parent type's exported fields. If an `Array<X>` or `Dictionary<X, Y>` already exists with compatible element type, the new abstraction is duplicative.

**Concrete (2026-04-30):** Brainstorm proposed `ElementProfile.SignatureEffects` Resource on `Category` to inherit element-signature SpellEffects across all spells of that element. `TraitTier.Effects: Array<SpellEffect>` already supported per-tier signature wiring (production pattern in `Spells/Fire/Tier1_Fireball/fire_tier1.tres:19-22`). The new abstraction was duplicative; designer wiring per-tier was the architecturally-sanctioned path.

**Distinction from parent rule:** Parent rule = "before *inventing* a new abstraction *type*, find the existing *base class*." This refinement = "before *inventing* a new abstraction *to hold collected content*, check whether existing `Array`/`Dictionary` fields on related types already accept that content." Both fail with the same shape (parallel abstractions); the diagnostic differs.
