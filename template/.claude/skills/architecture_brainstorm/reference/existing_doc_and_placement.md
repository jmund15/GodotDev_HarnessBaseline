# Step 1: Existing-doc check + spawn-placement decision

**[Shared]** See [`_brainstorm_shared/common.md` §1](../../_brainstorm_shared/common.md) — the three required asks (existing design doc / memory gotchas / does a family already own it), dispatched as [`/explore`](../../../commands/explore.md) lenses. Step 3 below still owns the FULL abstraction inventory; §1's `exp-prior-art` is what surfaces an obvious owner early enough to reshape the topic.

**Architecture-specific consideration:** the digest should ALSO surface any `ideation-complete` idea-bank doc (`ideas.md` in a topic folder) covering the same topic. If one exists, load it — it's the curated candidate pool that Steps 2 and 4 will draw from. If not (and the topic isn't a mature domain), redirect to `/idea_brainstorm` first.

**Cluster-scoped consumption (when upstream `ideas.md` has Per-Cluster Routing):** Scope this invocation to the named cluster only.

- Read the cluster section: survivors (Step 4 sourcing), Findings (priors), Open Questions (Socratic seeds), Routing (timing).
- Other clusters → SEPARATE invocations producing separate `arch-<cluster>.md` files in the SAME parent topic folder.
- Cross-cluster hybrids → SEPARATE arch session per hybrid (`arch-<hybrid-slug>.md`, same parent folder).
- Cross-cluster Open Questions are NOT this session's responsibility.
- Frontmatter: `derived_from: ../ideas.md`, `cluster: <cluster-slug>`.

**Default (no Per-Cluster Routing):** Whole-doc consumption (mature-domain, user-direct framing, or legacy doc). Output: a fresh topic folder with `arch.md` inside per `common.md §5`.

**Spawn-placement decision (apply if this session is spawning a child OR is itself a child of a parent brainstorm):**

See [`common.md §5.1`](../../_brainstorm_shared/common.md) — three placements (same-folder sibling / child subfolder / sibling folder) discriminated by a three-criterion test. Criteria map to outcomes per the §5.1 table (NOT "all three must hold"). Resolve at this step so Step 6's save path is unambiguous.

**Quick decision:**
- Just an extension paragraph → **same-folder** (no new roadmap).
- Own brainstorm session + independent maturity, parent-confined audience → **child subfolder** (submap; criterion 3 is *expected* to fail — parent-confined IS the definition).
- All three pass (multi-parent audience) → **sibling folder**.

**Most common gotcha:** failing a legitimate submap candidate against criterion 3 ("only serves this parent"). Criterion 3 is the submap-vs-sibling distinguisher, NOT a submap gate. See §5.1 *Common gotchas* table.
