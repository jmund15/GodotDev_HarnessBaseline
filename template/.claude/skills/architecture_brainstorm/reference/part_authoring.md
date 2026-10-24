# Part authoring: required evidence and artifact gates

Read at [`SKILL.md`](../SKILL.md) Step 5, once every design section is approved. This is the skill's final mandatory phase.

## Author Parts for the roadmap

Decompose the design into Parts per the schema in [`_brainstorm_shared/common.md §6`](../../_brainstorm_shared/common.md). Each Part becomes a row in the topic-folder `roadmap.md` (created or updated by `/update_roadmap` in Step 8). Every architectural claim in the design body maps to exactly one of: {a Part on the roadmap, an explicit worklog deferral, abandoned with stated reason}.

For each Part:

- Set **State** per the [common.md §6.3 vocabulary](../../_brainstorm_shared/common.md) (see *Part-readiness gate* below).
- Enumerate **Deps** (other Parts; cross-folder via `../<folder>/roadmap.md#<part>`).
- Provide **Trigger** per [common.md §6.10](../../_brainstorm_shared/common.md) (required for `*-pending` / `*-rework` / `workshop-pending` / `user-owned` States; content shape varies by State — see §6.10 sub-table).
- Set **Source** to the design-doc section that committed this Part.
- Propose initial **Pos** from the Deps, per [`roadmap_schema.md`](../../_brainstorm_shared/roadmap_schema.md) §6.2 *Pos*.

**Procedural order for the Part-authoring sub-procedures** (each `##` section below covers one in detail):

1. **PR-grouping guard** — recognize that any pre-existing design-doc grouping is a PR-batching strategy, not the Part list. Author fresh.
2. **Arch-pending consolidation litmus** — if 2+ `arch-pending` Parts would be authored, check whether their open questions roll up to one matrix-shaped session.
3. **User-owned question** — per Part, is execution inherently user-domain? If yes, mark `user-owned`, set Trigger to the user deliverable, skip steps 4-8.
4. **Part-readiness gate** (5 criteria) — assigns State `plan-pending` / `arch-pending` / `idea-pending` by how many criteria pass.
5. **Plan-pending cohesion litmus** — after the gate, scan any run of 3+ sequential `plan-pending` Parts and merge adjacent thin + tightly-coupled ones (the plan-tier mirror of the arch-pending consolidation litmus; counter-weight to the Compound-`+` split litmus).
6. **Downstream-of-fork guard** — after the gate, drop speculative Parts whose shape depends on a `*-pending` sibling's un-decided output.
7. **Single-concern-split guard** — a cleanup / migration / retirement spanning multiple Parts gets ONE named owner (or explicit sequencing); never leave a slice unowned for a "mechanical" downstream Part to propagate.
8. **Trigger format** — populate Trigger per common.md §6.10 (content shape per State).
9. **Integration touch points** — for `plan-pending` Parts only, enumerate cross-subsystem call sites; ≥3 boundaries forces split or additional Socratic pass.
10. **API + Test Pin** — for each `plan-pending` Part, write the actual class signatures (fields + key methods) and `[TestCase]` method names into the doc body BEFORE invoking `/update_roadmap`; if you can't, demote to `arch-rework`.

## PR-grouping guard (run BEFORE the readiness gate)

If the design body contains a pre-existing grouping section (sessions, PR batches, milestones, "recommended session grouping"), treat it as a PR-batching strategy — NOT the Part list. Author Parts fresh against the 5-criterion gate, item-by-item. A PR-batched session may legitimately split into multiple Parts; a single Part may span items from multiple PR batches. The two axes can disagree by design.

## Arch-pending consolidation litmus (run BEFORE authoring 2+ `arch-pending` Parts)

If about to produce 2+ `arch-pending` Parts, check whether their open questions roll up to one. Symptoms: each fork is "what's the X pattern for subsystem Y?" → rollup is "what's the canonical X?" (one matrix); same KIND of decision across different types → one matrix; same `Source` cell across forks → one session. **Litmus:** *"If I held one session asking 'what's the canonical X?', would all forks resolve?"* Yes → consolidate; push per-fork inventory into the consolidated Trigger per *Trigger format* below. Inverse of the *Compound-+ name split litmus* — that catches under-splitting at plan-pending tier; this catches over-splitting at arch-pending tier (the *Plan-pending cohesion litmus* below is the plan-tier merge counterpart).

