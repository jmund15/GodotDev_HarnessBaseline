---
name: Idea_Brainstorm
description: >-
  Use BEFORE architecture decisions, when the design space itself needs populating —
  greenfield topics, open-ended what-could-exist-here questions, or enumeration
  pressure. Produces a curated idea-bank doc with per-cluster honed survivors; hand
  off to /architecture_brainstorm once the pool stabilizes. SKIP for mature domains
  where canonical patterns already populate the space, tactical/mechanical topics, or
  when the user arrives with a populated idea space.
---

# Idea Brainstorm

Answers *"what could exist?"*, producing the candidate POOL that [`architecture_brainstorm`](../architecture_brainstorm/SKILL.md) (*"how should we build it?"*) narrows from. Socratic narrowing, trade-off comparison and Parts-authoring live there, not here. Shared surface: [`_brainstorm_shared/common.md`](../_brainstorm_shared/common.md).

## The Hard Gate

```
DO NOT INVOKE ARCHITECTURE BRAINSTORMING, IMPLEMENTATION SKILLS, OR
WRITE ANY CODE FROM WITHIN IDEA BRAINSTORM.
```

Produce the idea-bank doc and STOP.

**User-stated priors are NOT gate violations.** Record them as `[user-canon-aligned]` Findings; the gate forbids the AGENT inventing commitments (file paths, class names, lifecycle, BB keys). Litmus: *"Did the user say this, or am I deciding it?"* Said → per-cluster Findings. Agent-invented → defer to `/architecture_brainstorm` Step 4.

## 1. When to Use

**Trigger phrases:** "brainstorm X ideas" · "what fun / creative / interesting X are there" · "help me think of more X options" · "drip-feed ideas for X" · "more ideas for X" / "expand the X pool".

**Enumeration-pressure phrasing is the STRONGEST trigger, not a bypass license.** On cues like *"every idea you can think of"*, *"don't filter"*, *"unfiltered"*, *"just give me ideas"*, *"throw everything at the wall"* — fire this skill, populate the bank via the divergence pipeline, save with a hidden-pool reference. Chat stays at 3–5 honed per cluster; the doc-saved pool is broader (10–15); the full hidden pool stays in working context. Inline chat-enumeration of 50+ items is the Skill-bypass failure this prevents.

**Fire when ALL hold:**

- [ ] Topic is creative / spatial (open-ended), not mechanical / tactical (pricing, naming, sizing an already-decided thing).
- [ ] No existing idea-bank or design doc covers it (common.md §1 check, run in Step 1).
- [ ] Not in §2 below.

## 2. When to Skip

| Skip case | Why |
|---|---|
| **Mature domain** — agent lists 5+ named design approaches from memory, unsearched | Canon already populates the space → `/architecture_brainstorm`. |
| **Tactical / mechanical topic** — *"what's the right pricing for X?"* | Divergence adds noise to convergent decisions → `/architecture_brainstorm`, or plan production if architecture is settled. |
| **User says** *"skip the brainstorm, go straight to design"* | They've populated the space themselves. |
| **Existing `ideation-complete` doc covers the topic** | §1 check surfaces it → `/architecture_brainstorm`. |
| **Worklog scope 1 or 2** | Brainstorming overhead exceeds benefit. |

## 3. Workflow Position

`/idea_brainstorm` (scope-framing Socratic; per-cluster diverge→filter→hone→cluster; `ideas.md` at `ideation-complete`) → `/architecture_brainstorm` (architecture-narrowing Socratic; 2–3 approaches; design doc at `brainstorming-complete`) → implementation. Both brainstorm phases close by invoking `/update_roadmap`. Full flow-topology vocabulary (forward fan-out, cluster merge, ideation skip, reverse signal, zero-impl outcome, workshop terminate): [`common.md §7`](../_brainstorm_shared/common.md).

## 4. Procedure (6 Steps)

### Step 1: Existing-doc check

