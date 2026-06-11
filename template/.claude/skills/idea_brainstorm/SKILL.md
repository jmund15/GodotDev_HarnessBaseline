---
name: Idea_Brainstorm
description: >-
  Use BEFORE architecture decisions, when the design space needs population.
  Greenfield topics, "fun X ideas", "what creative X could exist", or
  enumeration-pressure requests ("every idea you can think of"). Produces a
  curated idea-bank doc with per-cluster honed survivors. Hand off to
  /architecture_brainstorm once the idea pool stabilizes. SKIP for mature
  domains (canonical patterns already populate the space), tactical/mechanical
  topics, or when the user already has a populated idea space.
---

# Idea Brainstorm

> **Scope:** Sits **upstream of `architecture_brainstorm`**. Idea brainstorm answers *"what could exist?"*; architecture brainstorm answers *"how should we build it?"*; Plan Mode answers *"how do we ship it?"*. Three sequential phases of design work; one skill per phase.
>
> **Not to be confused with:** [`architecture_brainstorm`](../architecture_brainstorm/SKILL.md) — that's where Socratic narrowing, 2–3 approaches with trade-offs, and Parts-authoring (with the 5-criterion readiness gate) live. Idea brainstorm produces the candidate POOL that architecture brainstorm narrows from.

## The Hard Gate

```
DO NOT INVOKE ARCHITECTURE BRAINSTORMING, IMPLEMENTATION SKILLS, OR
WRITE ANY CODE FROM WITHIN IDEA BRAINSTORM.
```

Idea brainstorm produces an idea-bank doc and STOPS. Hand off to the next phase. Skipping ahead loses the divergent-thinking value this skill exists to provide.

**User-stated priors are NOT gate violations.** Recording user-stated commitments as `[user-canon-aligned]` Findings is allowed; the gate forbids the AGENT inventing new commitments (file paths, class names, lifecycle, BB keys). Litmus: *"Did the user say this, or am I deciding it?"* Said → per-cluster Findings. Agent-invented → defer to `/architecture_brainstorm` Step 4.

---

## 1. When to Use

**Strong trigger phrases:**
- "brainstorm X ideas"
- "what fun / creative / interesting X are there"
- "help me think of more X options"
- "drip-feed ideas for X"
- "more ideas for X" / "expand the X pool"

**Enumeration-pressure phrasing is the STRONGEST trigger** (not a bypass license). When the request is wrapped in cues like *"every idea you can think of"*, *"don't filter"*, *"unfiltered"*, *"just give me ideas"*, *"throw everything at the wall"* — fire this skill, populate the idea bank via the divergence pipeline, save to the bank doc with hidden-pool reference. The chat surface stays at 3–5 honed per cluster; the doc-saved idea pool is broader (10–15); the full hidden pool exists in the agent's working context. **Inline chat-enumeration of 50+ items is the canonical Skill-bypass failure mode this clause exists to prevent.**

**Fire when ALL of these hold:**

- [ ] Topic is creative / spatial (open-ended) rather than mechanical / tactical (pricing, naming, sizing of an already-decided thing).
- [ ] No existing idea-bank or design doc covers the topic (verified via common.md §1 existing-doc check, run in Step 1).
- [ ] User has NOT already entered Plan Mode.
- [ ] Topic is not in the SKIP categories below.

---

## 2. When to Skip

| Skip case | Why |
|---|---|
| **Mature domain** — agent can list 5+ named design approaches from memory without searching | Design space is already populated by canon. Go straight to `/architecture_brainstorm`. |
| **Tactical / mechanical topic** — *"what's the right pricing for X?"* | Divergence adds noise to convergent decisions. Go straight to `/architecture_brainstorm` (or Plan Mode if architecture is settled). |
| **User explicitly says** *"skip the brainstorm, go straight to design"* | They've populated the space themselves. |
| **User in Plan Mode already** | Implementation phase; do not interrupt. |
| **Existing `ideation-complete` doc covers the topic** | §1 existing-doc check surfaces this; hand off to `/architecture_brainstorm`. |
| **Worklog scope 1 or 2** | Trivial / mechanical work; brainstorming overhead exceeds benefit. |

---

## 3. Workflow Position

