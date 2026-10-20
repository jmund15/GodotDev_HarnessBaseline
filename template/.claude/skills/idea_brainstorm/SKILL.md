---
name: Idea_Brainstorm
description: >-
  Use before architecture choices when a greenfield, creative or spatial topic needs candidate ideas, or an idea pool needs expansion. SKIP a populated space, mature domain or convergent tactical/mechanical topic; use architecture_brainstorm.
---

# Idea Brainstorm

Answers *"what could exist?"*, producing the candidate POOL that [`architecture_brainstorm`](../architecture_brainstorm/SKILL.md) (*"how should we build it?"*) narrows from. Socratic narrowing, trade-off comparison and Parts-authoring live there, not here. Shared surface: [`_brainstorm_shared/common.md`](../_brainstorm_shared/common.md).

## The Hard Gate

```
DO NOT INVOKE ARCHITECTURE BRAINSTORMING, IMPLEMENTATION SKILLS, OR
WRITE ANY CODE FROM WITHIN IDEA BRAINSTORM.
```

Produce the idea-bank doc and STOP.

**User-stated priors are NOT gate violations.** Record them as `[user-canon-aligned]` Findings; the gate forbids the AGENT inventing commitments (file paths, class names, lifecycle, BB keys). Litmus: *"Did the user say this, or am I deciding it?"* Said → per-cluster Findings. Agent-invented → defer to `/architecture_brainstorm`'s Socratic-and-approaches Step 4 procedure.

## 1. When to Use

**Trigger phrases:** "brainstorm X ideas" · "what fun / creative / interesting X are there" · "help me think of more X options" · "drip-feed ideas for X" · "more ideas for X" / "expand the X pool".

**Enumeration-pressure phrasing is the STRONGEST trigger, not a bypass license.** On cues like *"every idea you can think of"*, *"don't filter"*, *"unfiltered"*, *"just give me ideas"*, *"throw everything at the wall"* — fire this skill, populate the bank via the divergence pipeline, save the full pool in the doc's idea-pool section, because working context does not survive compaction. Chat shows only what Step 3 phase 5's presentation litmus admits; the clusters carry a sample broad enough to test the pool's breadth, with a pointer to the idea-pool section ([`doc_production.md`](../_brainstorm_shared/doc_production.md) §2). Inline chat enumeration of the raw pool is the Skill-bypass failure this prevents.

## 2. When Ideation Runs