**[Shared]** [`common.md §1`](../_brainstorm_shared/common.md) — required asks, dispatched as [`/explore`](../../commands/explore.md) lenses. Ideation may drop `exp-prior-art` (ideas precede architecture) but never `exp-memory`.

**Two doc-hit cases:**
- **Same topic** (digest doc IS this topic, status-tagged): resume per common §1.1. `ideation-active` → resume mid-procedure; `ideation-complete` → hand off to `/architecture_brainstorm`.
- **Adjacent design space** (doc covers a neighboring system whose commitments touch this topic but were not authored against it — a meta-economy brainstorm hitting a pre-existing event-system doc): proceed. Its claims are **priors to validate**, not canon. Surface competing ideas on merit; flag conflicts for `/architecture_brainstorm` (§6 *deference-filter*).

### Step 2: Scope-framing Socratic (often optional)

Optional, and ONLY for **scope-framing** — constraints the pool should respect (PvE vs multi, design pillar, target audience, tone, target system). Never for **architecture-narrowing** (*"single currency or dual?"*) — that is `/architecture_brainstorm` Step 2, AFTER the pool exists.

- Multiple-choice over open-ended. Interview mechanic — **[Shared]** [`common.md §9`](../_brainstorm_shared/common.md): frontier rounds for a batch of framing forks, one question per turn for a single deep one.
- Stop once the FRAME is clear enough to populate ideas in-bounds; skip the step entirely if the user's prompt already establishes it.

**Litmus:** *"Does answering this rule out specific candidate IDEAS, or just architectural COMBINATIONS of them?"* Ideas → scope-framing, ask now. Combinations → architecture-narrowing, defer.

**Multi-topic scope.** A topic splitting into independent subsystems (roguelike meta-game = currency + per-run events + almanack) defaults to **one brainstorm with multiple top-level clusters**; separate invocations duplicate the Step 1 check, re-load context, and lose cross-pollination (Step 4 hybrids surface only when clusters are visible to one pass). Split ONLY when (a) the user asks, or (b) cross-pollination would mislead.

### Step 3: Generation & Curation (per cluster)

Run the pipeline once per natural cluster. Clusters map to user-surfaced themes; a new one requires explicit *"I'm proposing a category you didn't mention"* framing.

**Optional `--fan_out`.** On `/idea_brainstorm <topic> --fan_out` (or a broad / enumeration-pressured cluster), **Diverge** dispatches to [`/idea_brainstorm_fanout`](../../commands/idea_brainstorm_fanout.md): N lens-diverse generators, an independent critique pass (dedup / fit / coverage-gap), then the raw pool + annotations return here. Opt-in; no flag means single-agent Diverge. It replaces **only** Diverge — Filter → Hone → Cluster & rank → present, the per-cluster user-react loop and the Hard Gate run unchanged. **Curation never leaves Claude:** the fan-out generates and critiques, never decides what survives.

**Hidden (agent context only):**

1. **Diverge** — generate 15–30 raw candidates per cluster from:
   - User-surfaced seeds (verbatim, expanded — never silently digested or condensed; `feedback_no_unilateral_condensation.md`)
   - Domain-canon precedents (game design: Hades, Slay the Spire, Dead Cells, Hollow Knight, Inscryption) — cite by name when used
   - Lateral candidates from adjacent genres or domains
   - Cross-pollination from clusters already processed

   **Fan-out hook (`--fan_out` only).** Dispatch [`/idea_brainstorm_fanout`](../../commands/idea_brainstorm_fanout.md) for THIS cluster: assemble the cluster CONTEXT (scope / boundary / raw seeds / kept survivors — **pushed**, not searched, per `gotcha_workflow_fanout_search_false_absence`), pick ~4 orthogonal lenses via the command's rubric, dispatch. Check liveness first — every generator returned `count > 0`; a silent-empty lens is not a clean lens (`arch_rule_autonomous_loop_positive_liveness`). Merge the raw pool + the coverage-gap critic's `proposedAdditions` + context-privileged candidates from the live conversation (user tone, unstated intent, chat-only seeds the generators never got — a top-up, NOT a re-diverge), then Filter using `fit`/`dedup` critic notes as input, not gospel.

