---
description: Drive a plan-pending Part to ship or an approval-ready plan
---

# /part_drive — Part Drive (Brief → Plan → [Approve] → Execute → Ship)

**Input:** a `plan-pending` Part: `/part_drive <part-name> [roadmap-path] [--plan-only]`, resolved on the nearest-ancestor `roadmap.md`.
**Enters at:** intake, through the `/plan_part` brief.
**Owner stop:** with `--plan-only`, approval of the converged plan; without it, none before land.

Run the stages, halts, slices and close in [`drive_spine.md`](../skills/_brainstorm_shared/drive_spine.md). This file holds only what a Part adds.

Which entry command fits a piece of work (this one, [`/part_execute`](part_execute.md), [`/feature_drive`](feature_drive.md)) follows [`execution_depth.md`](../skills/_brainstorm_shared/execution_depth.md) §Entry routing.

## The flag

`--plan-only` stops at an approval-ready plan and hands off. Everything else is identical. The differences all follow from one fact: `--plan-only` has an approval presentation, and the unflagged form does not. Chain position comes from the caller: a Part inside a multi-Part drive is mid-chain, and a standalone Part is final.

- **Forks.** Under `--plan-only`, batch every load-bearing taste fork into one owner question batch before presenting, and write the picks in as fact, so the approved plan carries no open owner decision. Unflagged, such a fork is spine H3.
- **Write surface.** Under `--plan-only`, write only the plan file and `/explore`'s scratchpad files: no production code, no tests, no roadmap edits, which belong to `/plan_handoff`. Nothing enforces this rule at the tool layer, so you honor it. An edit outside those paths during the plan phase is a defect.
- **Stages run.** `--plan-only` runs through plan check and then presents for approval. Unflagged runs through land.

## Brief

Run [`/plan_part <part>`](plan_part.md)'s briefing procedure; its HARD-STOPs and State gates bind. A Part that is not `plan-pending`, an unresolved name or an unresolved redirect ends the drive: surface the redirect and stop (a pre-flight abort, not a halt). Macro drift is spine H1. Read only the Part row, its sibling rows and the *Currently ready* section of the roadmap. They carry the boundary signal for what prior Parts shipped and later Parts defer.

## Plan

The plan file lands at `.claude/plans/<slug>.md` with the header `**Roadmap:** <path> — Part **<ID>**`, which drives `/session_end` drift detection. Its sections and admission follow [`plan_file_format.md`](../skills/_brainstorm_shared/plan_file_format.md) and `execution_depth.md` §Admission table.

Who drafts the plan (an architect-tier arm or this session), at what effort and through which brief follows `orchestration` reference `drive_dispatch.md` *Plan drafts*.

## Close

Unflagged, `/update_roadmap` flips the Part to `complete` with the close record spine §Close step 1 produced as its Trigger (`roadmap_triggers_mvp.md` §6.10), applied directly under the one-shot directive with its diff in the final report. Under `--plan-only`, first confirm the plan satisfies [*Plan-file format*](../skills/_brainstorm_shared/plan_file_format.md), then present it with a brief account of consequential drift, contradicted premises, resolved forks and the convergence basis in the message, not the plan; omit unchanged rounds. Name the plan file path and stop for the owner's word. On approval, offer [`/plan_handoff <plan-file>`](plan_handoff.md) with the plan path only; offer, never auto-chain.
