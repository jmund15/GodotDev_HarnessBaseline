# Execution Depth — Scope-Proportional Process Ladder

> Shared depth ladder for the drive commands. Referenced by [`/worklog drive`](../../commands/worklog.md), [`/feature_drive`](../../commands/feature_drive.md), and [`/part_drive`](../../commands/part_drive.md) via *"depth per `_brainstorm_shared/execution_depth.md`."*
>
> This file sets the default process depth a unit of work earns from its scope.

---

## The ladder

Keyed on the `worklog_reference` *Scope* vocabulary — that table is the SSOT; this one references it and never redefines it.

| Scope | Default process depth |
|---|---|
| 1 | No `/explore` dispatch (tier-1 items are the class `/explore`'s own SKIP litmus already sanctions — see rule 1), no plan file. Verification per the do-now gates in [`worklog_triage.md`](../../commands/reference/worklog_triage.md) §Step 5 → `do-now` (the SSOT: a gated change class → its gate; doc-only → none). |
| 2 | Plan is in-conversation; a plan *file* only when the executor is dispatched cold or `/plan_check` requires it. `/explore` owns its no-op rule and selected lenses. |
| 3 | Plan file + `/plan_check` (lens set by plan shape) + execute — the `/feature_drive` steps 2–6 shape. Full `/explore` per its trigger table. |
| 4 | Not drivable. Own design track: `/design_drive` (no design doc) or `/part_drive` (roadmap Part exists). |

## Reconciliation rules

1. **Each command owns its gate and coverage.** This ladder sets default depth, not exemptions from `/plan_check`, the CLAUDE.md Planning Phase Checklist, the project's regression gate (`change_control` §Gate cadence names it), or `/explore`'s no-op rule. An unknown-root-cause debug item still invokes `/explore`; every selected floor and triggered lens remains required. A scope-2 item meeting the plan-check threshold writes a plan file and runs that check.

2. **The ladder governs process artifacts only** — plan artifact and default drive depth. Each invoked command owns its lens selection; panel seat width belongs to `orchestration` §2; dispatch grain and executor choice to §5/§11.