2. **Filter** — apply the checklist:
   - **Game-mechanics fit** — contradicts an established system? If unsure, flag `[mechanic-uncertain]` for user confirmation rather than silently culling.
   - **Tone fit** — matches the game's voice / lore?
   - **Scope fit** — can a v1 reach it?
   - **Pillar fit** — supports or dilutes the design pillar?
   - **Redundancy** — a reskin of another candidate?
   - **Novelty** — adds something the user hasn't surfaced?

   **Not a filter criterion:** "Conflicts with an adjacent prior doc." Those are priors to validate — Step 1 *adjacent-design-space* + §6 *deference-filter*. Litmus: a cut rationale using *only words from the prior doc* rather than the idea's own merit (tone / pillar / mechanic conflict) deferred, it did not filter.

3. **Hone** — refine each survivor into one specific sentence grounded in named game systems, numbers, or lore framing. *"+1 slot"* is divergence output; *"+1 slot — a new wedge in the radial-craft menu's outer ring; cost varies per archetype"* is presentation-ready.

4. **Cluster & rank** — group survivors by sub-theme; pick the top 3–5 per cluster.

**Presented (chat output):**

5. **Present per cluster:**
   - 3–5 honed survivors, each with a one-line rationale (*"why this beat the cuts"*)
   - 2–3 named cuts, each with a one-line rejection reason (filter-pass audit trail)
   - Hidden-pool size note (*"started from ~N raw, kept ~M"*)
   - Bracketed marker tags per entry — canonical vocabulary; propose a SKILL edit to add a form:
     - **Provenance**: `[user-verbatim]`, `[user-canon-aligned]`, `[user-canon-honed]`, `[user-surfaced]`, `[canon-import: <source>]`, `[original]`, `[cross-cluster: N+M]` (Step 4 hybrids — names the spanning clusters)
     - **Scope**: `[scope: v1|medium|post-MVP|late-game/DLC]`
     - **Dependency**: `[depends-on: <subsystem>]`
     - **Tension**: `[pillar-tension flag]`, `[mechanic-uncertain]`
     - **Cross-ref**: `[architecture: Finding N-X]`, `[see-cluster: M]`

   Move on only after the current cluster is acknowledged — never dump every cluster, then collect reactions.

6. **Checkpoint to `decisions.md`.** **[Shared]** [`common.md §8`](../_brainstorm_shared/common.md) for path, schema, append-on-classify, consumption. Per-cluster block appended under `## Decided`:

   ```markdown
   ## Cluster <N> — <Cluster Name>
   - Honed survivors: <comma-separated names>
   - Cuts: <comma-separated names>
   - Findings: <comma-separated finding IDs>
   - Key commitments: <2–4 bullets of architectural commitments made this cluster>
   - Cross-cluster anchors: <list>
   ```

   Append once the user acknowledges the cluster (accepted survivors, swapped cuts back in, raised categories, or said "proceed"). The worker reads it as `reference_files` in the Step 5 `write_doc` call.

### Step 4: Cross-pollination

After all clusters are processed, find cross-cluster hybrids — ideas whose power comes from spanning two or more clusters. Present 3–5 with rationale, each tagged `[cross-cluster: N+M]` (Step 3 phase 5 vocabulary; the numbers name the spanning clusters). Hybrids often surface architectural primitives spanning subsystems — the arch skill's richest material.

