---
disable-model-invocation: true
---

# `/update_roadmap` Step 2 — resolve proposed edits from inputs

Read at Step 2 of [`update_roadmap.md`](../update_roadmap.md). Not auto-loaded.

Translate inputs into concrete edit operations on the Parts table.

## From an idea brainstorm

Each cluster's Per-Cluster Routing maps to a Part:

| Cluster Routing | Resulting Part State |
|---|---|
| `→ /architecture_brainstorm` | `arch-pending` |
| `→ /idea_brainstorm rerun` | `idea-rework` (existing Part) or `idea-pending` (new sub-topic) |
| `→ workshop` | `workshop-pending` |

Cluster Open Questions / Findings populate the Trigger and Source fields. Pos is `—` (un-sequenced) unless the cluster's Routing timing modifier names a position (e.g., `(now — dependency root)` → propose Pos 1).

## From an arch brainstorm

Each Part the design authored maps to a Parts table row:

| Arch-brainstorm output | Resulting Part State |
|---|---|
| Part passing the `architecture_brainstorm` skill's Part-readiness gate procedure | `plan-pending` |
| Part with open arch design questions (architectural fork) | `arch-pending` |
| Part needing creative ideation (creative fork) | `idea-pending` |
| Part awaiting user decision | `workshop-pending` |
| Part blocked on a feel-fork the design can't grill (per [`part_authoring.md`](../../skills/architecture_brainstorm/reference/part_authoring.md) §Part-readiness gate, feel-fork row) | `prototype-pending` |
| Part marked user-owned (per the `architecture_brainstorm` skill's Part-authoring user-owned-question rule) | `user-owned` |

Each Part inherits Deps from the design doc's enumerated dependencies and gets a Source link to the design section.

## Existing Part decomposed via child-subfolder spawn

Per arch SKILL Step 8 spawn-placement extension + [common.md §5.1](../../skills/_brainstorm_shared/common.md) — transitions an existing Part to `submap-pending`. Distinct from the table above: this is a transition on a pre-existing Part, NOT a new Part. The Part's name, Deps, Source, and dependent edges are preserved; only State + Trigger change. Trigger format: `"Decomposed into <child-folder>/roadmap.md (N child Parts)."` per roadmap_triggers_mvp.md §6.10. The child sub-roadmap is also added to the parent's `Spawned sub-brainstorms` section in the same batch.

## Re-authoring paths

For `/update_roadmap split` outcomes + re-decomposition triggered by the `architecture_brainstorm` skill's Part-readiness gate hard-stop remediation:

| Pre-existing State + remediation | Children's State |
|---|---|
| `plan-pending` Part split with known lines | Each child gated by the `architecture_brainstorm` skill's Part-readiness gate procedure; typically `plan-pending` |
| Part kicked to `arch-rework` with split charter | Children start `plan-pending` after the arch-rework session lands |

## Standalone

The named transition applies directly.

## Anti-pattern

| Rationalization | Reality |
|---|---|
| "Auto-promote freshly-arch'd Parts to Pos values inferred from Deps" | Pos assignment is a user judgment call about priority/parallelism. Default to un-sequenced (`—`) on new Parts; user explicitly promotes via `promote` op or by editing during the batch-diff review. |
