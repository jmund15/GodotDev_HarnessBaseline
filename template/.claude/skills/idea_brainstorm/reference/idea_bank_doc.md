# Step 5: Save the idea-bank doc

**[Shared]** Path tiebreaker, folder-per-topic, frontmatter — [`common.md §5`](../../_brainstorm_shared/common.md); spawn-placement for arch sessions — §5.1.

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

**Roadmap.md is NOT saved here.** This Step saves `ideas.md` only; `/update_roadmap` authors the roadmap in Step 6 under its single-executor rule ([`update_roadmap.md`](../../../commands/update_roadmap.md)).

#### Per-cluster handoff-readiness gate

Evaluate each cluster against three criteria before assigning a Routing action; the result decides arch vs rerun.

Read [`handoff_and_routing.md`](handoff_and_routing.md) §Per-cluster handoff-readiness gate for the criteria, the pass-status table, the workshop override and the rerun stop-condition.

#### Per-Cluster Routing — action × timing

Each cluster's `Routing` subsection: exactly ONE action + zero-or-more timing modifiers. Cross-Cluster Workshop Topics carry their own per-topic Routing line.

Read [`handoff_and_routing.md`](handoff_and_routing.md) §Per-Cluster Routing for the action and timing-modifier tables, worked examples, the roadmap state mapping and the recommended-starting-cluster rule.

**[Shared] Rationale spot-check** — `doc_production.md §2`. **[Shared] User review gate** — `doc_production.md §4`.