```
/idea_brainstorm           /architecture_brainstorm        Implementation
─────────────────          ─────────────────────────        ──────────────
"What could exist?"        "How should we build it?"        (Plan Mode,
                                                            write code,
                                                            tests, commit)

• Scope-framing Socratic   • Architecture-narrowing
  (constraints, not          Socratic (trade-offs)
  architecture)            • 2–3 approaches with
• Per-cluster diverge        comparison
  → filter → hone          • Parts authored on
  → cluster → present        roadmap.md
• Idea-bank doc save       • Design doc save
  Status: ideation-          Status: brainstorming-
  complete                   complete
• /update_roadmap          • /update_roadmap
  applies Per-Cluster        applies authored
  Routing → Parts            Parts → roadmap.md
```

**Cardinality and flow topology** — full vocabulary (forward fan-out, cluster merge, ideation skip, reverse signal, zero-impl outcome, workshop terminate) lives at [`_brainstorm_shared/common.md §7`](../_brainstorm_shared/common.md). The above diagram is the slim "you are here" view; common §7 is the canonical reference.

Each phase has its own SKILL.md. Shared procedure surface lives in [`_brainstorm_shared/common.md`](../_brainstorm_shared/common.md).

---

## 4. The Procedure (6 Steps)

### Step 1: Existing-doc check

**[Shared]** See [`_brainstorm_shared/common.md` §1](../_brainstorm_shared/common.md) — existing-doc digest (one `read_files` call over enumerated paths) + Memory sweep (semantic-search); the abstraction-scan ask is optional for ideation.

