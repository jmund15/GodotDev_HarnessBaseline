---
name: Spec-Doc Coverage subsection before plan-mode exit
description: Plans downstream of a brainstorm/design doc need an explicit Spec-Doc Coverage mapping (design mechanic → plan section) before ExitPlanMode.
type: feedback
originSessionId: 15bc6648-e4d1-4a64-b970-d32e8c122873
---
Before exiting plan mode for any plan downstream of a brainstorming/design doc: **explicitly verify each mechanic from the design doc is addressed in the plan, OR explicitly out-of-scoped with rationale.** Missing-spec slips that get caught at plan-exit review cost a re-plan cycle; ones that don't get caught ship without the user noticing the gap until playtest.

**How to apply:** In the plan-mode draft, add a **Spec-Doc Coverage** subsection that maps brainstorm-doc mechanics → plan section. One row per design-doc mechanic. Missing rows = explicit defer-reason or pull-into-plan. The subsection sits between the action list and the open-questions list; it's a checklist for the user to skim before approving.

**Why:** Brainstorm/design docs enumerate the contract; plans are the execution against that contract. Without the mapping, it's easy to forget a mechanic — especially mid-tier mechanics (knockback dropoff, hitbox lifecycle) that aren't the headline feature but ARE in the design. The user shouldn't have to mentally diff the doc against the plan.

**Concrete (2026-05-10):** Wind Blast plan-mode exit blocked by user with *"that's a major slip"* when `hitbox-lifecycle` + `knockback-dropoff` (both in brainstorm doc) were missing from the plan. Both belonged in execution and were skipped. A Spec-Doc Coverage subsection would have surfaced the gaps before plan-mode exit, saving a re-plan cycle.

**Trigger:** Apply whenever Plan Mode is entered and an upstream brainstorm/design doc exists (typically in Obsidian `DevProjects/{{PROJECT_NAME}}/Claude/BrainstormingDesigns/`). Doesn't apply to ad-hoc plans with no design-doc lineage.

**Migrated from MCP** (was `Spec_Doc_Coverage_Before_Plan_Exit`, entityType `process_rule`) 2026-05-11.
