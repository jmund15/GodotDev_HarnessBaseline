---
paths:
  - "**/*.cs"
---

# Design Litmus (fires on every .cs touch — answer before introducing, not after review)

Seven questions, asked at the moment of INTRODUCTION (a new type, `[Export]`, parameter, bool/enum switch, or helper). Full doctrine: `architecture_philosophy` skill; scene-facing mirror: `rules/scene_authoring.md` §Scene anatomy.

1. **Reuse before introduce; a new seam needs two.** Before adding any named configuration surface — type, `[Export]`, parameter, behavior-selecting bool/enum, `*Helper`/private static — name the family that already owns the concern, or record "none exists". A bool that selects behavior is a strategy slot in disguise: search for `*Strategy`/`*Config` siblings in the folder and on the base first. Passing a literal `null`/default into a parameter typed as an existing strategy/config abstraction is a **neutered seam** — wire the family instead. *Dual case, when "none exists":* a seam cut for exactly one concrete implementation is indirection wearing an interface. Name the second implementation or consumer that exists TODAY, or that the user has stated as direction (CLAUDE.md *Modular when direction is known*); can't name it → don't cut the seam.
2. **Orthogonal axis → composition, not a rung.** Before subclassing, name the axis the base hierarchy varies on. If the new behavior varies on a DIFFERENT axis (could co-occur independently of the base's defining feature), it is an optional composable Resource/config slot on the base — precedent: `StatusRunner.SpreadConfig` — not a subclass. *Litmus:* could a sibling that lacks the base's defining feature still want this behavior? Yes → compose.
3. **One knob, one axis.** An export's name describes everything that changes when it flips. A bool that silently selects two concerns gets split, or becomes an enum whose members name the fusion.
4. **Derive, don't duplicate.** Every authored value has one home; a second surface derives it, never re-authors it. *Litmus:* changed in one place and shipped — what still reads the old value?
5. **Every visible export is read** in every context an author can reach it. Inert export = defect: delete, or narrow the type per context.
6. **Cohesion test.** If this new piece were deleted, would exactly one responsibility disappear? More → it's fused; less → it's redundant with an existing surface. (Asks what the piece OWNS; `architecture_philosophy` §Deletion Test asks whether it's deep enough to keep — what would scatter across callers.)
7. **A seam is signature plus contract.** Introducing an interface, abstract base, or component contract: state its invariants, call-ordering constraints, failure modes, and required configuration alongside the members. A design that records only signatures has not designed the seam — this codebase's recurring defect classes (init ordering, subscription ordering, required-dependency loudness) are precisely the facts a signature cannot carry. *Litmus:* could a caller holding only the signature use this wrong and still compile? Then the contract is undeclared.

**Dependency rule (components):** inter-component dependencies are one-directional and communicated — explicit exported slot, or (house default) auto-resolution via Blackboard/ENCI + `_GetConfigurationWarnings` + loud initialize. Never a hidden sibling walk (`gotcha_godot_scene_reference_traps`).

**Consumption rule (capability-graded):** a consumer reads the NARROWEST layer answering its question — raw substrate (perception, physics, stats) for raw facts, the deriving component for that component's derived answer. A derived layer is an optional capability over the substrate, never a mandatory funnel: consumers resolve the deriving component (per the Dependency rule) rather than reading a value key it publishes, and the substrate stays consumable without it. *Litmus:* two consumers asking different questions ("what is near me" vs "what have I committed to") reading one surface → the layers are fused; split them.
