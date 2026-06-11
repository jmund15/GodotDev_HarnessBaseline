---
name: Architecture_Brainstorm
description: >-
  Use BEFORE new systems, 3+ file refactors, framework-boundary changes
  (Jmodot ↔ PP), or architecturally-loaded features — once candidate ideas
  exist. Triggers: "how would we approach X", "design X", "should we build
  Y", "what's the architecture for Z". Run /idea_brainstorm FIRST if
  greenfield. Produces a design doc + Parts on the topic-folder roadmap.md
  (5-criterion readiness gate). SKIP for mechanical work, worklog
  scope-1/2, bug fixes with known root cause, existing design doc, or Plan
  Mode. Pass --red_team for interleaved adversarial passes between Socratic
  steps.
---

# Architecture Brainstorm

> **Scope:** Sits **between `/idea_brainstorm` and Plan Mode**. Idea brainstorm answers *"what could exist?"*; this skill answers *"how should we build it?"*; Plan Mode (a Claude Code built-in) answers *"how do we ship it?"*. Three sequential phases.
>
> **Not to be confused with:** [`idea_brainstorm`](../idea_brainstorm/SKILL.md) — that's the upstream phase where divergent ideation produces the candidate pool. This skill consumes that pool and narrows to a design. **Not** `/create_obsidian_design_doc` either — that's a **post-hoc retrospective** written *after* a system ships.

## The Hard Gate

```
DO NOT INVOKE ANY IMPLEMENTATION SKILL, WRITE ANY CODE, OR
SCAFFOLD ANY PROJECT UNTIL THE DESIGN HAS BEEN APPROVED.
```