## Downstream-of-fork guard (run AFTER the readiness gate)

Don't author Parts whose shape depends on a `*-pending` sibling's output — that's the future session's job. Push inventory into the `*-pending` Part's **Trigger** instead (see *Trigger format* below); the future session inherits the inventory, not the shape. **Litmus:** *"If the upstream `*-pending` session decided differently, would this Part change shape?"* Yes → speculative; remove.

## Single-concern-split guard (run when one concern touches 2+ Parts' surface)

When a single cross-cutting concern — a cleanup, a migration, a retirement of a dead/legacy surface — spans the surface of 2+ Parts, it MUST have **one named owner Part** (or an explicit purge-first → consume-after sequence via Deps). Never split it so that a slice is left unowned, especially not as a side-note on a Part framed as "mechanical." A downstream Part told its job is mechanical relocation/eviction will faithfully *propagate* an un-purged surface rather than remove it — multiplying the contamination across every target the mechanical Part writes to.

- **Litmus:** *"If the owning Part is skipped or its plan_check doesn't fire, does a later 'mechanical' Part move/copy the un-resolved surface instead of deleting it?"* Yes → the concern is split with an unowned slice; assign one owner or sequence purge-first.

## User-owned question (ask once per Part, BEFORE the 5-criterion gate runs)

Is this Part's execution inherently user-domain (spatial design, manual content authoring, taste-driven tuning) such that no agent phase applies? Explicit yes → mark `user-owned` (per `_brainstorm_shared/common.md §6.3`), Trigger names the user deliverable, skip the 5-criterion gate (orthogonal). No → run the gate as normal. Hard-but-automatable stays `plan-pending`. Escape-hatch litmus: *"inherently user-domain"* (spatial, taste, manual authoring) ≠ *"hard for the agent right now"*.

## Part-readiness gate (when can a Part be authored with State=`plan-pending`?)

A Part may be marked `plan-pending` (ready for plan production + impl) only when ALL FIVE criteria hold:

1. **Files-nameable** — the design commitment names concrete files OR extends classes inventoried in Step 3. *"The X subsystem"* fails; *"new `StaminaComponent.cs` + extends `Jmodot.AI.HSM.Transitions.TransitionCondition`"* passes.
2. **Tests-writable** — the test plan translates DIRECTLY to one of:
   - Logic-Domain: one `[TestSuite]` + 2–5 `[TestCase]` method names, OR
   - Gameplay-Domain (per CLAUDE.md *Hybrid TDD*): an `ISceneRunner` integration plan + a playtest verification rubric.
3. **No-hidden-design** — every claim has lifecycle, owner, init-timing resolved in the doc body. *"X will do Y"* with X's owner unstated fails.
4. **All-new-types-specified** — either extends an existing 2+ subclass family (per Step 3 inventory), OR introduces new types whose shapes (fields, base class, key methods) are FULLY specified in the doc body. Greenfield subsystems legitimately introduce multiple co-designed types — that's fine if all shapes are specified.
5. **Bounded-scope** — the Part touches ≤2 subsystems per the [`project_subsystems` registry](../../project_subsystems/SKILL.md), ≤4 hours of focused work estimate, and Step 3's abstraction inventory revealed no further family-extension surprises.

**Compound-`+` name split litmus.** Compound names ("Foundation + Refactor", "Authoring + Integration") are a split-candidate signal. Before authoring as a single Part, ask: *"Is this one cohesive unit or two stapled together?"* When in doubt, split.

| Criteria passing | State assignment |
|---|---|
| **All 5** | `plan-pending` — ready for plan production |
| **3-4** | `arch-pending` — gap is design-shaped (one criterion open); name the open piece in Trigger |
| **0-2** | `arch-pending` (architectural fork) OR `idea-pending` (creative fork) — Trigger names the open piece |
| **Open piece is a feel-fork** | `prototype-pending` — the fork's answer turns on how something reads or responds at runtime, so discussion cannot settle it. Applies only under [`design_contract.md`](../../_brainstorm_shared/design_contract.md) clause 7's *Prototype carve-out*: `prototypes/<slug>/QUESTION.md` states the question and the shape of its answer, and the Trigger mirrors it |