**Two doc-hit cases — handle distinctly:**
- **Same topic** (digest doc IS for this brainstorm's topic, status-tagged): resume per common §1.1 status table. If `ideation-active` → resume mid-procedure; if `ideation-complete` → hand off to `/architecture_brainstorm`.
- **Adjacent design space** (digest doc covers a neighboring system whose prior commitments touch this topic but were not authored against it — e.g., a meta-economy brainstorm hitting a pre-existing event-system doc): proceed with this brainstorm. The doc's claims become **priors to validate**, not canon to respect. Surface competing ideas on merit; flag conflicts for `/architecture_brainstorm` to resolve. See §6 *Anti-Patterns* (this skill) — *deference-filter* row.

### Step 2: Scope-framing Socratic (lighter, often optional)

**Rule:** Optional. Use ONLY for **scope-framing** — establishing constraints the idea pool should respect (PvE vs multi, design pillar, target audience, tone, target system the ideas should slot into). Do NOT use for **architecture-narrowing** (*"single currency or dual?"*) — that belongs in `/architecture_brainstorm` Step 2 AFTER the idea pool exists.

- Multiple-choice preferred over open-ended. One question per turn.
- Stop asking when the FRAME is clear enough to populate ideas in-bounds.
- Often this step is empty — if the user's prompt already establishes the frame, skip.

**Litmus to distinguish scope-framing from architecture-narrowing:** *"Does answering this question rule out specific candidate IDEAS, or just specific architectural COMBINATIONS of them?"* If ruling out ideas → scope-framing, OK to ask now. If ruling out combinations → architecture-narrowing, defer to next phase.

**Multi-topic scope decision:** If the topic naturally splits into independent subsystems (e.g., a roguelike meta-game = currency + per-run events + almanack), default to **one combined brainstorm with multiple top-level clusters** rather than separate `/idea_brainstorm` invocations per subsystem. The cost of "one brainstorm, three clusters" is a longer single conversation; the cost of "three separate brainstorms" is duplicated Step 1 existing-doc checks, re-context-loading, and lost cross-pollination (Step 4 hybrids only surface when clusters are visible to the same pass). Switch to separate invocations ONLY when (a) the user explicitly asks, OR (b) the subsystems are independent enough that cross-pollination would be misleading (rare).

### Step 3: Idea Generation & Curation (per cluster)

**Rule:** This is the meat of the skill. Run the pipeline once per natural cluster. Clusters typically map to user-surfaced themes; new clusters require explicit *"I'm proposing a category you didn't mention"* framing.

**Hidden phases (agent context only):**

1. **Diverge** — generate 15–30 raw candidates per cluster, drawing on:
   - User-surfaced seeds (verbatim, expanded — never silently digested or condensed; see `feedback_no_unilateral_condensation.md`)
   - Domain-canon precedents (for game design: Hades, Slay the Spire, Dead Cells, Hollow Knight, Inscryption, etc.) — cite by name when used
   - Lateral / surprising candidates from adjacent genres or domains
   - Cross-pollination from other clusters already processed

2. **Filter** — apply the explicit checklist:
   - **Game-mechanics fit** — does this contradict established systems? If unsure, flag as `[mechanic-uncertain]` and surface for user confirmation rather than silently culling. Agent's domain knowledge is finite; uncertainty must be visible, not hidden.
   - **Tone fit** — does this match the game's established voice / lore?
   - **Scope fit** — can a v1 implementation reach this?
   - **Pillar fit** — does this support or dilute the design pillar?
   - **Redundancy** — is this a reskin of another candidate?
   - **Novelty** — does this contribute something the user hasn't already surfaced?

   **Not a filter criterion:** "Conflicts with claims in an adjacent prior doc." Prior-doc claims from neighboring design spaces are priors to validate, not filter gates — see Step 1 *adjacent-design-space* case + §6 *Anti-Patterns* (this skill) — *deference-filter* row. The litmus: if your cut rationale uses *only words from the prior doc* rather than words about the idea's own merit (tone/pillar/mechanic conflict), you've deferred, not filtered.

3. **Hone** — refine each survivor into one specific sentence grounded in named game systems / specific numbers / lore framing. Idea-stubs (*"+1 slot"*) become honed entries (*"+1 slot — appears as a new wedge in the radial-craft menu's outer ring; cost varies per archetype"*). A stub is divergence output; a honed entry is presentation-ready.

4. **Cluster & rank** — group survivors by sub-theme; pick the top 3–5 per cluster.

**Presented phase (chat output):**

5. **Present per cluster:**
   - 3–5 honed survivors, each with a one-line rationale (*"why this beat the cuts"*)
   - 2–3 explicit cuts named, each with a one-line rejection reason (audit transparency for the filter pass)
   - Brief note on hidden-pool size (*"started from ~N raw, kept ~M"*)
   - Bracketed marker tags per entry (canonical vocabulary — propose a SKILL edit if you need a new form):
     - **Provenance**: `[user-verbatim]`, `[user-canon-aligned]`, `[user-canon-honed]`, `[user-surfaced]`, `[canon-import: <source>]`, `[original]`, `[cross-cluster: N+M]` (Step 4 cross-pollination hybrids — names the spanning clusters)
     - **Scope**: `[scope: v1|medium|post-MVP|late-game/DLC]`
     - **Dependency**: `[depends-on: <subsystem>]`
     - **Tension**: `[pillar-tension flag]`, `[mechanic-uncertain]` (Step 3 Filter — surface for user confirmation)
     - **Cross-ref**: `[architecture: Finding N-X]`, `[see-cluster: M]`

   Move to the next cluster only after the current one is acknowledged. The user reacts per cluster, not after all clusters are dumped.

6. **Checkpoint to scratch ledger.** **[Shared]** See [`_brainstorm_shared/common.md §8`](../_brainstorm_shared/common.md) for path, append cadence, format rules, consumption, and lifecycle. Per-cluster bullet schema for this skill:

   ```markdown
   ## Cluster <N> — <Cluster Name>
   - Honed survivors: <comma-separated names>
   - Cuts: <comma-separated names>
   - Findings: <comma-separated finding IDs>
   - Key commitments: <2–4 bullets of architectural commitments made during this cluster>
   - Cross-cluster anchors: <list>
   ```

   Append after the user acknowledges the cluster (accepted survivors, swapped cuts back in, raised additional categories, or said "proceed"). Worker reads it as `reference_files` in the final `write_doc` call (Step 5).

### Step 4: Cross-pollination pass

After all clusters are processed, look for cross-cluster hybrids — ideas whose power comes from spanning two or more clusters. Present 3–5 hybrid candidates with rationale, each marked with the `[cross-cluster: N+M]` provenance tag (per Step 3 phase 5 vocabulary; the cluster numbers identify the spanning clusters).

This step is where the architecture skill later finds its richest material: hybrids often surface architectural primitives that span multiple subsystems.

**Hybrid → arch handoff.** Each accepted hybrid becomes its OWN `/architecture_brainstorm` invocation per `common.md §5.1` spawn-placement (typically same-folder via criterion-3 failure — the hybrid's design is consumed by this brainstorm-topic's parent, not by 2+ unrelated parents). The hybrid's arch session takes the hybrid's name as its cluster slug. Track via Cross-Pollination section in the saved doc (Step 5).

### Step 5: Save the idea-bank doc

**[Shared]** Path tiebreaker + folder-per-topic + frontmatter conventions — see [`_brainstorm_shared/common.md` §5](../_brainstorm_shared/common.md). Spawn-placement decisions for downstream arch sessions — see [`§5.1`](../_brainstorm_shared/common.md).

**Idea-bank doc-specific conventions:**
- Folder + filename per `common.md §5` + the §5.1 spawn-placement of this ideation. **Map the placement to a path; do NOT default to the parent folder for a sub-topic:**
  - **Fresh topic** (default): `YYYY-MM-DD-<kebab-case-topic>/ideas.md` (folder per topic; flat filename `ideas.md` inside)
  - **Deeper-scope sub-topic** (§5.1 *child subfolder* — own ideation, parent-confined audience): **`<parent-topic-folder>/<sub-slug>/ideas.md` — inside a NEW child subfolder, NOT the parent topic folder.** ⚠ Recurring mis-save target. Create the subfolder if it doesn't exist.
  - **Cross-cutting sibling topic** (§5.1 all 3 criteria met): `YYYY-MM-DD-<sibling-slug>/ideas.md` — a NEW top-level folder at the parent's depth.
- Frontmatter `phase: idea_brainstorm`, `status: ideation-active` (bump to `ideation-complete` on user approval)

**Doc shape (lighter than architecture doc):**
- Context & Scope
- (Optional) Frame constraints from Step 2
- Per-cluster sections, each containing:
  - 5–10 honed survivors (broader than chat — sample from the full hidden pool)
  - **Considered & Cut** subsection with 3–5 cuts + reasons
  - Hidden-pool-size note
  - **Architectural Findings (priors)** — declarative commitments inherited from the user, typically `[user-canon-aligned]`. Hard Gate applies.
  - **Open Questions** — interrogative deferrals; each seeds a Step 2 Socratic question downstream.
  - **Routing** — exactly one action + optional timing modifiers (see Per-Cluster Routing below)
- Cross-pollination section (Step 4 output)
- (Optional) **Cross-Cluster Open Questions** — questions spanning multiple clusters; omit if empty
- (Optional) **Cross-Cluster Workshop Topics** — `→ workshop`-routed items needing USER decision; each subsection carries its own Routing line

**No Parts-table embedded in this doc** — Parts are roadmap-shape (per `common.md §6`), authored by `/architecture_brainstorm` against committed designs. The idea bank is a candidate POOL; architecture chooses from it and authors Parts. The closest equivalent in this doc is per-cluster Routing, which says WHAT phase comes next per cluster (and translates via Step 6 into `arch-pending` / `idea-rework` / `workshop-pending` Parts on `roadmap.md`, not directly into `plan-pending`).

**Roadmap.md is NOT saved here — load-bearing guardrail.** Step 5 saves the IDEA-BANK DOC only (`ideas.md`). The topic-folder `roadmap.md` is owned by `/update_roadmap` in Step 6 — DO NOT create roadmap.md via `write_doc` / `write_code` / direct `Write` here. Bypassing the executor silently drops Trigger validators ([`common.md §6.10`](../_brainstorm_shared/common.md)), derived-view recomputation ([§6.5](../_brainstorm_shared/common.md)), Mermaid deterministic regen ([§6.4](../_brainstorm_shared/common.md)), and the revision-log discipline ([§6.7](../_brainstorm_shared/common.md)). Single-executor pattern: every roadmap.md routes through `/update_roadmap`. The mistake-mode this guardrail prevents: a worker-prose `write_doc` call producing the topic-folder roadmap.md inline during this Step instead of waiting for Step 6's `/update_roadmap` invocation (which proposes creating it if absent — per `/update_roadmap` Step 1 *Not found* path).

#### Per-cluster handoff-readiness gate

Before assigning a Routing action, evaluate each cluster against three load-bearing criteria. The result determines whether `→ /architecture_brainstorm` or `→ /idea_brainstorm rerun` is the correct action.

1. **Approach-diversity** — at least 2 architecturally-distinct survivors. Cosmetic variations of one mechanic don't count; survivors must represent genuinely different ways of solving the cluster's problem. (Failure mode: arch brainstorm Step 4 cannot generate a 2–3 approach comparison — its core deliverable collapses to a single "obvious" answer.)
2. **Concreteness floor** — each honed survivor is one specific sentence grounded in named systems, specific numbers, or lore framing. Stubs like *"+1 slot"* don't pass; *"+1 craft slot — new wedge in radial menu's outer ring; cost varies per archetype"* does. (Failure mode: arch brainstorm is forced to do honing work that belongs in this skill's Step 3 Hone phase.)
3. **Cluster-boundary firmness** — the cluster's scope is stateable in one sentence that EXCLUDES neighboring clusters' content. New candidates stopped leaking in from adjacent design space during honing. (Failure mode: arch brainstorm scope balloons, fan-out triggers prematurely, and Step 1 cluster-scoped consumption can't anchor to a clear boundary.)

| Pass status | Routing action | Rationale |
|---|---|---|
| **All 3 pass** | `→ /architecture_brainstorm` | Cluster pool is mature; arch can run productively with real material. |
| **Any of 3 fails** | `→ /idea_brainstorm rerun` | Target the failing criterion in the rerun framing (e.g., *"rerun for approach-diversity — current survivors are 4 cosmetic variations of mechanic X"*). |

**Open Questions are NOT a gate criterion.** Arch brainstorm Step 2 exists to ask Socratic questions; unanswered Open Questions are valid handoff material — they become Step 2's multi-choice seeds. Making "all questions answered" a gate would push this skill to do work that belongs in arch brainstorm.

**`→ workshop` overrides the gate.** When a cluster surfaces a user-decision-required tension (competing design pillars, leading-hypothesis arbitration), `→ workshop` is the correct action regardless of the 3-criteria status — the gate only differentiates arch-vs-rerun.

**Stop-condition for the rerun chain.** Reruns sharpen the failing criterion in-place. If a second rerun on the same cluster still fails the same criterion, the cluster's underlying problem is likely scope/boundary (criterion 3), not a populate-more-ideas issue — split the cluster, merge it into an adjacent one, or escalate to `→ workshop`.

#### Per-Cluster Routing — action × timing

Each cluster's `Routing` subsection: exactly ONE action + zero-or-more timing modifiers. Cross-Cluster Workshop Topics carry their own per-topic Routing line.

**Actions (mandatory, exactly one):**

| Action | Meaning | Terminates chain? |
|---|---|---|
| `→ /architecture_brainstorm` | Cluster ready to architect. Arch session scopes to THIS cluster (`architecture_brainstorm/SKILL.md` Step 1). | No |
| `→ /idea_brainstorm rerun` | Pool too thin (< 5 survivors), new sub-themes, or leading-hypothesis needs honing. | No |
| `→ workshop` | USER decision needed before any further agent phase. User re-routes after deciding. | **Yes** |

**Timing modifiers (optional, stackable — controlled vocabulary):**

| Modifier | Meaning |
|---|---|
| `(now)` | Fire immediately |
| `(after Cluster X lands)` | Sequential dependency |
| `(parallel-safe with Cluster X)` | Concurrent-safe |
| `(future scope — when Y triggers)` | Deferred; name ripeness trigger |
| `(blocked on Z)` | Blocked on external decision/asset |

**Worked examples:**

```
- Cluster 1 (Currency): → /architecture_brainstorm  (now — dependency root)
- Cluster 2 (Per-Run Events): → /architecture_brainstorm  (after Cluster 1 lands — needs currency primitives)
- Cluster 3 (Almanack): → /architecture_brainstorm  (now — parallel-safe with Cluster 1)
- Cluster 4 (Hub World): → /idea_brainstorm rerun  (only 4 survivors after filter)
- Cluster 5 (Cosmetic unlocks): → /architecture_brainstorm  (future scope — when MVP economy data exists)
- Workshop: End-Game Framing: → workshop  (B+C leading; user arbitrates)
```

**Workshop terminates the chain.** No worklog/User-Tasks cross-write; the brainstorm doc is sole carrier.

**Roadmap state mapping.** Cluster Routing action → Part State translation is the executor's contract: see [`/update_roadmap`](../../commands/update_roadmap.md) Step 2 (idea-brainstorm input mapping). This skill's job is to supply the per-cluster Routing line; the command authors the Parts.

Timing modifiers translate to Part fields: `(now)` → propose Pos=1 for the recommended starting cluster; `(after Cluster X lands)` → Deps=`<Cluster X Part name>`; `(parallel-safe with Cluster X)` → same Pos as Cluster X's Part; `(future scope — when Y triggers)` → un-sequenced Pos=`—` with Trigger=`Y`; `(blocked on Z)` → Trigger=`blocked on Z` until external resolution.

**Recommended starting cluster (required when ≥2 actionable clusters):** name the cluster that fires first + one-line rationale (dependency root / unblocks downstream / earliest arch decision needed). This becomes the Part with Pos=1 in the roadmap.

**[Shared] Rationale spot-check** — see [`_brainstorm_shared/common.md` §2](../_brainstorm_shared/common.md).

**[Shared] User review gate** — see [`_brainstorm_shared/common.md` §4](../_brainstorm_shared/common.md).

### Step 6: Invoke `/update_roadmap`

After the user approves the idea-bank doc:

- Bump `status` to `ideation-complete` + mirror in Revision History footer (same edit; `_brainstorm_shared/common.md §5`).
- Invoke `/update_roadmap` with:
  - The saved `ideas.md` path (so the command identifies the topic folder)
  - The per-cluster Routing actions (from each cluster's Routing line in Step 5) — `/update_roadmap` Step 2 translates these to Part States
  - The recommended starting cluster (becomes Part with Pos=1)

`/update_roadmap` runs in batch-propose mode (single approval applies all edits to `roadmap.md` — Parts table, Mermaid block, derived views, revision log entry). If no roadmap exists in this topic folder yet, the command proposes creating one from `common.md §6` schema — confirm and proceed.

**Do NOT hand-edit OR `write_doc` `roadmap.md` from this skill.** The command owns that surface end-to-end (Mermaid regen, validator checks, derived-view recomputation, revision-log append). The prohibition covers BOTH direct `Edit`/`Write` calls AND worker-delegated `write_doc` calls — both bypass the executor's validation gates equally. Reinforces the Step 5 *Roadmap.md is NOT saved here* guardrail above.

### Handoff

After `/update_roadmap` applies:
- Surface per-cluster routing summary: count by action (M arch / K idea-rerun / L workshop), name the recommended starting cluster + one-line rationale.
- The roadmap.md `Currently ready to execute` derived view shows which arch sessions can fire first; cross-cluster hybrids each get a separate arch session per `common.md §5.1` spawn-placement (typically same-folder via criterion 3 failure).

---

## 5. MCP-Offline Policy

**[Shared]** Obsidian MCP offline → non-event (native vault `Read`/`Write`/`Edit`). **ai-worker MCP offline → fallback to a Haiku subagent for any `read_files` / `read_web` call** (Step 1 existing-doc digest, Step 2 multi-section spot-check, final `write_doc` synthesis). Do NOT degrade to chained native reads. See [`_brainstorm_shared/common.md` §3](../_brainstorm_shared/common.md) for the substitution shape.

---

## 6. Anti-Patterns

| Rationalization | Reality |
|---|---|
| "More ideas = better brainstorm" | Volume without filter is *dumping*, not brainstorming. The user becomes the curator; the agent fails its primary value-add. Surface the converged output (3–5 honed per cluster), not the raw pool. |
| "Generate all clusters first, then the user filters" | A single dump across N clusters multiplies the curate-load by N. Per-cluster pacing — diverge → filter → hone → present → user-react — is mandatory. |
| "Filter step risks killing creative ideas" | Filter is explicit and auditable (tone / pillar / scope / mechanic / redundancy / novelty checklist). Uncertain culls flag as `[mechanic-uncertain]` for user confirmation. Killing for clear tone or pillar mismatch is correct, not over-cautious. |
| "User said *'give me everything you thought of'* — I'll surface the raw pool" | Even on explicit request, route via the converged output. The full hidden pool lives in the doc's idea-pool section, not in chat. Same litmus as the *enumeration-bypass* trigger above. |
| "Mechanic conflicts will be caught at user-review" | User-review is for *design judgment*, not mechanic-correctness fact-checking. The Step 3 filter is where mechanic-fit lives. Surfacing ideas that contradict established game systems (e.g., proposing potions cost mana to *craft* when the model is mana-cost-at-*cast*) is a Step 3 filter failure. |
| "Let me also commit to an architecture during ideation" | That's `/architecture_brainstorm`'s job. Premature architectural commitment narrows the idea pool before it's populated. The skills are separate precisely so this can't happen by accident. |
| "Idea-bank output should include implementation Parts directly" | No. Parts are roadmap-shape (per `common.md §6`), authored by `/architecture_brainstorm` from a design committed at arch-brainstorm time. Idea bank's job is candidate enumeration. Per-Cluster Routing produces `arch-pending` / `idea-rework` / `workshop-pending` Parts via `/update_roadmap` (Step 6) — never `plan-pending` (that requires arch-brainstorm's 5-criterion readiness gate). Skipping the arch-brainstorm phase is the category leakage. |
| "Let me skip the existing-doc check, this is just ideation" | Same prior-art reason applies: don't duplicate prior idea-bank work. §1 is shared with `/architecture_brainstorm` precisely so both skills respect prior artifacts. |
| "Just one question to scope-frame — let me ask three" | Step 2 is *"often empty"* — multi-question scope-framing is usually architecture-narrowing in disguise. Ask one, populate ideas, see if more framing is even needed. |
| "The prior doc says X, so the idea bank should respect X (deference filter)" | Adjacent prior docs were authored before this brainstorm and may not survive contact with the systems being newly designed. If your filter rationale uses *only words from the prior doc* rather than words about the idea's own merit, you've deferred, not filtered. Surface competing ideas; let `/architecture_brainstorm` arbitrate. See Step 1 *adjacent-design-space* case. |
| "Add `workshop needed first` as a timing modifier on `→ /architecture_brainstorm`" | Workshop is an ACTION (terminates the chain), not a timing modifier. By arch time ideation must be settled. Use `→ workshop`. |
| "Per-cluster Findings let me record new arch commitments here" | Findings are PRIORS, not new commitments. Hard Gate litmus applies. Agent-invented → `/architecture_brainstorm` Step 4. |
| "Bundle all per-cluster open questions into one global Open Questions section" | Same monolithic-handoff failure Per-Cluster Routing fixes. Per-cluster questions stay in-cluster; only cross-cluster ones go in the optional global section. |

---

## 7. Cross-references

**Shared procedures:**
- [`_brainstorm_shared/common.md`](../_brainstorm_shared/common.md) — §1 existing-doc check, §1.1 resume table, §1.2 stale-roadmap remediation, §2 rationale spot-check, §3 MCP-offline, §4 user-review gate, §5 doc path/frontmatter conventions, §5.1 spawn-placement, §6 roadmap.md schema (including §6.10 Trigger semantics), §7 workflow phase cardinality, §8 scratch-ledger checkpoint discipline

**Adjacent skills:**
- [`architecture_brainstorm`](../architecture_brainstorm/SKILL.md) — runs AFTER this skill; consumes the idea-bank doc; produces a design doc + Parts authored on `roadmap.md`
- [`debugging`](../debugging/SKILL.md) — alternative path for bug fixes with known root cause; not idea generation

**Commands:**
- [`/update_roadmap`](../../commands/update_roadmap.md) — Step 6 invocation; applies Per-Cluster Routing → Part State mapping to `roadmap.md`

**File-based memory:**
- `feedback_no_unilateral_condensation.md` — Step 3 Diverge verbatim-port discipline
- `feedback_no_performative_agreement.md` — Step 2 Socratic-question opener discipline
- `feedback_session_start_hook_does_not_override_skill_procedure.md` — Step 3 / Step 2 procedural-gate discipline (session-start hooks don't excuse skipping load-bearing gates)

**CLAUDE.md sections:**
- §9 Tool Routing — Pre-Call Litmus — relevant for §1 read_files routing (synthesis bundling)