One litmus decides: *is the design space unpopulated?* Run this skill when candidate ideas would have to be invented for a creative or spatial topic that no idea-bank or design doc covers (Step 1's check). Route to `/architecture_brainstorm` when canon or the user already populates the space: a mature domain whose named approaches you can list without searching, a convergent tactical or mechanical topic, a user who says to skip to design, or an `ideation-complete` doc. Whether the triage admits a brainstorm skill at all follows [`execution_depth.md`](../_brainstorm_shared/execution_depth.md) §Admission table.

## 3. Workflow Position

This skill runs before `/architecture_brainstorm`. Phase cardinality and flow topology (forward fan-out, cluster merge, ideation skip, reverse signal, zero-impl outcome, workshop terminate): [`flow_and_frontier.md §7`](../_brainstorm_shared/flow_and_frontier.md).

## 4. Procedure (6 Steps)

### Step 1: Existing-doc check

**[Shared]** [`existing_doc_check.md §1`](../_brainstorm_shared/existing_doc_check.md) — required asks, dispatched as [`/explore`](../../commands/explore.md) lenses selected under [`explore_agents.md`](../../commands/agents/explore_agents.md) §Lens Roster & Trigger Rules. Prior-art records existing ownership without constraining idea generation.

**Two doc-hit cases:**
- **Same topic** (digest doc IS this topic, status-tagged): resume per common §1.1. `ideation-active` → resume mid-procedure; `ideation-complete` → hand off to `/architecture_brainstorm`.
- **Adjacent design space** (doc covers a neighboring system whose commitments touch this topic but were not authored against it — a meta-economy brainstorm hitting a pre-existing event-system doc): proceed. Its claims are **priors to validate**, not canon. Surface competing ideas on merit; flag conflicts for `/architecture_brainstorm` (§6 *deference-filter*).

### Step 2: Scope-framing Socratic (often optional)

Optional, and ONLY for **scope-framing** — constraints the pool should respect (PvE vs multi, design pillar, target audience, tone, target system). Never for **architecture-narrowing** (*"single currency or dual?"*) — that is `/architecture_brainstorm`'s Socratic-and-approaches Step 2 procedure, AFTER the pool exists.

- Multiple-choice over open-ended. Interview mechanic — **[Shared]** [`flow_and_frontier.md §9`](../_brainstorm_shared/flow_and_frontier.md): frontier rounds for a batch of framing forks, one question per turn for a single deep one.
- Stop once the FRAME is clear enough to populate ideas in-bounds; skip the step entirely if the user's prompt already establishes it.

**Litmus:** *"Does answering this rule out specific candidate IDEAS, or just architectural COMBINATIONS of them?"* Ideas → scope-framing, ask now. Combinations → architecture-narrowing, defer.

**Multi-topic scope.** A topic splitting into independent subsystems defaults to **one brainstorm with multiple top-level clusters**; separate invocations duplicate the Step 1 check, re-load context, and lose cross-pollination (Step 4 hybrids surface only when clusters are visible to one pass). Split ONLY when (a) the user asks, or (b) cross-pollination would mislead.

### Step 3: Generation & Curation (per cluster)

Run the pipeline once per natural cluster. Clusters map to user-surfaced themes; a new one requires explicit *"I'm proposing a category you didn't mention"* framing.

**Optional `--fan_out`.** On `/idea_brainstorm <topic> --fan_out` (or when `execution_depth.md` §Admission table admits fan-out for the cluster), **Diverge** dispatches to [`/idea_brainstorm_fanout`](../../commands/idea_brainstorm_fanout.md): N lens-diverse generators, an independent critique pass (dedup / fit / coverage-gap), then the raw pool + annotations return here. Opt-in; no flag means single-agent Diverge. It replaces **only** Diverge — Filter → Hone → Cluster & rank → present, the per-cluster user-react loop and the Hard Gate run unchanged. **Curation never leaves Claude:** the fan-out generates and critiques, never decides what survives.

**Hidden (agent context only):**

1. **Diverge** — generate raw candidates per cluster until new ones repeat existing ones in substance; the pool must well exceed the survivors presented. Sources:
   - User-surfaced seeds (verbatim, expanded — never silently digested or condensed; `feedback_no_unilateral_condensation.md`)
   - Domain-canon precedents (game design: Hades, Slay the Spire, Dead Cells, Hollow Knight, Inscryption) — cite by name when used
   - Lateral candidates from adjacent genres or domains
   - Cross-pollination from clusters already processed

   **Fan-out hook (`--fan_out` only).** [`/idea_brainstorm_fanout`](../../commands/idea_brainstorm_fanout.md) owns the context push, lens choice, liveness check and handback for THIS cluster; Filter takes the returned pool with its `fit`/`dedup` critic notes as input, not gospel.

2. **Filter** — apply the checklist:
   - **Game-mechanics fit** — contradicts an established system? If unsure, flag `[mechanic-uncertain]` for user confirmation rather than silently culling.
   - **Tone fit** — matches the game's voice / lore?
   - **Scope fit** — can a v1 reach it?
   - **Pillar fit** — supports or dilutes the design pillar?
   - **Redundancy** — a reskin of another candidate?
   - **Novelty** — adds something the user hasn't surfaced?

   **Not a filter criterion:** "Conflicts with an adjacent prior doc." Those are priors to validate — the `architecture_brainstorm` skill's Socratic-and-approaches Step 1 procedure *adjacent-design-space* + §6 *deference-filter*. Litmus: a cut rationale using *only words from the prior doc* rather than the idea's own merit (tone / pillar / mechanic conflict) deferred, it did not filter.

3. **Hone** — refine each survivor into one specific sentence grounded in named game systems, numbers, or lore framing. *"+1 slot"* is divergence output; *"+1 slot — a new wedge in the radial-craft menu's outer ring; cost varies per archetype"* is presentation-ready.

4. **Cluster & rank** — group survivors by sub-theme and rank them for presentation.

**Presented (chat output):**

5. **Present per cluster:**
   - Only the survivors the owner can compare in one read, each beating the cuts on a distinct merit, with a one-line rationale (*"why this beat the cuts"*)
   - The cuts whose rejection reason teaches the owner something about the filter, each with a one-line reason; omit cuts with generic reasons (filter-pass audit trail)
   - Hidden-pool size note (*"started from ~N raw, kept ~M"*)
   - Bracketed marker tags per entry — canonical vocabulary; propose a SKILL edit to add a form:
     - **Provenance**: `[user-verbatim]`, `[user-canon-aligned]`, `[user-canon-honed]`, `[user-surfaced]`, `[canon-import: <source>]`, `[original]`, `[cross-cluster: N+M]` (Step 4 hybrids — names the spanning clusters)
     - **Scope**: `[scope: v1|medium|post-MVP|late-game/DLC]`
     - **Dependency**: `[depends-on: <subsystem>]`
     - **Tension**: `[pillar-tension flag]`, `[mechanic-uncertain]`
     - **Cross-ref**: `[architecture: Finding N-X]`, `[see-cluster: M]`

   While the owner is present, move on only after the current cluster is acknowledged: one cluster's reaction reshapes the next, so never dump every cluster and then collect reactions. When the owner is away (an unattended arming, `drive_spine.md` §Halts), save each cluster to the doc and batch the acknowledgments into one return packet (`CLAUDE.core.md` §Preferences).

6. **Checkpoint to `decisions.md`.** **[Shared]** [`flow_and_frontier.md §8`](../_brainstorm_shared/flow_and_frontier.md) for path, schema, append-on-classify, consumption. Per-cluster block appended under `## Decided`:

   ```markdown
   ## Cluster <N> — <Cluster Name>
   - Honed survivors: <comma-separated names>
   - Cuts: <comma-separated names>
   - Findings: <comma-separated finding IDs>
   - Key commitments: <2–4 bullets of architectural commitments made this cluster>
   - Cross-cluster anchors: <list>
   ```

   Append once the user acknowledges the cluster (accepted survivors, swapped cuts back in, raised categories, or said "proceed"). Step 5 carries the acknowledged entries as doc content; `reference_files` passes structure and voice only (`flow_and_frontier.md` §8 Consumption).

### Step 4: Cross-pollination

After all clusters are processed, find cross-cluster hybrids — ideas whose power comes from spanning two or more clusters. Present every hybrid whose value depends on spanning clusters, omitting two ideas merely placed side by side; give each a rationale and tag it `[cross-cluster: N+M]` (Step 3 phase 5 vocabulary; the numbers name the spanning clusters). Hybrids often surface architectural primitives spanning subsystems — the arch skill's richest material.

**Hybrid → arch handoff.** Each accepted hybrid becomes its OWN `/architecture_brainstorm` invocation per `common.md §5.1` spawn-placement (typically same-folder via criterion-3 failure — its design is consumed by this topic's parent, not by more than one unrelated parent), taking the hybrid's name as cluster slug. Track via the saved doc's Cross-Pollination section.

### Step 5: Save the idea-bank doc

**[Shared]** Path tiebreaker, folder-per-topic, frontmatter — [`common.md §5`](../_brainstorm_shared/common.md); spawn-placement for arch sessions — §5.1.

**Map the placement to a path; never default to the parent folder for a sub-topic:**
- **Fresh topic** (default): `YYYY-MM-DD-<kebab-case-topic>/ideas.md` (folder per topic, flat filename inside)
- **Deeper-scope sub-topic** (§5.1 *child subfolder* — own ideation, parent-confined audience): `<parent-topic-folder>/<sub-slug>/ideas.md`, in a NEW child subfolder, NOT the parent folder. Recurring mis-save target; create it if absent.
- **Cross-cutting sibling topic** (§5.1 all 3 criteria met): `YYYY-MM-DD-<sibling-slug>/ideas.md`, a NEW top-level folder at the parent's depth.
- Frontmatter `phase: idea_brainstorm`, `status: ideation-active` (bump to `ideation-complete` on user approval).

**Doc shape (lighter than an arch doc):**
- Context & Scope
- (Optional) Frame constraints from Step 2
- Per-cluster sections: honed survivors (broader than chat — a sample of the hidden pool broad enough to test its breadth, the full pool living in the idea-pool section) · **Considered & Cut** (the cuts whose reasons teach the owner about the filter) · hidden-pool-size note · **Architectural Findings (priors)** — user-inherited commitments, typically `[user-canon-aligned]`, Hard Gate applies · **Open Questions** — interrogative deferrals, each seeding a downstream Step 2 Socratic question · **Routing** — one action + optional timing modifiers (below)
- Cross-pollination section (Step 4 output)
- (Optional) **Cross-Cluster Open Questions** — omit if empty
- (Optional) **Cross-Cluster Workshop Topics** — `→ workshop` items needing USER decision; each subsection carries its own Routing line

**No Parts-table in this doc.** Its nearest equivalent is per-cluster Routing, naming the next phase and translating via Step 6 into `arch-pending` / `idea-rework` / `workshop-pending` Parts, never `plan-pending` (§6 *implementation Parts directly*). This idea route does not apply the architecture brainstorm's Part-readiness gate.

**Roadmap.md is NOT saved here.** This Step saves `ideas.md` only; `/update_roadmap` authors the roadmap in Step 6 under its single-executor rule ([`update_roadmap.md`](../../commands/update_roadmap.md)).

#### Per-cluster handoff-readiness gate

Evaluate each cluster against three criteria before assigning a Routing action; the result decides arch vs rerun.

Read [`reference/handoff_and_routing.md`](reference/handoff_and_routing.md) §Per-cluster handoff-readiness gate for the criteria, the pass-status table, the workshop override and the rerun stop-condition.

#### Per-Cluster Routing — action × timing

Each cluster's `Routing` subsection: exactly ONE action + zero-or-more timing modifiers. Cross-Cluster Workshop Topics carry their own per-topic Routing line.

Read [`reference/handoff_and_routing.md`](reference/handoff_and_routing.md) §Per-Cluster Routing for the action and timing-modifier tables, worked examples, the roadmap state mapping and the recommended-starting-cluster rule.

**[Shared] Rationale spot-check** — `doc_production.md §2`. **[Shared] User review gate** — `doc_production.md §4`.

### Step 6: Invoke `/update_roadmap`

After the user approves the idea-bank doc:

- Bump `status` to `ideation-complete` + mirror in the Revision History footer (same edit; `common.md §5`).
- Invoke `/update_roadmap` with: the saved `ideas.md` path (identifies the topic folder); the Step 5 per-cluster Routing actions (its Step 2 translates them to Part States); the recommended starting cluster (becomes Pos=1).

It runs batch-propose — one approval applies all `roadmap.md` edits (Parts table, Mermaid block, derived views, revision log). With no roadmap in the folder, it creates one from the `common.md §6` schema.

### Handoff

- Surface the routing summary: count by action (M arch / K idea-rerun / L workshop), name the recommended starting cluster + one-line rationale.

## 6. Anti-Patterns

| Rationalization | Reality |
|---|---|
| "More ideas = better brainstorm" | Volume without filter is *dumping*; the user becomes curator. Surface the converged, honed set per cluster. |
| "Filter risks killing creative ideas" | Filter is explicit and auditable (tone / pillar / scope / mechanic / redundancy / novelty); uncertain culls flag `[mechanic-uncertain]`. Killing for clear tone or pillar mismatch is correct. |
| "Mechanic conflicts will be caught at user-review" | User-review is design judgment, not fact-checking. Ideas contradicting established systems (potions costing mana to *craft* when the model is mana-at-*cast*) are a Step 3 filter failure. |
| "Idea-bank output should include implementation Parts directly" | Parts are roadmap-shape (`common.md §6`), authored by `/architecture_brainstorm` from a committed design. Routing yields `arch-pending` / `idea-rework` / `workshop-pending` via `/update_roadmap`, never `plan-pending` (that needs arch's readiness gate). |
| "Just one question to scope-frame — let me ask three" | Ask a framing question only if its answer rules out candidate ideas, and batch those per [`flow_and_frontier.md §9`](../_brainstorm_shared/flow_and_frontier.md) (Step 2); the rest is architecture-narrowing in disguise. |
| "The prior doc says X, so the idea bank should respect X (deference-filter)" | Adjacent prior docs predate this brainstorm and may not survive contact with the systems being newly designed. Rationale using *only words from the prior doc* is deference, not filtering. Surface competing ideas; `/architecture_brainstorm` arbitrates in its Socratic-and-approaches Step 1 procedure *adjacent-design-space*. |
| "Add `workshop needed first` as a timing modifier on `→ /architecture_brainstorm`" | Workshop is an ACTION and terminates the chain; by arch time ideation must be settled. Use `→ workshop`. |
| "Bundle all per-cluster open questions into one global section" | The monolithic-handoff failure Per-Cluster Routing fixes. Per-cluster questions stay in-cluster; only cross-cluster ones go global. |
| "Let me also commit to an architecture during ideation" | That is `/architecture_brainstorm`. Premature commitment narrows the pool before it is populated. |
| "Skip the existing-doc check, this is just ideation" | `existing_doc_check.md` §1 is shared with `/architecture_brainstorm` so both respect prior artifacts; do not redo prior idea-bank work. |
| "User said *'give me everything you thought of'* — I'll surface the raw pool" | Route via converged output even then; the hidden pool lives in the doc's idea-pool section, not chat. Same litmus as the enumeration-pressure trigger in §1. |
| "Per-cluster Findings let me record new arch commitments here" | Findings are PRIORS. The Hard Gate litmus applies; an agent-invented commitment goes to `/architecture_brainstorm`'s Socratic-and-approaches Step 4 procedure. |

## 7. Cross-references

- [`existing_doc_check.md`](../_brainstorm_shared/existing_doc_check.md) §1 existing-doc check, §1.1 resume table, §1.2 stale-roadmap remediation; [`doc_production.md`](../_brainstorm_shared/doc_production.md) §2 rationale spot-check, §3 MCP-offline, §4 user-review gate; [`common.md`](../_brainstorm_shared/common.md) §5 doc path/frontmatter, §5.1 spawn-placement; [`common.md`](../_brainstorm_shared/common.md) §6 roadmap.md schema; [`roadmap_triggers_mvp.md`](../_brainstorm_shared/roadmap_triggers_mvp.md) §6.10 Triggers; [`flow_and_frontier.md`](../_brainstorm_shared/flow_and_frontier.md) §7 phase cardinality, §8 `decisions.md`, §9 interview mechanic
- [`architecture_brainstorm`](../architecture_brainstorm/SKILL.md) runs AFTER this skill; [`debugging`](../debugging/SKILL.md) covers known-root-cause fixes, not idea generation
- [`/update_roadmap`](../../commands/update_roadmap.md) — Step 6; [`/idea_brainstorm_fanout`](../../commands/idea_brainstorm_fanout.md) — Step 3 `--fan_out`
- `feedback_no_unilateral_condensation.md` — Step 3 Diverge verbatim-port discipline
- `feedback_no_performative_agreement.md` — Step 2 Socratic-question opener discipline
- `feedback_session_start_hook_does_not_override_skill_procedure.md` — session-start hooks don't excuse skipping Step 2 / Step 3 gates
- CLAUDE.core.md §9 — read_files routing (synthesis bundling)
