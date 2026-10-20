---
disable-model-invocation: true
---

# `/update_roadmap` Steps 4–5 — Mermaid and derived views

Read at Steps 4 and 5 of [`update_roadmap.md`](../update_roadmap.md). Not auto-loaded. When the project has a roadmap generator (`roadmap_triggers_mvp.md` §6.13), its Step 3 run has already produced the Mermaid block, the derived views and the MVP recompute, and these steps are the review; without one, render them by hand from the sections named below.

## Step 4 — Render Mermaid diagram

Review the regenerated block in the Step 7 diff; never hand-edit it: fix the Parts table and rerun. By hand, render it from [`common.md`](../../skills/_brainstorm_shared/common.md) §6.4 and replace the block under the `## Current state — Mermaid` heading. The classDef block is §6.4's, emitted verbatim. Palette, classDef discipline, and renderer constraints: see the `mermaid_diagrams` skill.

| Rationalization | Reality |
|---|---|
| "Don't regen Mermaid; the old one is close enough" | Mermaid drift is silent and accumulates. Always regen — costs are negligible. |

## Step 5 — Compute derived views

Review the derived views in the Step 7 diff. By hand, compute them from `common.md` §6.5 and replace the existing derived-view sections.

## Step 5 — MVP Checkpoints recompute

Review the changed check-marks, Status lines and warnings in the Step 7 diff. By hand, recompute them from `roadmap_triggers_mvp.md` §6.11 *Status* and *Auto-computation*. Narrative MVP fields are never touched (§6.11 *Auto-computation*).