The 0-2 case catches Parts masquerading as impl-ready when their open work is genuinely design. The 3-4 case is the most common "almost ready" state — a single open piece blocks promotion to `plan-pending`. A feel-fork whose answer shape cannot be stated is not a feel-fork; it stays `arch-pending` or `idea-pending`.

**Trigger format safety-net.** `/update_roadmap` Step 3 emits soft warns for malformed Triggers across every State-category per the [common.md §6.10 sub-table](../../_brainstorm_shared/common.md). This catches standalone transitions and hand-edits that bypass this skill's authoring gate; in-skill authoring (procedural step 8 above) should produce conforming Triggers before the validator fires.

**Iterative narrowing.** When a Part is marked `*-pending`, the next brainstorm session for that fork starts the narrowing pass. Stop-condition for the chain: clarifying questions feel forced, or proposed approaches differ only in cosmetic detail — at that point more brainstorming adds Socratic overhead without sharpening the design.

## Plan-pending cohesion litmus (run AFTER the readiness gate, when a design yields 3+ sequential `plan-pending` Parts)

The readiness gate is sizing-*asymmetric* — criterion 5, the Integration-touch-points ≥3 rule, and the Compound-`+` split litmus all guard the UPPER bound; nothing guards the lower. A design decomposed by **type-surface layer** (data-types → builder → rules → config → seam → algorithm) yields a chain of `plan-pending` Parts that each pass all 5 criteria individually yet are collectively over-split: thin declarative Parts front-loaded before the one behavioral Part, each costing a full plan→TDD→review cycle for little production code. This is the plan-tier mirror of the *Arch-pending consolidation litmus* and the counter-weight to the split-biased *Compound-`+`* litmus.

Scan any run of sequential `plan-pending` Parts; **merge** adjacent ones matching the **thin-and-coupled** signature:

- **Thin** — production surface is mostly declarative (abstract bases, structs, config Resources, enums); the test artifact dwarfs the production code.
- **Coupled** — its types exist mainly to feed the next Part (the next Part's API consumes them directly), OR it shares a design decision with its neighbor (resolving one forces resolving the other).

**Litmus:** *"Would one plan-and-build session naturally take these adjacent Parts together, because each is thin and exists mainly to feed the next?"* Yes → merge into one buildable unit.

**Keep split only for a stated reason** (record it in the Part's Source/Trigger): independent reuse beyond the next layer; isolated risk worth shipping/verifying alone (a substantial layer with its own deep test surface — e.g. a builder); a framework/consumer review boundary; genuine standalone size.

**Not in conflict with Compound-`+`:** that splits ONE Part bundling *unrelated* concerns; this merges SEPARATE Parts that are thin slices of the *same* concern, artificially layered. Discriminator: unrelated-bundle → split; thin-coupled-layer → merge.

**Size-ceiling fallback:** if merging would breach criterion 5's ≤2-subsystem / half-day ceiling, keep the Parts split but **plan the cohesive cluster in one `/plan_part` session** rather than one `/part_drive` per thin Part — the planning overhead is the real cost, not the Part boundary. (Unit of *shipping* stays small; unit of *planning* becomes the cluster.)

## Integration touch points (required field for `plan-pending` Parts)

Each Part authored at `plan-pending` enumerates the cross-subsystem call sites or seams it'll touch, referencing `project_subsystems` IDs. Format: bullet list of `<subsystem-id>: <touch-point description>`. Lives in the design doc (the source linked from the Part's `Source` cell), not the roadmap Parts table.

If touch-point count **≥3 distinct subsystem boundaries**, scope-review flag: either split the Part into smaller Parts (each scope-bounded) OR audit the integration shape now (additional Socratic pass). Crossing 3+ subsystem boundaries is the canonical scope-creep failure mode.

## API + Test Pin (per `plan-pending` Part, BEFORE Step 8)

For every Part declared `plan-pending`, write the content criteria 2 and 4 demand into the doc body BEFORE invoking `/update_roadmap`:

- **Criterion 2** → 2–5 `[TestCase]` method names per `[TestSuite]` (Logic-Domain) OR `ISceneRunner` test method names + playtest rubric (Gameplay-Domain). Prose descriptions like *"tests state-transition validity"* do NOT satisfy this — write `IsTransitionValid_IdleToActive_ReturnsTrue()`.
- **Criterion 4** → C# class signature (fields + key methods) for every new type. *"`SessionLifecycleManager` autoload owning lifecycle state"* does NOT satisfy this — write `public partial class SessionLifecycleManager : Node { public SessionPhase CurrentState { get; private set; } public void RequestTransition(SessionPhase target, ...); ... }`.

If you can't write them, the Part is `arch-rework` with Trigger naming the missing API surface. Naming a class and saying *"methods TBD at plan time"* is the canonical failure shape — it passes the criteria's letter while violating their spirit. **Litmus:** would a future implementer have to invent method signatures + test names, or can they read them verbatim from this doc?

This sub-procedure is the enforcement teeth for the gate. The readiness gate's criteria define WHAT's required; this step requires WRITING it BEFORE state declaration goes to `/update_roadmap`. Without this enforcement, gate criteria are aspirational — the agent declares plan-pending against criteria the doc body doesn't actually satisfy.

## Cardinality and recommended starting

- **Recommended starting Part** — name the Part that should be tackled first, with one-line rationale (foundation / dependency root / unblocks most downstream). Required when 2+ Parts produced.
- **Parallel-safe groups** — Parts at the same Pos share that depth and can be developed simultaneously after their common deps land.
- **All-`*-pending` outcome is valid** — if the design produces zero `plan-pending` Parts, that's the design honestly acknowledging more brainstorming is needed before implementation. State this explicitly; Step 8's `/update_roadmap` will record the `*-pending` Parts and trigger the next narrowing session(s).

## MVP recommendation (optional — fires when Parts ≥ 5 AND top-level roadmap AND no MVP section)

If ALL THREE hold:
1. The design produced **5 or more Parts**
2. The target `roadmap.md` is a **top-level roadmap** (frontmatter has NO `parent-roadmap` field per common.md §5.1) — sub-roadmaps are forbidden from carrying MVPs per [common.md §6.11](../../_brainstorm_shared/common.md)
3. The target roadmap has no `## MVP Checkpoints` section yet

…surface the recommendation to the user as the last item in the Parts-approval message:

> *"This brainstorm produced N Parts. Consider authoring MVP Checkpoints via `/mvp_plan` to frame them as playable milestones (per common.md §6.11). Skip if every Part's design-doc section already names its playtest moment."*

**Do NOT inline-author MVPs here.** Milestone-narrative thinking is a different cognitive mode than per-Part Socratic — separating it preserves Step 5's focus. The recommendation surfaces the option; the user invokes `/mvp_plan` after this brainstorm completes (or defers / declines). For roadmaps with 1-4 Parts, skip the recommendation — MVPs are usually redundant at that scale (the Parts themselves are the milestones). **For sub-roadmaps: never surface the recommendation** — sub-roadmap milestone narrative lives on the parent roadmap's MVPs which reference this sub-roadmap's Parts. If a sub-roadmap appears to need its own milestone framing, that's a signal the parent MVPs need expansion / refinement, not that the sub-roadmap should grow its own MVP section.

Get approval on the full Parts list before invoking `/update_roadmap` in Step 8.

## Part-authoring anti-patterns

Refuse the premise and cite the rule. These rows live here because they arise while this file is open; the general rows stay in [`SKILL.md`](../SKILL.md) §6.

| Rationalization | Reality |
|---|---|
| "Force this seed-stage Part to State=`plan-pending` so it 'feels complete'" | The `*-pending` states exist *precisely so you don't have to* fake commitment. Acknowledging a fork via `arch-pending` / `idea-pending` is a stronger design move than papering over it with hand-wavy fields. Litmus: *can a future implementer write the test names from this Part's content alone?* If no, it's `arch-pending` (or `idea-pending`) — Trigger names what's missing. Honesty is not failure. |
| "This Part is big / input-coupled — leave it `arch-pending` to be safe" | `arch-pending` means the *design* is genuinely unresolved — NOT that the impl is large or that you haven't investigated the coupling yet. Run the Step 3 inventory FIRST: if the coupling turns out mechanical/reference-based and you can write the API + Test Pin test names, it's `plan-pending` (split into multiple `plan-pending` Parts if impl-size exceeds one session). Impl-size → split, never `arch-pending`. Litmus: *"Is the gap a design decision I can't make, or work I haven't done yet?"* Only the former is `arch-pending`. (Inverse of the row above — that one guards against faking `plan-pending`; this guards against over-deferring to `arch-pending`.) |
| "I named the class + its responsibility, so criterion 4 passes" | Naming is necessary but not sufficient. Criterion 4 requires the C# class signature (fields + key methods) WRITTEN into the doc body, not just the class's name + role. Same for criterion 2 — prose test descriptions (*"tests state-transition validity"*) don't substitute for actual `MethodName_Scenario_Asserts()` strings. The *API + Test Pin* sub-procedure exists to force this. Litmus: can the next implementer read signatures + test names verbatim from the doc, or must they invent them? Invent → `arch-rework`. |
| "I'll add this design fork to worklog so it's easier to track" | Different surfaces, different signal. Worklog is tactical and transient; `arch-pending` / `idea-pending` Parts are strategic and design-bound (live on the roadmap with a Trigger). Cross-pollinating breaks both — worklog grows noise that doesn't fit its trigger model; the roadmap loses authority over its own design state. Architecture commitments live as Parts; trust the Trigger field. |
| "It's a seed idea, so I can leave the Trigger vague" | Vague Triggers defeat the purpose. The next brainstorm reads the parent Part's Trigger as ITS Step 1 existing-doc digest — vague triggers there force re-deriving the design space from scratch. State the ripening criterion concretely *now*, while context is fresh, even if you don't have answers yet. |
| "This Part touches 4 subsystems but the design is clear enough — mark it `plan-pending`" | The readiness gate's bounded-scope criterion (≤2 subsystems per `project_subsystems`) is the hard cap. ≥3 subsystem boundaries = canonical scope-creep failure mode (see `project_subsystems` *Cross-cutting Flows*). Either split the Part or run an additional Socratic pass — don't sneak it past the gate. |
| "The design doc has a §5a-style 'recommended session grouping' — port those as the Part list" | Different axes. PR-cohesion groups for review narrative; readiness-gate sizes for impl-units. Author Parts fresh against the gate, item-by-item. A coherent PR may still be too big for one plan-and-build session. |
| "Compound `+` name reads fine, no need to split" | Compound `+` is a split-candidate signal. When 4-of-N sequenced Parts carry `+` names, the porting axis is wrong — re-author against the gate. `Foundation + Refactor` is the canonical failure: bundling 4 unrelated workstreams under one plan-and-build session fails criterion 5 (bounded-scope). When in doubt, split. |
| "Existing brainstorm doc predates current gate but I'll just `/update_roadmap` the existing Parts" | Pre-gate Parts need re-gating, not re-importing. See [`common.md §1.2`](../../_brainstorm_shared/common.md) (Stale-roadmap remediation) — the procedure re-runs this Part-authoring phase against the existing design body, then hands the fresh Part list to `/update_roadmap`. Parts inherit the wrong sizing axis silently otherwise. |
| "I already know the inventory from grep, so I can pre-list the downstream Parts of the `arch-pending` session to save it work" | Subsystem grouping, file decomposition, and Part shape are the future session's call against ITS gate. Pre-listing pre-empts that decision and locks in inventory groupings (which often change once design questions are answered — e.g., subsystems merge if their patterns are identical). Push inventory into the `*-pending` Part's Trigger per the format spec; let the future session author Parts. |
| "Each subsystem has its own open question about pattern X, so each gets its own `arch-pending` Part" | Shared higher-order shape ("pattern per class kind?", "injection per type family?", "lifecycle per X?") rolls up to ONE arch session producing a decision matrix. Per-subsystem `arch-pending` Parts pre-decide the matrix axis IS subsystem (when it might be per-class-kind, per-protocol, per-init-timing). Apply the *Arch-pending consolidation litmus* before authoring. |
| "Type-surface layer decomposition is clean, so each layer earns its own `plan-pending` Part" | Clean dependency ordering ≠ right sizing. Layer-decomposition (data → builder → rules → config → seam → algorithm) front-loads thin declarative Parts before the one behavioral Part — each passes the readiness gate individually yet costs a full plan→TDD→review cycle for little code. Run the *Plan-pending cohesion litmus*: merge thin + tightly-coupled adjacent `plan-pending` Parts. Plan-tier mirror of the arch-pending consolidation litmus; counter-weight to Compound-`+`. |