**Hybrid → arch handoff.** Each accepted hybrid becomes its OWN `/architecture_brainstorm` invocation per `common.md §5.1` spawn-placement (typically same-folder via criterion-3 failure — its design is consumed by this topic's parent, not by 2+ unrelated parents), taking the hybrid's name as cluster slug. Track via the saved doc's Cross-Pollination section.

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
- Per-cluster sections: 5–10 honed survivors (broader than chat — sample the hidden pool) · **Considered & Cut** (3–5 cuts + reasons) · hidden-pool-size note · **Architectural Findings (priors)** — user-inherited commitments, typically `[user-canon-aligned]`, Hard Gate applies · **Open Questions** — interrogative deferrals, each seeding a downstream Step 2 Socratic question · **Routing** — one action + optional timing modifiers (below)
- Cross-pollination section (Step 4 output)
- (Optional) **Cross-Cluster Open Questions** — omit if empty
- (Optional) **Cross-Cluster Workshop Topics** — `→ workshop` items needing USER decision; each subsection carries its own Routing line

**No Parts-table in this doc.** Its nearest equivalent is per-cluster Routing, naming the next phase and translating via Step 6 into `arch-pending` / `idea-rework` / `workshop-pending` Parts, never `plan-pending` (§6 *implementation Parts directly*).

**Roadmap.md is NOT saved here.** This Step saves `ideas.md` only; `/update_roadmap` owns the topic-folder `roadmap.md` in Step 6 — never create it here via `write_doc` or direct `Write`. Bypassing the executor silently drops Trigger validators (`common.md §6.10`), derived-view recomputation (§6.5), Mermaid deterministic regen (§6.4), and revision-log discipline (§6.7). Single-executor pattern: every roadmap.md routes through `/update_roadmap`, which proposes creating it if absent (its Step 1 *Not found* path).

#### Per-cluster handoff-readiness gate

Evaluate each cluster against three criteria before assigning a Routing action; the result decides arch vs rerun.

1. **Approach-diversity** — at least 2 architecturally-distinct survivors; cosmetic variations of one mechanic don't count. Failure: arch Step 4's 2–3 approach comparison collapses to one "obvious" answer.
2. **Concreteness floor** — each survivor meets the Step 3 Hone bar: one specific sentence grounded in named systems, numbers, or lore. Failure: arch does Hone work belonging to Step 3.
3. **Cluster-boundary firmness** — scope is stateable in one sentence EXCLUDING neighboring clusters' content; candidates stopped leaking in from adjacent design space during honing. Failure: arch scope balloons, fan-out fires prematurely, cluster-scoped consumption can't anchor.

| Pass status | Routing action | Rationale |
|---|---|---|
| **All 3 pass** | `→ /architecture_brainstorm` | Pool is mature; arch runs on real material. |
| **Any of 3 fails** | `→ /idea_brainstorm rerun` | Name the failing criterion in the rerun framing (*"rerun for approach-diversity — current survivors are 4 cosmetic variations of mechanic X"*). |

**Open Questions are NOT a gate criterion.** Arch Step 2 exists to ask them; unanswered ones are valid handoff material and become its multi-choice seeds. Gating on "all questions answered" pushes arch work into this skill.

**`→ workshop` overrides the gate.** A cluster surfacing a user-decision-required tension (competing pillars, leading-hypothesis arbitration) routes to `→ workshop` regardless of the 3-criteria status — the gate only differentiates arch-vs-rerun.

**Rerun stop-condition.** Reruns sharpen the failing criterion in place. If a second rerun on the same cluster fails the same criterion, the problem is scope/boundary (criterion 3): split the cluster, merge it into a neighbor, or escalate to `→ workshop`.

#### Per-Cluster Routing — action × timing

Each cluster's `Routing` subsection: exactly ONE action + zero-or-more timing modifiers. Cross-Cluster Workshop Topics carry their own per-topic Routing line.

**Actions (mandatory, exactly one):**

| Action | Meaning | Terminates chain? |
|---|---|---|
| `→ /architecture_brainstorm` | Ready to architect; the arch session scopes to THIS cluster (`architecture_brainstorm/SKILL.md` Step 1). | No |
| `→ /idea_brainstorm rerun` | Pool too thin (< 5 survivors), new sub-themes, or leading-hypothesis needs honing. | No |
| `→ workshop` | USER decision needed before any further agent phase; the user re-routes after deciding. | **Yes** |

**Timing modifiers (optional, stackable — controlled vocabulary):**

| Modifier | Meaning | Part field |
|---|---|---|
| `(now)` | Fire immediately | Pos=1 for the recommended starting cluster |
| `(after Cluster X lands)` | Sequential dependency | Deps=`<Cluster X Part name>` |
| `(parallel-safe with Cluster X)` | Concurrent-safe | same Pos as Cluster X's Part |
| `(future scope — when Y triggers)` | Deferred; name ripeness trigger | un-sequenced Pos=`—`, Trigger=`Y` |
| `(blocked on Z)` | Blocked on external decision/asset | Trigger=`blocked on Z` until resolved |

**Worked examples:**

```
- Cluster 1 (Currency): → /architecture_brainstorm  (now — dependency root)
- Cluster 2 (Per-Run Events): → /architecture_brainstorm  (after Cluster 1 lands — needs currency primitives)
- Cluster 3 (Almanack): → /architecture_brainstorm  (now — parallel-safe with Cluster 1)
- Cluster 4 (Hub World): → /idea_brainstorm rerun  (only 4 survivors after filter)
- Cluster 5 (Cosmetic unlocks): → /architecture_brainstorm  (future scope — when MVP economy data exists)
- Workshop: End-Game Framing: → workshop  (B+C leading; user arbitrates)
```

**Workshop terminates the chain** with no worklog/User-Tasks cross-write; the brainstorm doc is sole carrier.

**Roadmap state mapping.** Routing action → Part State is the executor's contract: [`/update_roadmap`](../../commands/update_roadmap.md) Step 2 (idea-brainstorm input mapping). This skill supplies the Routing line; the command authors Parts.

**Recommended starting cluster (required when ≥2 actionable clusters):** name the cluster firing first + one-line rationale (dependency root / unblocks downstream / earliest arch decision needed). It becomes the Pos=1 Part.

**[Shared] Rationale spot-check** — `common.md §2`. **[Shared] User review gate** — `common.md §4`.

### Step 6: Invoke `/update_roadmap`

After the user approves the idea-bank doc:

- Bump `status` to `ideation-complete` + mirror in the Revision History footer (same edit; `common.md §5`).
- Invoke `/update_roadmap` with: the saved `ideas.md` path (identifies the topic folder); the Step 5 per-cluster Routing actions (its Step 2 translates them to Part States); the recommended starting cluster (becomes Pos=1).

It runs batch-propose — one approval applies all `roadmap.md` edits (Parts table, Mermaid block, derived views, revision log). With no roadmap in the folder, it proposes creating one from `common.md §6` schema; confirm and proceed.

**Never hand-edit OR `write_doc` `roadmap.md` from this skill** — the prohibition covers direct `Edit`/`Write` and worker-delegated `write_doc` equally (Step 5 *Roadmap.md is NOT saved here*).

### Handoff

- Surface the routing summary: count by action (M arch / K idea-rerun / L workshop), name the recommended starting cluster + one-line rationale.
- The roadmap.md `Currently ready to execute` view shows which arch sessions fire first; each hybrid gets its own arch session per `common.md §5.1`.

## 5. MCP-Offline Policy

**[Shared]** Obsidian MCP offline → non-event (native vault `Read`/`Write`/`Edit`). ai-worker/`write_doc` offline → substitute the executor per [`common.md §3`](../_brainstorm_shared/common.md) (CLAUDE.md OFFLINE FALLBACK).

## 6. Anti-Patterns

| Rationalization | Reality |
|---|---|
| "More ideas = better brainstorm" | Volume without filter is *dumping*; the user becomes curator. Surface the converged 3–5 honed per cluster. |
| "Generate all clusters first, then the user filters" | An N-cluster dump multiplies curate-load by N. Per-cluster pacing (diverge → filter → hone → present → user-react) is mandatory. |
| "Filter risks killing creative ideas" | Filter is explicit and auditable (tone / pillar / scope / mechanic / redundancy / novelty); uncertain culls flag `[mechanic-uncertain]`. Killing for clear tone or pillar mismatch is correct. |
| "User said *'give me everything you thought of'* — I'll surface the raw pool" | Route via converged output even then; the hidden pool lives in the doc's idea-pool section, not chat. Same litmus as §1's enumeration trigger. |
| "Mechanic conflicts will be caught at user-review" | User-review is design judgment, not fact-checking. Ideas contradicting established systems (potions costing mana to *craft* when the model is mana-at-*cast*) are a Step 3 filter failure. |
| "Let me also commit to an architecture during ideation" | That's `/architecture_brainstorm`. Premature commitment narrows the pool before it's populated. |
| "Idea-bank output should include implementation Parts directly" | Parts are roadmap-shape (`common.md §6`), authored by `/architecture_brainstorm` from a committed design. Routing yields `arch-pending` / `idea-rework` / `workshop-pending` via `/update_roadmap`, never `plan-pending` (that needs arch's 5-criterion gate). |
| "Skip the existing-doc check, this is just ideation" | §1 is shared with `/architecture_brainstorm` so both respect prior artifacts; don't redo prior idea-bank work. |
| "Just one question to scope-frame — let me ask three" | Step 2 is often empty; multi-question framing is usually architecture-narrowing in disguise. Ask one, populate ideas, see if more is needed. |
| "The prior doc says X, so the idea bank should respect X (deference-filter)" | Adjacent prior docs predate this brainstorm and may not survive contact with the systems being newly designed. Rationale using *only words from the prior doc* is deference, not filtering. Surface competing ideas; `/architecture_brainstorm` arbitrates (Step 1 *adjacent-design-space*). |
| "Add `workshop needed first` as a timing modifier on `→ /architecture_brainstorm`" | Workshop is an ACTION and terminates the chain; by arch time ideation must be settled. Use `→ workshop`. |
| "Per-cluster Findings let me record new arch commitments here" | Findings are PRIORS. Hard Gate litmus applies; agent-invented → `/architecture_brainstorm` Step 4. |
| "Bundle all per-cluster open questions into one global section" | The monolithic-handoff failure Per-Cluster Routing fixes. Per-cluster questions stay in-cluster; only cross-cluster ones go global. |

## 7. Cross-references

- [`common.md`](../_brainstorm_shared/common.md) — §1 existing-doc check, §1.1 resume table, §1.2 stale-roadmap remediation, §2 rationale spot-check, §3 MCP-offline, §4 user-review gate, §5 doc path/frontmatter, §5.1 spawn-placement, §6 roadmap.md schema (§6.10 Triggers), §7 phase cardinality, §8 `decisions.md`, §9 interview mechanic
- [`architecture_brainstorm`](../architecture_brainstorm/SKILL.md) runs AFTER this skill; [`debugging`](../debugging/SKILL.md) covers known-root-cause fixes, not idea generation
- [`/update_roadmap`](../../commands/update_roadmap.md) — Step 6; [`/idea_brainstorm_fanout`](../../commands/idea_brainstorm_fanout.md) — Step 3 `--fan_out`
- `feedback_no_unilateral_condensation.md` — Step 3 Diverge verbatim-port discipline
- `feedback_no_performative_agreement.md` — Step 2 Socratic-question opener discipline
- `feedback_session_start_hook_does_not_override_skill_procedure.md` — session-start hooks don't excuse skipping Step 2 / Step 3 gates
- CLAUDE.md §9 Tool Routing — §1 read_files routing (synthesis bundling)
