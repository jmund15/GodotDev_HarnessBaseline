# Execution Depth — Scope-Proportional Process Ladder

> Shared depth ladder for the drive commands. Referenced by [`/worklog drive`](../../commands/worklog.md), [`/feature_drive`](../../commands/feature_drive.md), and [`/part_drive`](../../commands/part_drive.md) via *"depth per `_brainstorm_shared/execution_depth.md`."*
>
> This file is NOT a skill — no frontmatter, not directly invocable. It sets the default process depth a unit of work earns from its scope.

---

## The ladder

Keyed on the `worklog_reference` *Scope* vocabulary — that table is the SSOT; this one references it and never redefines it. Tiers state **process artifacts only**.

| Scope | Default process depth |
|---|---|
| 1 | No `/explore` dispatch (tier-1 items are the class `/explore`'s own SKIP litmus already sanctions — see rule 1), no plan file. Verification per the do-now file-type gates in [`worklog_drive_triage.md`](../../commands/agents/worklog_drive_triage.md) §do-now step 3 (the SSOT: `.cs` → `/regression_gate`; `.tres`/`.tscn` Logic-affecting → Logic suite; doc-only → none). |
| 2 | Plan is in-conversation; a plan *file* only when the executor is dispatched cold. `/explore` floor lenses only; skip them when the item's `Where:` paths were read first-party this session, run them otherwise. |
| 3 | Plan file + `/plan_check` (lens set by plan shape) + execute — the `/feature_drive` steps 2–6 shape. Full `/explore` per its trigger table. |
| 4 | Not drivable. Own design track: `/design_drive` (no design doc) or `/part_drive` (roadmap Part exists). |

Who executes each tier is not this table's call — CLAUDE.md §Model Delegation + [`orchestration`](../orchestration/SKILL.md) §11 own it.

## Reconciliation rules

1. **The independent litmuses stay the binding floor.** The ladder sets *default* depth; `/plan_check`'s litmus (3+ files / new types / deletions), CLAUDE.md gate 4, `/regression_gate` on `.cs`, and `/explore`'s own SKIP litmus (skip only on a single mechanical fix with a known root cause, or a same-session dossier) adjudicate independently. The ladder may narrow the LENS SET; it never decides whether a pass those litmuses mandate runs. A scope-1 `debug` item with an unknown root cause still gets the tier-2 floor-lens pass; a scope-2 item whose plan trips the plan_check litmus still writes a plan file and runs `/plan_check`. Deepen past the default freely — never skip a gate a litmus trips.

2. **The ladder governs process artifacts only** — `/explore` lens set, plan artifact, `/plan_check` depth. Dispatch grain and executor choice belong to CLAUDE.md §Model Delegation + `orchestration` §11; do not restate their thresholds here.
