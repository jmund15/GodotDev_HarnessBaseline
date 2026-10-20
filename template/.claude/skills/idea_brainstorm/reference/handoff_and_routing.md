# Handoff readiness and per-cluster routing

The detail of the Step 5 subsections of `SKILL.md` that assign a cluster's Routing action, moved verbatim. The headings keep the names `SKILL.md` and its citers use.

## Per-cluster handoff-readiness gate

1. **Approach-diversity** — at least 2 architecturally-distinct survivors, since a comparison needs two candidates; cosmetic variations of one mechanic don't count. Failure: the approach comparison in the `architecture_brainstorm` skill's Socratic-and-approaches Step 4 procedure collapses to one "obvious" answer.
2. **Concreteness floor** — each survivor meets the Step 3 Hone bar: one specific sentence grounded in named systems, numbers, or lore. Failure: arch does Hone work belonging to Step 3.
3. **Cluster-boundary firmness** — scope is stateable in one sentence EXCLUDING neighboring clusters' content; candidates stopped leaking in from adjacent design space during honing. Failure: arch scope balloons, fan-out fires prematurely, cluster-scoped consumption can't anchor.

| Pass status | Routing action | Rationale |
|---|---|---|
| **All 3 pass** | `→ /architecture_brainstorm` | Pool is mature; arch runs on real material. |
| **Any of 3 fails** | `→ /idea_brainstorm rerun` | Name the failing criterion in the rerun framing (*"rerun for approach-diversity — current survivors are 4 cosmetic variations of mechanic X"*). |

**Open Questions are NOT a gate criterion.** The `architecture_brainstorm` skill's Socratic-and-approaches Step 2 procedure asks them; unanswered ones are valid handoff material and become its multi-choice seeds. Gating on "all questions answered" pushes arch work into this skill.

**`→ workshop` overrides the gate.** A cluster surfacing a user-decision-required tension (competing pillars, leading-hypothesis arbitration) routes to `→ workshop` regardless of the 3-criteria status — the gate only differentiates arch-vs-rerun.

**Rerun stop-condition.** Reruns sharpen the failing criterion in place. If a second rerun on the same cluster fails the same criterion, the problem is scope/boundary (criterion 3): split the cluster, merge it into a neighbor, or escalate to `→ workshop`.

## Per-Cluster Routing — action × timing

**Actions (mandatory, exactly one):**

| Action | Meaning | Terminates chain? |
|---|---|---|
| `→ /architecture_brainstorm` | Ready to architect; the arch session scopes to THIS cluster (the `architecture_brainstorm` skill's Socratic-and-approaches Step 1 procedure). | No |
| `→ /idea_brainstorm rerun` | Pool too thin (fails the readiness gate's approach-diversity or concreteness criterion), new sub-themes, or leading-hypothesis needs honing. | No |
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

**Roadmap state mapping.** Routing action → Part State is the executor's contract: [`/update_roadmap`](../../../commands/update_roadmap.md) Step 2 (idea-brainstorm input mapping). This skill supplies the Routing line; the command authors Parts.

**Recommended starting cluster (required when ≥2 actionable clusters, since a lone cluster is no choice):** name the cluster firing first + one-line rationale (dependency root / unblocks downstream / earliest arch decision needed). It becomes the Pos=1 Part.