If you find yourself reaching for `Edit`, `Write`, or implementation-flavored skills (e.g., `spell_authoring` / `refactor_procedure`, or `architecture_philosophy`'s code patterns) before the user has approved the design — **stop**. The skill's job is to make the user's intent and the design's shape *both* precise before code writes start. Skipping ahead loses the design conversation.

---

## 1. When to Use

**Strong trigger phrases** — auto-fire when the user says:
- "how would we approach X?"
- "should we build Y? what would that look like?"
- "let's design X" / "design X" (when candidates exist)
- "I want to design X but need to think it through"
- "design a stamina system for the wizard" (or any new-system request with candidates)
- "what would Z look like architecturally?"
- "we need a Z system but I'm not sure how to structure it"

**Weak trigger phrases** — ask user first ("is this an architecture brainstorm, or do you want to brainstorm ideas first?"):
- "add feature X" (could be scope 2 or scope 3+)
- "refactor Y" (could be mechanical or architectural)
- "implement the Q system" (depends whether design has happened)

**Fire when ALL of these hold:**

- [ ] Task scope is genuinely 3+ (multi-file or design-choice-heavy) **OR** the user describes the feature in open-ended terms.
- [ ] No existing design doc covers the topic (verified via common.md §1 existing-doc check, run in Step 1).
- [ ] User has NOT already entered Plan Mode.
- [ ] Task is not in the SKIP categories below.
- [ ] Candidate ideas EXIST — either from `/idea_brainstorm` (read its idea-bank doc), from a mature domain with canonical patterns, or from the user's own framing. If not, route to `/idea_brainstorm` first.

---

## 2. When to Skip

**Worklog-scope-driven skips** (use the `worklog_reference` skill's scope definitions):

| Worklog Scope | Definition | Architecture-brainstorming verdict |
|---|---|---|
| **1** | Trivial — sub-30 min, single file, mechanical | **SKIP** |
| **2** | Small focused — couple hours, possibly multi-file, no design choices | **SKIP** |
| **3** | Multi-file thoughtful — half-day, real design tradeoffs | **CANDIDATE** — ask user "does this need a design pass first?" |
| **4** | Full feature — multi-session, **linked planning doc REQUIRED** | **SKIP** (read existing linked doc; design has already happened) |

**Other skip categories:**

- [ ] **Mechanical changes** — typo fixes, single-line tweaks, simple renames, file moves, mechanical migrations.
- [ ] **Bug fixes with known root cause** — handled by the [`debugging`](../debugging/SKILL.md) skill's phased procedure. Brainstorming is for forward design, not retrospective root-causing.
- [ ] **Existing design doc covers the work** — §1 surfaces this. If a doc exists, redirect: *"A design doc covering this exists at `<path>`. Load it and follow `doc_before_writing` discipline; brainstorming would duplicate effort."*
- [ ] **User already in Plan Mode** — the user has bypassed brainstorming intentionally. Do not interrupt.
- [ ] **Greenfield with no candidates** — route to `/idea_brainstorm` first; come back here when an idea bank exists.

---

## 3. Three-Phase Composition

```
/idea_brainstorm           /architecture_brainstorm        Plan Mode + impl
─────────────────          ─────────────────────────        ────────────────
"What could exist?"        "How should we build it?"        "How do we ship?"

• Scope-framing Socratic   • Architecture-narrowing         • Entry: /plan_part
• Per-cluster diverge        Socratic                         <part> (briefs Plan
  → filter → hone          • 2–3 approaches with              Mode with verbatim
  → cluster → present        trade-offs                       design surface +
• Idea-bank doc            • Section-by-section               codebase drift)
  Status: ideation-          approval                       • Bounded file list
  complete                 • Parts authored on              • Function sigs
• /update_roadmap            roadmap.md (per                • Test names
  applies Per-Cluster        common.md §6) —                • Step-by-step
  Routing → Parts on         5-criterion gate for           • Verification
  roadmap.md                 plan-pending state               commands
                           • Design doc + 
                             /update_roadmap apply
                             Status: brainstorming-
                             complete
```

**Key invariants:**
- Plan Mode is a **Claude Code built-in**, not a local skill. This skill describes the *handoff*, not Plan Mode internals.
- The user invokes Plan Mode (or chooses inline implementation). This skill does NOT enter Plan Mode for them.
- A Plan Mode plan should reference the design doc AND the corresponding Part on roadmap.md (e.g., *"Implements Part 'Foundation+Refactor' per `BrainstormingDesigns/2026-04-29-stamina-system/arch.md#session-1`"*).
- Plan Mode's first action must be **bounded file enumeration** — if files can't be enumerated, Plan Mode HARD-STOPS (per Step 5 Plan Mode handoff requirement).
- An `ideation-complete` idea-bank doc upstream is GENUINELY useful context — load it during §1 so the approaches step (Step 4) draws from a curated pool rather than generating candidates from thin air.

**When the user wants to skip Plan Mode and go straight from brainstorming to inline implementation** — fine, that's their call. The design doc + roadmap Part landed; future maintenance has the design context.

**Cardinality and flow topology** — full vocabulary (forward fan-out, cluster merge, ideation skip, reverse signal via `*-rework`, zero-impl outcome, workshop terminate) lives at [`_brainstorm_shared/common.md §7`](../_brainstorm_shared/common.md). The above diagram is the slim "you are here" view for this skill; common §7 is the canonical reference. Note that this skill in particular often produces a zero-`plan-pending` outcome (the design IS the answer — usage convention, naming standard, etc.); §7 covers when that's valid.

---

## 4. The Procedure (8 Steps)

**Optional `--red_team` augmentation.** When invoked as `/architecture_brainstorm <topic> --red_team`, an adversarial [`/architecture_brainstorm_redteam`](../../commands/architecture_brainstorm_redteam.md) **Mode B** pass fires at each Socratic boundary (Steps 2/4/5) on a *phase-scoped* lens subset — sharpening the next question with surfaced taste-forks and folding rigor-holes into the live design before it hardens. It is **opt-in** (no flag → default flow unchanged; the Step-7 Mode A pass remains the no-flag touchpoint) and **never decides taste** ([`_brainstorm_shared/appetite_invariant.md`](../_brainstorm_shared/appetite_invariant.md)) — an interleaved rigor-hole adjusts the current approach, but a taste-fork becomes the *next* Socratic question. The per-step hooks below are marked **"Red-team hook (`--red_team` only)"**.

**Combining with `--auto`** (`/architecture_brainstorm --red_team --auto`): `--auto` governs **only the Step-7 Mode A drafted-doc pass** (its bounded generate↔critique↔revise loop). It NEVER drives the Mode B interleaving — auto-advancing the live Socratic dialogue would auto-answer taste-forks and fabricate your vision (the appetite-invariant violation). So `--red_team --auto` = human-gated interleaving through Steps 2/5 **plus** an auto-hardened Step-7 pass; the two-step taste-batch → reconcile → approve gate still governs the result. (`--auto` without `--red_team` is also valid — it just auto-runs the Step-7 pass with no interleaving.)

### Step 1: Existing-doc check + spawn-placement decision

**[Shared]** See [`_brainstorm_shared/common.md` §1](../_brainstorm_shared/common.md) — existing-doc digest (one `read_files` call over enumerated paths) + Memory sweep (semantic-search) + early abstraction scan (semantic-search → LSP).

**Architecture-specific consideration:** the digest should ALSO surface any `ideation-complete` idea-bank doc (`ideas.md` in a topic folder) covering the same topic. If one exists, load it — it's the curated candidate pool that Steps 2 and 4 will draw from. If not (and the topic isn't a mature domain), redirect to `/idea_brainstorm` first.

**Cluster-scoped consumption (when upstream `ideas.md` has Per-Cluster Routing):** Scope this invocation to the named cluster only.

- Read the cluster section: survivors (Step 4 sourcing), Findings (priors), Open Questions (Socratic seeds), Routing (timing).
- Other clusters → SEPARATE invocations producing separate `arch-<cluster>.md` files in the SAME parent topic folder.
- Cross-cluster hybrids → SEPARATE arch session per hybrid (`arch-<hybrid-slug>.md`, same parent folder).
- Cross-cluster Open Questions are NOT this session's responsibility.
- Frontmatter: `derived_from: ../ideas.md`, `cluster: <cluster-slug>`.

**Default (no Per-Cluster Routing):** Whole-doc consumption (mature-domain, user-direct framing, or legacy doc). Output: a fresh topic folder with `arch.md` inside per `common.md §5`.

**Spawn-placement decision (apply if this session is spawning a child OR is itself a child of a parent brainstorm):**

See [`common.md §5.1`](../_brainstorm_shared/common.md) — three placements (same-folder sibling / child subfolder / sibling folder) discriminated by a three-criterion test. Criteria map to outcomes per the §5.1 table (NOT "all three must hold"). Resolve at this step so Step 6's save path is unambiguous.

**Quick decision:**
- Just an extension paragraph → **same-folder** (no new roadmap).
- Own brainstorm session + independent maturity, parent-confined audience → **child subfolder** (submap; criterion 3 is *expected* to fail — parent-confined IS the definition).
- All three pass (multi-parent audience) → **sibling folder**.

**Most common gotcha:** failing a legitimate submap candidate against criterion 3 ("only serves this parent"). Criterion 3 is the submap-vs-sibling distinguisher, NOT a submap gate. See §5.1 *Common gotchas* table.

### Step 2: Ask clarifying questions (Socratic, one at a time)

**Rule:** Multiple-choice preferred over open-ended. One question per turn.

- Don't open with *"great question!"* / *"absolutely!"* — see `feedback_no_performative_agreement.md`. Ask the question directly.
- Multiple-choice example: *"For the stamina system: should it be (A) a finite resource that depletes and regenerates, (B) a cooldown stack that gates abilities, or (C) a combination — light actions deplete, heavy actions consume cooldowns?"*
- One question per turn so the user can think and respond without juggling multiple decisions.
- Stop asking when the design space is narrow enough to propose 2–3 concrete approaches.

**Sourcing options:** If an upstream idea-bank doc exists, draw multi-choice options from its honed candidates. If not, draw from canonical patterns + existing codebase abstractions.

**Red-team hook (`--red_team` only).** After the user answers a clarifying question, before composing the next one, dispatch the redteam in **Mode B** on the tentative pick (Step-2 lens subset: `rt-boundary` + `rt-failuremode` + `rt-yagni-scope` — the three that can fire pre-commitment). Fold any rigor-hole into the pick; turn each taste-fork into the *next* Socratic question. The critic never picks a taste-fork for the user.

### Step 3: Inventory existing abstractions (cross-link, don't duplicate)

**Rule:** Before proposing approaches, grep for existing 2+ subclass families in the relevant domain.

This step **applies the discipline documented in [`architecture_philosophy`](../architecture_philosophy/SKILL.md) — *Coupling & Discovery* sections (*Node Retrieval & Coupling*, *Interface Usage*)**. Don't restate the rules; surface them as a step in the design process:

- **First move:** run `mcp__plugin_semantic-search_semantic-search__search` against the relevant abstraction. Works for known type names (`"BBDataSig"`, `"IIdentifiable"`, `"PoolableProjectileBehavior"` — returns the class declaration in one shot) AND for fuzzy domain queries (`"synergy resolution"`, `"effect lifecycle"`, `"ingredient trait composition"`). Especially useful for abstractions whose names don't follow `abstract*` / `I*` conventions. See CLAUDE.md §9.
- **Then:** LSP `findReferences` on the candidate base type to enumerate all subclasses / implementers (semantic-search returns the declaration; LSP returns every usage — the latter is what reveals the 2+ subclass family).
- **Backup:** Grep for `abstract class` and `interface I` patterns in `{{PROJECT_NAME}}/` and `Jmodot/` if semantic-search misses something.
- Identify which existing hierarchies a new design could extend instead of inventing parallel abstractions.

**Cross-reference:** `feedback_inspect_existing_abstractions_first.md` — *"if a base + 2+ subclasses exist, extend it. Inventing a parallel abstraction is usually wrong."*

**Inventory-currency check (gate before Step 4).** Re-run inventory subset if any risk-factor holds: session has spanned >24 hours from initial Step 3 fetch; `git status` shows uncommitted upstream work or behind-count; submodule pointers may have drifted (`git submodule status` — verify Jmodot HEAD matches parent-repo expected). Stale inventory produces Step 4 approaches modeled on deleted/renamed types or missing newly-shipped infrastructure → Section 4 rewrite cost when the divergence surfaces mid-Section-5. `archive_git_submodule_pr_merge_strategy.md` (auto-memory) covers submodule-pointer-drift gotchas; trust test-file `using` imports over directory existence when verifying greenfield-vs-shipped status.

### Step 4: Propose 2–3 approaches with trade-offs

**Rule:** Each approach gets explicit trade-offs and a recommendation. Never present a single "obvious" answer — the comparison is what makes the choice legible.

Format:

```
Approach A — <one-line summary>
  Trade-offs: <pros> // <cons>
  Code shape: <high-level — 2-3 file paths or class names>
  Recommendation: <yes/no/conditional>

Approach B — ...
Approach C — ...
```

If you can only think of one approach, that's a signal — either the design is well-constrained (no real alternatives) or you haven't explored the space enough. Push for at least one alternative; if genuinely none, say so explicitly.

**Sourcing options:** When an upstream idea-bank doc exists, the 2–3 approaches are typically *clusters of related candidate ideas* (e.g., "Approach A: data-driven Resource-family per category" might bundle 4–5 idea-bank entries; "Approach B: registry + dispatcher" bundles 4–5 different ones). The approaches are architectural framings; the idea-bank entries are the specific instances those framings would generate.

**Red-team hook (`--red_team` only).** Once the approaches are drafted, dispatch **Mode B** on them (Step-4 subset: adds `rt-abstraction`, plus `rt-testability` if an approach already names test surface). This is the richest interleave seam — approaches are the first concrete shapes the abstraction/boundary lenses can attack. Fold rigor-holes into the trade-off comparison; surface taste-forks as the choice the user makes *between* approaches.

### Step 5: Present design section-by-section, get approval per section

**Rule:** Do not present the full design as one wall of text. Walk through sections (e.g., Data Model → Lifecycle → BB Keys → Tests → Integration Points). Get approval per section before moving on.

**Red-team hook (`--red_team` only).** After a section is drafted, before its per-section approval, dispatch the **full 5-lens** Mode B panel on that section (it now has committed shape — `rt-abstraction` and `rt-testability` can finally bite). Rigor-holes → revise the section before presenting; taste-forks → fold into the section's approval decision; a dead-end halts the brainstorm for a reframe.

**Verbatim port discipline:** when sections are agreed in chat, port them **1:1** into the design doc. Do NOT silently digest the chat-form into a tighter file-form. See `feedback_no_unilateral_condensation.md` — *"chat content IS the spec when the user says 'save to file'."*

If the user pushes back on a section, treat it as a decision to make, not a clarification to dismiss. Update the section, get approval, then continue.

**Expect 1-2 substantive upstream reopenings per session.** Late-section content (Sections 4-5 — architecture detail, Parts decomposition) routinely surfaces assumptions baked into early-section (1-3) identity locks. Reopening Sections 1-3 from Section 5 feedback is correct behavior, not failure — the alternative is shipping a self-contradicting design. Update the upstream section, get re-approval, propagate downstream implications. If a session reopens 3+ times on the same Section 1 lock, the locked decision was wrong — escalate to a Socratic re-pass rather than continuing to patch.

**Checkpoint to scratch ledger.** **[Shared]** See [`_brainstorm_shared/common.md §8`](../_brainstorm_shared/common.md) for path, append cadence, format rules, consumption, and lifecycle. Per-section bullet schema for this skill:

   ```markdown
   ## Section <name>
   - Approach chosen: <A/B/C — one-line rationale>
   - Key commitments: <2–4 bullets — concrete decisions made (file paths, class names, lifecycle choices)>
   - Open follow-ups: <Parts demoted to *-pending or *-rework state, if any>
   ```

   Append after each design section is approved. Worker reads it as `reference_files` in the final `write_doc` call (Step 6).

**Final mandatory phase: Author Parts for the roadmap.** After all design sections are approved, decompose the design into Parts per the schema in [`_brainstorm_shared/common.md §6`](../_brainstorm_shared/common.md). Each Part becomes a row in the topic-folder `roadmap.md` (created or updated by `/update_roadmap` in Step 8). Every architectural claim in the design body maps to exactly one of: {a Part on the roadmap, an explicit worklog deferral, abandoned with stated reason}.

For each Part:

- Set **State** per the [common.md §6.3 vocabulary](../_brainstorm_shared/common.md) (see *Part-readiness gate* below).
- Enumerate **Deps** (other Parts; cross-folder via `../<folder>/roadmap.md#<part>`).
- Provide **Trigger** per [common.md §6.10](../_brainstorm_shared/common.md) (required for `*-pending` / `*-rework` / `workshop-pending` / `user-owned` States; content shape varies by State — see §6.10 sub-table).
- Set **Source** to the design-doc section that committed this Part.
- Propose initial **Pos** — integer if dep-ordered; same Pos for parallel-safe Parts; `—` if un-sequenced.

**Procedural order for the Part-authoring sub-procedures** (each `####` subsection below covers one in detail):

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

#### PR-grouping guard (run BEFORE the readiness gate)

If the design body contains a pre-existing grouping section (sessions, PR batches, milestones, "recommended session grouping"), treat it as a PR-batching strategy — NOT the Part list. Author Parts fresh against the 5-criterion gate, item-by-item. A PR-batched session may legitimately split into multiple Parts; a single Part may span items from multiple PR batches. The two axes can disagree by design. See `feedback_pr_grouping_is_not_part_decomp.md`.

#### Arch-pending consolidation litmus (run BEFORE authoring 2+ `arch-pending` Parts)

If about to produce 2+ `arch-pending` Parts, check whether their open questions roll up to one. Symptoms: each fork is "what's the X pattern for subsystem Y?" → rollup is "what's the canonical X?" (one matrix); same KIND of decision across different types → one matrix; same `Source` cell across forks → one session. **Litmus:** *"If I held one session asking 'what's the canonical X?', would all forks resolve?"* Yes → consolidate; push per-fork inventory into the consolidated Trigger per *Trigger format* below. Inverse of the *Compound-+ name split litmus* — that catches under-splitting at plan-pending tier; this catches over-splitting at arch-pending tier (the *Plan-pending cohesion litmus* below is the plan-tier merge counterpart). See `feedback_arch_part_sizing_axis.md`.

#### Downstream-of-fork guard (run AFTER the readiness gate)

Don't author Parts whose shape depends on a `*-pending` sibling's output — that's the future session's job. Push inventory into the `*-pending` Part's **Trigger** instead (see *Trigger format* below); the future session inherits the inventory, not the shape. **Litmus:** *"If the upstream `*-pending` session decided differently, would this Part change shape?"* Yes → speculative; remove. See `feedback_arch_part_sizing_axis.md`.

#### Single-concern-split guard (run when one concern touches 2+ Parts' surface)

When a single cross-cutting concern — a cleanup, a migration, a retirement of a dead/legacy surface — spans the surface of 2+ Parts, it MUST have **one named owner Part** (or an explicit purge-first → consume-after sequence via Deps). Never split it so that a slice is left unowned, especially not as a side-note on a Part framed as "mechanical." A downstream Part told its job is mechanical relocation/eviction will faithfully *propagate* an un-purged surface rather than remove it — multiplying the contamination across every target the mechanical Part writes to.

- **Litmus:** *"If the owning Part is skipped or its plan_check doesn't fire, does a later 'mechanical' Part move/copy the un-resolved surface instead of deleting it?"* Yes → the concern is split with an unowned slice; assign one owner or sequence purge-first.
- **Empirical:** 2026-05-16 foundation arch split the PvP-GameUI purge across P2 ("PvP-shaped fields"), P11 ("mechanical eviction"), and Open Questions ("dead-code purge, separate from P11"). P11 would have relocated a still-PvP-wired `GameUI` (GameOverUI instance + "First to N" label) into per-arena scenes — propagating, not purging — had P2's `/plan_check` not caught it. See `feedback_arch_part_sizing_axis.md`.

#### User-owned question (ask once per Part, BEFORE the 5-criterion gate runs)

Is this Part's execution inherently user-domain (spatial design, manual content authoring, taste-driven tuning) such that no agent phase applies? Explicit yes → mark `user-owned` (per `_brainstorm_shared/common.md §6.3`), Trigger names the user deliverable, skip the 5-criterion gate (orthogonal). No → run the gate as normal. Hard-but-automatable stays `plan-pending`. Escape-hatch litmus: *"inherently user-domain"* (spatial, taste, manual authoring) ≠ *"hard for the agent right now"*.

#### Part-readiness gate (when can a Part be authored with State=`plan-pending`?)

A Part may be marked `plan-pending` (ready for Plan Mode + impl) only when ALL FIVE criteria hold:

1. **Files-nameable** — the design commitment names concrete files OR extends classes inventoried in Step 3. *"The X subsystem"* fails; *"new `StaminaComponent.cs` + extends `Jmodot.AI.HSM.Transitions.TransitionCondition`"* passes.
2. **Tests-writable** — the test plan translates DIRECTLY to one of:
   - Logic-Domain: one `[TestSuite]` + 2–5 `[TestCase]` method names, OR
   - Gameplay-Domain (per CLAUDE.md *Hybrid TDD*): an `ISceneRunner` integration plan + a playtest verification rubric.
3. **No-hidden-design** — every claim has lifecycle, owner, init-timing resolved in the doc body. *"X will do Y"* with X's owner unstated fails.
4. **All-new-types-specified** — either extends an existing 2+ subclass family (per Step 3 inventory), OR introduces new types whose shapes (fields, base class, key methods) are FULLY specified in the doc body. Greenfield subsystems legitimately introduce multiple co-designed types — that's fine if all shapes are specified.
5. **Bounded-scope** — the Part touches ≤2 subsystems per the [`project_subsystems` registry](../project_subsystems/SKILL.md), ≤4 hours of focused work estimate, and Step 3's abstraction inventory revealed no further family-extension surprises.

**Compound-`+` name split litmus.** Compound names ("Foundation + Refactor", "Authoring + Integration") are a split-candidate signal. Before authoring as a single Part, ask: *"Is this one cohesive unit or two stapled together?"* When in doubt, split.

| Criteria passing | State assignment |
|---|---|
| **All 5** | `plan-pending` — ready for Plan Mode |
| **3-4** | `arch-pending` — gap is design-shaped (one criterion open); name the open piece in Trigger |
| **0-2** | `arch-pending` (architectural fork) OR `idea-pending` (creative fork) — Trigger names the open piece |

The 0-2 case catches Parts masquerading as impl-ready when their open work is genuinely design. The 3-4 case is the most common "almost ready" state — a single open piece blocks promotion to `plan-pending`.

**Trigger format safety-net.** `/update_roadmap` Step 3 emits soft warns for malformed Triggers across every State-category per the [common.md §6.10 sub-table](../_brainstorm_shared/common.md). This catches standalone transitions and hand-edits that bypass this skill's authoring gate; in-skill authoring (procedural step 6 above) should produce conforming Triggers before the validator fires.

**Iterative narrowing.** When a Part is marked `*-pending`, the next brainstorm session for that fork starts the narrowing pass. Stop-condition for the chain: clarifying questions feel forced, or proposed approaches differ only in cosmetic detail — at that point more brainstorming adds Socratic overhead without sharpening the design.

#### Plan-pending cohesion litmus (run AFTER the readiness gate, when a design yields 3+ sequential `plan-pending` Parts)

The readiness gate is sizing-*asymmetric* — criterion 5, the Integration-touch-points ≥3 rule, and the Compound-`+` split litmus all guard the UPPER bound; nothing guards the lower. A design decomposed by **type-surface layer** (data-types → builder → rules → config → seam → algorithm) yields a chain of `plan-pending` Parts that each pass all 5 criteria individually yet are collectively over-split: thin declarative Parts front-loaded before the one behavioral Part, each costing a full plan→TDD→review cycle for little production code. This is the plan-tier mirror of the *Arch-pending consolidation litmus* and the counter-weight to the split-biased *Compound-`+`* litmus.

Scan any run of sequential `plan-pending` Parts; **merge** adjacent ones matching the **thin-and-coupled** signature:

- **Thin** — production surface is mostly declarative (abstract bases, structs, config Resources, enums); the test artifact dwarfs the production code.
- **Coupled** — its types exist mainly to feed the next Part (the next Part's API consumes them directly), OR it shares a design decision with its neighbor (resolving one forces resolving the other).

**Litmus:** *"Would one Plan Mode session naturally plan-and-build these adjacent Parts together, because each is thin and exists mainly to feed the next?"* Yes → merge into one buildable unit.

**Keep split only for a stated reason** (record it in the Part's Source/Trigger): independent reuse beyond the next layer; isolated risk worth shipping/verifying alone (a substantial layer with its own deep test surface — e.g. a builder); a framework/consumer review boundary; genuine standalone size.

**Not in conflict with Compound-`+`:** that splits ONE Part bundling *unrelated* concerns; this merges SEPARATE Parts that are thin slices of the *same* concern, artificially layered. Discriminator: unrelated-bundle → split; thin-coupled-layer → merge.

**Size-ceiling fallback:** if merging would breach criterion 5's ≤2-subsystem / half-day ceiling, keep the Parts split but **plan the cohesive cluster in one `/plan_part` session** rather than one `/plan_drive` per thin Part — the planning overhead is the real cost, not the Part boundary. (Unit of *shipping* stays small; unit of *planning* becomes the cluster.)

See `feedback_arch_part_sizing_axis.md`.

#### Integration touch points (required field for `plan-pending` Parts)

Each Part authored at `plan-pending` enumerates the cross-subsystem call sites or seams it'll touch, referencing `project_subsystems` IDs. Format: bullet list of `<subsystem-id>: <touch-point description>`. Lives in the design doc (the source linked from the Part's `Source` cell), not the roadmap Parts table.

If touch-point count **≥3 distinct subsystem boundaries**, scope-review flag: either split the Part into smaller Parts (each scope-bounded) OR audit the integration shape now (additional Socratic pass). Crossing 3+ subsystem boundaries is the canonical scope-creep failure mode — see `project_subsystems`' *Cross-cutting Flows* section for known examples (Spell cast touches 5 subsystems; not a single-session impl).

#### API + Test Pin (per `plan-pending` Part, BEFORE Step 8)

For every Part declared `plan-pending`, write the content criteria 2 and 4 demand into the doc body BEFORE invoking `/update_roadmap`:

- **Criterion 2** → 2–5 `[TestCase]` method names per `[TestSuite]` (Logic-Domain) OR `ISceneRunner` test method names + playtest rubric (Gameplay-Domain). Prose descriptions like *"tests state-transition validity"* do NOT satisfy this — write `IsTransitionValid_MainMenuToHub_ReturnsTrue()`.
- **Criterion 4** → C# class signature (fields + key methods) for every new type. *"`GameLifecycleManager` autoload owning lifecycle state"* does NOT satisfy this — write `public partial class GameLifecycleManager : Node { public GameLifecycle CurrentState { get; private set; } public void RequestTransition(GameLifecycle target, ...); ... }`.

If you can't write them, the Part is `arch-rework` with Trigger naming the missing API surface. Naming a class and saying *"methods TBD in Plan Mode"* is the canonical failure shape — it passes the criteria's letter while violating their spirit. **Litmus:** would a future implementer have to invent method signatures + test names, or can they read them verbatim from this doc?

This sub-procedure is the enforcement teeth for the gate. The Step 5 gate criteria define WHAT's required; this step requires WRITING it BEFORE state declaration goes to `/update_roadmap`. Without this enforcement, gate criteria are aspirational — the agent declares plan-pending against criteria the doc body doesn't actually satisfy.

#### Plan Mode handoff requirement

When a `plan-pending` Part is handed to Plan Mode, the FIRST action of Plan Mode MUST be to enumerate the full set of files to be modified. If the file list cannot be bounded ("...and possibly some others"), Plan Mode HARD-STOPS with feedback: *"Part scope is unbounded — split or kick back to arch brainstorm with the discovered cross-cutting."*

This is the failsafe for criterion 1 (files-nameable) drifting between brainstorm time and Plan Mode time. Arch brainstorm names files; Plan Mode verifies. Drift = scope-creep alarm. Don't proceed past the unbounded signal.

**Recommended entry recipe:** invoke [`/plan_part <part-name>`](../../commands/plan_part.md) at the top of the Plan Mode session. The command loads the design surface verbatim (Source-cell section + API + Test Pin + Integration touch points), cross-references the design's named files/types/families against current codebase state, and classifies any divergence as **macro** (HARD-STOP, kicks back to `arch-rework`) or **micro** (surfaced in the briefing for Plan Mode to resolve during drafting). The briefing it emits IS the in-context handoff packet — `/plan_part` operationalizes this requirement so the file-list-bounded gate and the "design is approved, don't redesign" contract are enforced rather than aspirational.

**Autonomous alternative — `/plan_drive`.** To drive a `plan-pending` Part to an approval-ready plan *unattended*, invoke [`/plan_drive <part-name>`](../../commands/plan_drive.md) instead of entering Plan Mode by hand — it wraps `/plan_part` + Plan Mode + `/plan_check` into one brief→draft→audit→converge loop, halting only at the `ExitPlanMode` approval gate (or a halt valve). Use `/plan_part` directly for hands-on planning; `/plan_drive` when you want the loop run for you. (This is the forward seam of the pipeline: `/architecture_brainstorm` → `/plan_drive` → approval → `/part_execute`.)

**Hard-stop remediation.** When Plan Mode hard-stops a `plan-pending` Part, route per the table:

| Situation | Move |
|---|---|
| Split lines known | `/update_roadmap split <part> into <a>, <b>` — children inherit gate-resolved States |
| Split lines unknown (or also under-specified) | `arch-rework`, Trigger names the split charter ("identify split boundaries") |
| Under-specified only (size fine) | `arch-rework`, Trigger names the missing piece |
| Inherently user-domain | `user-owned`, Trigger names the user deliverable |

#### Cardinality and recommended starting

- **Recommended starting Part** — name the Part that should be tackled first, with one-line rationale (foundation / dependency root / unblocks most downstream). Required when 2+ Parts produced.
- **Parallel-safe groups** — Parts at the same Pos share that depth and can be developed simultaneously after their common deps land.
- **All-`*-pending` outcome is valid** — if the design produces zero `plan-pending` Parts, that's the design honestly acknowledging more brainstorming is needed before implementation. State this explicitly; Step 8's `/update_roadmap` will record the `*-pending` Parts and trigger the next narrowing session(s).

#### MVP recommendation (optional — fires when Parts ≥ 5 AND top-level roadmap AND no MVP section)

If ALL THREE hold:
1. The design produced **5 or more Parts**
2. The target `roadmap.md` is a **top-level roadmap** (frontmatter has NO `parent-roadmap` field per common.md §5.1) — sub-roadmaps are forbidden from carrying MVPs per [common.md §6.11](../_brainstorm_shared/common.md)
3. The target roadmap has no `## MVP Checkpoints` section yet

…surface the recommendation to the user as the last item in the Parts-approval message:

> *"This brainstorm produced N Parts. Consider authoring MVP Checkpoints via `/mvp_plan` to frame them as playable milestones (per common.md §6.11). Skip if every Part's design-doc section already names its playtest moment."*

**Do NOT inline-author MVPs here.** Milestone-narrative thinking is a different cognitive mode than per-Part Socratic — separating it preserves Step 5's focus. The recommendation surfaces the option; the user invokes `/mvp_plan` after this brainstorm completes (or defers / declines). For roadmaps with 1-4 Parts, skip the recommendation — MVPs are usually redundant at that scale (the Parts themselves are the milestones). **For sub-roadmaps: never surface the recommendation** — sub-roadmap milestone narrative lives on the parent roadmap's MVPs which reference this sub-roadmap's Parts. If a sub-roadmap appears to need its own milestone framing, that's a signal the parent MVPs need expansion / refinement, not that the sub-roadmap should grow its own MVP section.

Get approval on the full Parts list before invoking `/update_roadmap` in Step 8.

### Step 6: Save the design doc

**[Shared]** Path tiebreaker + folder-per-topic + frontmatter conventions — see [`_brainstorm_shared/common.md` §5](../_brainstorm_shared/common.md). Spawn-placement decisions for any child docs — see [`§5.1`](../_brainstorm_shared/common.md) (applied at Step 1).

**Architecture-doc-specific conventions:**
- Folder + filename per `common.md §5`. **The save path is fixed by the Step 1 spawn-placement decision (§5.1) — resolve it there, then map to a path here. Each placement is a distinct case; do NOT default to the parent folder:**
  - **Direct-arch invocation** (no upstream cluster routing, fresh topic): `YYYY-MM-DD-<kebab-case-topic>/arch.md`
  - **Cluster-scoped** (downstream of an `ideas.md` Per-Cluster Routing): `<parent-topic-folder>/arch-<cluster-slug>.md` (same parent folder)
  - **Same-folder sibling / follow-up** (§5.1 *sub-component* — criterion 1 or 3 fails): `<parent-topic-folder>/arch-<slug>.md` or `<parent-topic-folder>/arch-<slug>-followup-N.md`
  - **Deeper-scope submap** (§5.1 *child subfolder* — own brainstorm, parent-confined audience): **`<parent-topic-folder>/<sub-slug>/arch.md` — inside a NEW child subfolder, NOT the parent folder.** ⚠ This is the case that gets mis-saved to the parent top level. The design doc AND the child `roadmap.md` (the latter created by `/update_roadmap` in Step 8's multi-roadmap case) BOTH live in `<parent-topic-folder>/<sub-slug>/`. Create the subfolder if it doesn't exist.
  - **Cross-cutting sibling folder** (§5.1 all 3 criteria met): `YYYY-MM-DD-<sibling-slug>/arch.md` — a NEW top-level topic folder at the same depth as the parent.
- Frontmatter: `phase: architecture_brainstorm`
- Status taxonomy:
  - **`active-brainstorming`** — in progress
  - **`brainstorming-complete`** — all design sections finalized; Parts authored and ready for `/update_roadmap`. Whether the resulting roadmap Parts are all `plan-pending` or mix `plan-pending` with `arch-pending` / `idea-pending` is recorded in `roadmap.md`, NOT in this doc's status — separation of concerns.

**Roadmap.md is NOT saved here — load-bearing guardrail.** Step 6 saves the DESIGN DOC only — `arch-<slug>.md` / `arch.md` / `arch-<slug>-followup-N.md` per the filename table above. The topic-folder `roadmap.md` (top-level OR child sub-roadmap from a *deeper-scope* spawn per [`common.md §5.1`](../_brainstorm_shared/common.md)) is owned by `/update_roadmap` in Step 8 — DO NOT create roadmap.md via `write_doc` / `write_code` / direct `Write` here, in Step 7, or anywhere except as Step 8's invocation output. Bypassing the executor silently drops Trigger validators ([`common.md §6.10`](../_brainstorm_shared/common.md)), derived-view recomputation ([§6.5](../_brainstorm_shared/common.md)), Mermaid deterministic regen ([§6.4](../_brainstorm_shared/common.md)), and the revision-log discipline ([§6.7](../_brainstorm_shared/common.md)). Single-executor pattern applies recursively: every roadmap.md at every depth routes through `/update_roadmap`. The mistake-mode this guardrail prevents: a worker-prose `write_doc` call producing a sub-roadmap inline during this Step instead of waiting for Step 8's two-invocation sequence (parent transition + child creation).

**Worklog discipline (two-category integrity):** the brainstorm doc IS the design deliverable. Two homes for things surfaced in this brainstorm:

- **Architectural commitment** → a Part on `roadmap.md` (any State per §6.3 — `plan-pending`, `arch-pending`, `idea-pending`, `workshop-pending`). Owned by this brainstorm-topic-folder's roadmap.
- **Tactical deferral / out-of-scope punt** → worklog (e.g., "lift mechanic — revisit post-MVP" with no design implications).

Don't include a "worklog items to add" section listing architecture work surfaced inside the brainstorm — that's category confusion. Architecture commitments live on the roadmap; worklog is for tactical noise.

**Design doc structure expectations:**
- Sections for each architectural commitment, including the test plan and (for `plan-pending` Parts) the Integration touch points enumeration from Step 5.
- An *Open Questions* section at the end for genuinely-unanswered items raised during Socratic phase (Step 2) — these are NOT roadmap Parts; they're meta-questions about the design itself. **Open Questions is for design *meta*-questions, NOT a parking lot for deferred scope decisions that belong to a named Part.** If a note bears on a specific Part's scope (what it adds, migrates, or *deletes/retires*), it belongs in that Part's source section as committed scope OR as an explicit worklog deferral — not parked here. Litmus: *"Does resolving this change what a named Part builds or removes?"* Yes → it's Part scope, commit it; No (it's a design-shape question with no Part owner) → Open Questions. Parking Part-scoped decisions here defeats the No-orphans check below and the decision silently drops at the `/plan_part` handoff (the design surface tool reads Open Questions but a parked-not-resolved decision reads as ambient context, not an action).
- The roadmap is NOT embedded in this doc — it lives in `roadmap.md` in the same folder, owned by `/update_roadmap`.

### Step 7: Spec self-review

Before declaring the design complete, scan the doc for:

- [ ] **Placeholder scan** — no `TBD`, `TODO`, `<fill in>`, `???` markers. Either resolve or escalate to the user as an open question.
- [ ] **Contradiction check** — sections shouldn't claim opposing things (e.g., *"stamina is a resource"* in §2, *"stamina is a cooldown"* in §5).
- [ ] **Ambiguity check** — every concrete claim has a concrete file path or class name. *"It uses the existing event system"* is ambiguous; *"It emits `WizardStaminaChanged` via `EventBus.Publish` (see `Jmodot/Implementation/Events/EventBus.cs:42`)"* is concrete.
- [ ] **Scope check** — the design doc matches the topic. If discussion drifted, either expand scope (and the topic) or trim the off-topic sections.
- [ ] **Parts authored** — Step 5 produced a list of Parts, each with State (per common.md §6.3 vocabulary), Deps, Source link back into this doc, and (for `plan-pending` Parts) Integration touch points enumeration. At least one Part is required; zero-Part outcome = doc terminates without bridging to either implementation or further design work.
- [ ] **No-orphans check** — every architectural claim in the design body maps to exactly one of: {a Part on the roadmap, an explicit worklog deferral, abandoned with stated reason}. No design claim with no path forward. If a section says *"the system will use X"* but X is neither committed in a Part nor explicitly punted to worklog nor abandoned, that's an orphan — promote it to a Part or punt it explicitly. Orphans are the failure mode that produces *"we wrote a design doc and then nothing happened"* months later. **Open Questions and Memory-Anchor footnotes do NOT count as a "path forward" for a Part-scoped scope decision** — a deletion/migration/retirement that bears on a named Part but lives only in Open Questions or as a cited-but-un-actioned memory anchor is an orphan. (Empirical: 2026-05-16 foundation arch cited `project_pvp_retired` in Memory Anchors + parked the GameUI PvP purge in Open Questions; neither committed it to P2's scope, so the purge survived to `/plan_check`.)
- [ ] **Seam-ownership check** — for every item the design explicitly scopes OUT to a neighboring or future brainstorm, name the interface **contract** at that boundary and assign an **owner**: a Part on this roadmap, an existing Part on a cross-referenced roadmap (cite it), or an explicit worklog deferral. A claim that defers work to *"their own Parts"* / *"a future session"* is an orphan **unless that Part demonstrably exists**. Clean scoping *creates* seams; seams need owners too. (Empirical: 2026-05-16 foundation arch deferred InRun scene-wiring to "their own Parts" that were never authored, and left the player-lifetime/scene-transfer seam between the lifecycle layer and the deferred dungeon-floor arch unowned — surfaced only in a later audit.)
- [ ] **No-speculative-downstream check** — every Part depends only on (a) Parts in this roadmap at `plan-pending` or stronger, OR (b) already-shipped infra. No Part may depend on `*-pending` output (that belongs to the future session and authors its own Parts). Litmus per Step 5 *Downstream-of-fork guard*: *"If this Part's upstream session decided differently, would this Part change shape?"* Yes = speculative; remove. See `feedback_arch_part_sizing_axis.md`.
- [ ] **Readiness-gate audit** — for each Part with State=`plan-pending`, verify the 5 criteria from Step 5 actually hold. If any fails, demote to `arch-pending` with the open piece named in Trigger.
- [ ] **Plan-pending cohesion audit** — for any run of 3+ sequential `plan-pending` Parts, the *Plan-pending cohesion litmus* (Step 5) ran: thin + tightly-coupled adjacent Parts were merged, or each kept-split Part states its keep-separate reason. Catches type-surface-layer over-split (clean dependency ordering ≠ right Part sizing).
- [ ] **Integration touch-points audit** — for each `plan-pending` Part, verify the touch-points list ≤2 distinct subsystem boundaries (per `project_subsystems`). ≥3 boundaries = mandatory split or additional Socratic pass before invoking `/update_roadmap`.

**Mode A adversarial pass (drafted design).** For architecturally-loaded designs, run [`/architecture_brainstorm_redteam`](../../commands/architecture_brainstorm_redteam.md) in its standalone **Mode A** before the user-review gate (its `--auto` mode for topics you trust the agents to harden). It red-teams the *drafted* design for rigor holes and **classifies findings rigor-hole (fold back into the doc) vs taste-fork (surface to the user as Socratic seeds) — it never decides taste.** This is the full-doc counterpart to the per-step **Mode B** hooks that fire during Steps 2/4/5 when the skill is invoked with `--red_team`. Both AUGMENT the Socratic flow; neither replaces Steps 2/5.

**[Shared] Rationale spot-check** — see [`_brainstorm_shared/common.md` §2](../_brainstorm_shared/common.md).

**[Shared] User review gate** — see [`_brainstorm_shared/common.md` §4](../_brainstorm_shared/common.md).

### Step 8: Invoke `/update_roadmap`

After the design doc is saved and the user has approved both doc and Parts list, invoke `/update_roadmap` with:
- The saved design doc path (so the command knows which topic folder owns the roadmap)
- The Parts list from Step 5 (mapped per §6.3 vocabulary)
- Any spawn-placement decision from Step 1 (same-folder / child-subfolder / sibling-folder) for the `Spawned sub-brainstorms` section.

**Multi-roadmap case (`child-subfolder` spawn that decomposes an existing parent Part)** — invoke `/update_roadmap` **TWICE in sequence**. Single-roadmap batch-diff semantics stay intact; user approves each batch separately.

| Invocation | Target roadmap | What it does |
|---|---|---|
| **1 — Parent** | `<parent-folder>/roadmap.md` | Transition parent Part to `submap-pending` with Trigger=`"Decomposed into <child-folder>/roadmap.md (N child Parts)."` per common.md §6.10. Add `Spawned sub-brainstorms` entry pointing to the child roadmap. Append revision log entry. Parent Part's Deps + Source + dependent edges PRESERVED (only State + Trigger change). |
| **2 — Child** | `<parent-folder>/<child-slug>/roadmap.md` (NEW file) | Create child sub-roadmap from common.md §6.1 schema with `parent-roadmap: ../<parent-folder>/roadmap.md`, `scope-level` inherited from parent, fresh `created: <today>` / `last_revised: <today>`. Populate Parts table from this brainstorm's Step 5 output. Cross-roadmap deps preserved with `(parent) <Part>` prefix or `[[../<parent>/roadmap#Part]]` wikilink. If 2+ cross-roadmap deps exist, also author `## Cross-roadmap dependencies` (§6.12). |

**Do NOT inline-author the child sub-roadmap via `write_doc` / `Write`** — per CLAUDE.md NEVER rule + Step 6 guardrail above, the child roadmap.md routes through `/update_roadmap` exactly like the parent. Invocation 2 IS the canonical authoring path.

`/update_roadmap` is the single executor for roadmap.md edits. It runs in batch-propose mode — assembles the full diff (Parts table + Mermaid + derived views + revision log entry), shows it, applies on single approval. Do NOT hand-edit `roadmap.md` from within this skill; the command owns that surface.

If `/update_roadmap` reports no parent `roadmap.md` exists, it'll propose creating one — confirm and proceed. The initial creation pulls frontmatter from the design doc's frontmatter (`topic`, `scope-level`, `scope` → `pp-game` / `jmodot-framework`).

---

## 5. MCP-Offline Policy

**[Shared]** Obsidian MCP offline → non-event (native vault `Read`/`Write`/`Edit`). **ai-worker MCP offline → fallback to a Haiku subagent for any `read_files` / `read_web` call** (Step 1 existing-doc digest, Step 2 multi-section spot-check, final `write_doc` synthesis). Do NOT degrade to chained native reads. See [`_brainstorm_shared/common.md` §3](../_brainstorm_shared/common.md) for the substitution shape.

---

## 6. Anti-Patterns

| Rationalization | Reality |
|---|---|
| "The design is clear enough; let me just start coding" | The Hard Gate exists for a reason. If the design *is* clear, Step 5 will move fast. Don't skip the doc. |
| "I'll write the code first and document the design after" | That's `/create_obsidian_design_doc`'s job (post-hoc retrospective). The brainstorming doc is upfront — it *constrains* the implementation, not the other way around. |
| "Section-by-section approval is overhead — let me show the full design at once" | Walls of text get rubber-stamped or rejected wholesale. Per-section approval is what makes design decisions traceable. |
| "Only one approach makes sense here" | If you can't articulate alternatives, you haven't fully explored the design space. Find at least one alternative, even if you reject it. |
| "Let me also implement a small part to see if it works" | That's not brainstorming — that's prototyping. Different mode. If you want to prototype, say so explicitly and switch modes. |
| "The user already approved the topic; I can skip clarifying questions" | The user approved the topic, not the shape. Clarifying questions narrow the shape. |
| "Existing design doc is outdated; let me start fresh" | Don't. Read the existing doc, identify what's stale, propose updates per `doc_before_writing` discipline. Starting fresh discards prior decisions. |
| "Force this seed-stage Part to State=`plan-pending` so it 'feels complete'" | The `*-pending` states exist *precisely so you don't have to* fake commitment. Acknowledging a fork via `arch-pending` / `idea-pending` is a stronger design move than papering over it with hand-wavy fields. Litmus: *can a future implementer write the test names from this Part's content alone?* If no, it's `arch-pending` (or `idea-pending`) — Trigger names what's missing. Honesty is not failure. |
| "This Part is big / input-coupled — leave it `arch-pending` to be safe" | `arch-pending` means the *design* is genuinely unresolved — NOT that the impl is large or that you haven't investigated the coupling yet. Run the Step 3 inventory FIRST: if the coupling turns out mechanical/reference-based and you can write the Step 5 test names, it's `plan-pending` (split into multiple `plan-pending` Parts if impl-size exceeds one session). Impl-size → split, never `arch-pending`. Litmus: *"Is the gap a design decision I can't make, or work I haven't done yet?"* Only the former is `arch-pending`. (Inverse of the row above — that one guards against faking `plan-pending`; this guards against over-deferring to `arch-pending`.) |
| "I named the class + its responsibility, so criterion 4 passes" | Naming is necessary but not sufficient. Criterion 4 requires the C# class signature (fields + key methods) WRITTEN into the doc body, not just the class's name + role. Same for criterion 2 — prose test descriptions (*"tests state-transition validity"*) don't substitute for actual `MethodName_Scenario_Asserts()` strings. The *API + Test Pin* sub-procedure exists to force this. Litmus: can the next implementer read signatures + test names verbatim from the doc, or must they invent them? Invent → `arch-rework`. |
| "I'll add this design fork to worklog so it's easier to track" | Different surfaces, different signal. Worklog is tactical and transient; `arch-pending` / `idea-pending` Parts are strategic and design-bound (live on the roadmap with a Trigger). Cross-pollinating breaks both — worklog grows noise that doesn't fit its trigger model; the roadmap loses authority over its own design state. Architecture commitments live as Parts; trust the Trigger field. |
| "It's a seed idea, so I can leave the Trigger vague" | Vague Triggers defeat the purpose. The next brainstorm reads the parent Part's Trigger as ITS Step 1 existing-doc digest — vague triggers there force re-deriving the design space from scratch. State the ripening criterion concretely *now*, while context is fresh, even if you don't have answers yet. |
| "This Part touches 4 subsystems but the design is clear enough — mark it `plan-pending`" | Step 5's bounded-scope criterion (≤2 subsystems per `project_subsystems`) is the hard cap. ≥3 subsystem boundaries = canonical scope-creep failure mode (see `project_subsystems` *Cross-cutting Flows*). Either split the Part or run an additional Socratic pass — don't sneak it past the gate. |
| "I'll hand-edit `roadmap.md` while I'm in the design doc anyway" | `/update_roadmap` is the single executor for roadmap edits — it owns Mermaid regen, derived-view recomputation, validator checks, and revision log append. Hand-edits drift these layers silently. Always route roadmap changes through the command. |
| "Topic is greenfield with no candidates — I'll generate ideas inline during Step 4" | No. Idea generation belongs in `/idea_brainstorm`. Generating candidates inline here skips the divergence pipeline (filter / hone / cluster), produces inferior approaches, and loses the idea-bank artifact. Route to `/idea_brainstorm` first, then come back. |
| "Idea-bank doc exists but the candidates feel thin — let me regenerate during Step 4" | If the idea bank feels thin, that's signal to RE-RUN `/idea_brainstorm` (its revision discipline applies in-place), not to silently regenerate here. Architecture brainstorm consumes idea-bank output, doesn't replace it. |
| "The design doc has a §5a-style 'recommended session grouping' — port those as the Part list" | Different axes. PR-cohesion groups for review narrative; readiness-gate sizes for impl-units. Author Parts fresh against the gate, item-by-item. A coherent PR may still be too big for one Plan Mode session. See `feedback_pr_grouping_is_not_part_decomp.md`. |
| "Compound `+` name reads fine, no need to split" | Compound `+` is a split-candidate signal. When 4-of-N sequenced Parts carry `+` names, the porting axis is wrong — re-author against the gate. `Foundation + Refactor` is the canonical failure: bundling 4 unrelated workstreams under one Plan Mode session fails criterion 5 (bounded-scope). When in doubt, split. |
| "Existing brainstorm doc predates current gate but I'll just `/update_roadmap` the existing Parts" | Pre-gate Parts need re-gating, not re-importing. See [`common.md §1.2`](../_brainstorm_shared/common.md) (Stale-roadmap remediation) — the procedure re-runs arch Step 5 against the existing design body, then hands the fresh Part list to `/update_roadmap`. Parts inherit the wrong sizing axis silently otherwise. |
| "I already know the inventory from grep, so I can pre-list the downstream Parts of the `arch-pending` session to save it work" | Subsystem grouping, file decomposition, and Part shape are the future session's call against ITS gate. Pre-listing pre-empts that decision and locks in inventory groupings (which often change once design questions are answered — e.g., subsystems merge if their patterns are identical). Push inventory into the `*-pending` Part's Trigger per the format spec; let the future session author Parts. See `feedback_arch_part_sizing_axis.md`. |
| "Each subsystem has its own open question about pattern X, so each gets its own `arch-pending` Part" | Shared higher-order shape ("pattern per class kind?", "injection per type family?", "lifecycle per X?") rolls up to ONE arch session producing a decision matrix. Per-subsystem `arch-pending` Parts pre-decide the matrix axis IS subsystem (when it might be per-class-kind, per-protocol, per-init-timing). Apply *Arch-pending consolidation litmus* in Step 5 before authoring. See `feedback_arch_part_sizing_axis.md`. |
| "Type-surface layer decomposition is clean, so each layer earns its own `plan-pending` Part" | Clean dependency ordering ≠ right sizing. Layer-decomposition (data → builder → rules → config → seam → algorithm) front-loads thin declarative Parts before the one behavioral Part — each passes the readiness gate individually yet costs a full plan→TDD→review cycle for little code. Run the *Plan-pending cohesion litmus* (Step 5): merge thin + tightly-coupled adjacent `plan-pending` Parts. Plan-tier mirror of the arch-pending consolidation litmus; counter-weight to Compound-`+`. See `feedback_arch_part_sizing_axis.md`. |

---

## 7. Cross-references

**Shared procedures:**
- [`_brainstorm_shared/common.md`](../_brainstorm_shared/common.md) — §1 existing-doc check, §1.1 resume table, §1.2 stale-roadmap remediation, §2 rationale spot-check, §3 MCP-offline, §4 user-review gate, §5 doc path / frontmatter conventions, §5.1 spawn-placement convention, §6 roadmap.md schema (including §6.10 Trigger semantics), §7 workflow phase cardinality, §8 scratch-ledger checkpoint discipline

**Adjacent skills:**
- [`idea_brainstorm`](../idea_brainstorm/SKILL.md) — runs BEFORE this skill for greenfield topics; produces the idea-bank doc that Steps 2 and 4 consume
- [`project_subsystems`](../project_subsystems/SKILL.md) — Step 5 bounded-scope criterion (≤2 subsystems) + Integration touch points uses this skill's subsystem registry
- [`architecture_philosophy`](../architecture_philosophy/SKILL.md) — *Coupling & Discovery* sections (*Node Retrieval & Coupling*, *Interface Usage*) — Step 3 cross-link, NOT duplicate
- [`worklog_reference`](../worklog_reference/SKILL.md) — scope 1/2/3/4 definitions used by skip rules
- [`debugging`](../debugging/SKILL.md) — alternative path for bug fixes with known root cause; brainstorming is for forward design only
- [`status_effect_authoring`](../status_effect_authoring/SKILL.md) / [`refactor_procedure`](../refactor_procedure/SKILL.md) — plus any project-specific authoring skills — invoked AFTER brainstorming approval, when the approved design maps to one of these authoring surfaces
- [`testing`](../testing/SKILL.md) — design docs MUST include test-plan section (Logic-Domain TDD applies to design-mandated logic)

**Commands:**
- [`/update_roadmap`](../../commands/update_roadmap.md) — Step 8 invocation; single executor for roadmap.md edits (Parts table, Mermaid, derived views, revision log)
- [`/architecture_brainstorm_redteam`](../../commands/architecture_brainstorm_redteam.md) — adversarial red-team; **Mode A** standalone pass on the drafted design (Step 7 augment) + **Mode B** interleaved per-step passes when invoked with `--red_team` (Steps 2/4/5); classifies rigor-holes vs taste-forks, never decides taste
- `/create_obsidian_design_doc` — POST-HOC retrospective; complementary to this skill, not overlapping. Run *after* implementation ships, not instead of brainstorming.
- `/test_skill architecture_brainstorm` — adversarial validation of this skill (Mode B rule-negation).

**File-based memory:**
- `feedback_inspect_existing_abstractions_first.md` — Step 3 abstraction inventory
- `feedback_recommended_fix_means_implement.md` — post-approval inline-implementation path
- `feedback_obsidian_write_locations.md` — Step 6 PP/Jmodot tiebreaker
- `feedback_no_unilateral_condensation.md` — Step 5 verbatim-port discipline
- `feedback_no_performative_agreement.md` — Step 2 Socratic-question opener discipline
- `feedback_session_start_hook_does_not_override_skill_procedure.md` — Step 2 / Step 5 procedural-gate discipline

**Agent templates:**
- [`agents/doc_before_writing.md`](../../commands/agents/doc_before_writing.md) — §1 existing-doc check pattern
- `obsidian_conventions` skill — wikilink & heading-anchor discipline, MCP connectivity, vault write locations

**CLAUDE.md sections:**
- Planning Phase Checklist (this skill sits upstream of it; output feeds INTO it)
- Obsidian section (Read/Write constraints, tiebreaker, MCP-offline policy)
- Worklog section (small deferrals vs tracked items in TODO/)

**Plan Mode:**
- Claude Code built-in. Not documented in `.claude/`. This skill describes the **handoff**, not Plan Mode internals.
