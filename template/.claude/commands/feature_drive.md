---
allowed-tools: Workflow, Agent
description: Ship one-plan work with no roadmap Part, its design settled or open on technical choices, or on taste where the project's UI skill supplies the candidates; use /part_drive for a Part, /design_drive for multi-Part design
---

# /feature_drive — One-Plan Drive (Design Capture → Ship)

**Input:** work of size below parts (`execution_depth.md` §Fields), its design settled in the conversation, open on technical decisions, or open on taste where the project's UI skill supplies the candidates: `/feature_drive [<scope statement>]`, where no argument makes the conversation's scope the input. With no candidate approach, unless the `/idea_brainstorm` row of §Entry routing excepts the work, or at size = parts, exit and route per `execution_depth.md` §Entry routing.
**Enters at:** ground (design capture), then plan.
**Owner stop:** remaining owner forks batched at entry through `AskUserQuestion`, because the owner just spoke and this is the batching point `/part_drive` lacks; a fork found mid-run follows spine H3. Work a person sees takes its taste forks to the project's UI skill's review loop where it has one, which holds only the piece it shows.

Run the stages and halts in [`drive_spine.md`](../skills/_brainstorm_shared/drive_spine.md), slices in [`drive_execution.md`](../skills/_brainstorm_shared/drive_execution.md), and close in [`drive_close.md`](../skills/_brainstorm_shared/drive_close.md). This file holds only what a one-plan drive adds.

Which entry command fits a piece of work follows [`_brainstorm_shared/execution_depth.md`](../skills/_brainstorm_shared/execution_depth.md) §Entry routing.

## Design capture

A drive whose input is a batch of owner words (a review page's comments, one message holding several asks) folds the batch first, by the project's UI skill's fold step: one job reads the batch whole and writes `synthesis.md`, and capture and the plan's job map start from its causes.

When `execution_depth.md` §Admission table's Grounding row admits it, dispatch [`/explore`](explore.md) at capture, before drafting. `TOPIC` is the conversation's resolved design. Then:

0. Work a person sees loads the project's UI skill first.
1. Ground every captured claim per [`design_contract`](../skills/_brainstorm_shared/design_contract.md) clause 1: cite documentation for authored intent, and verify shipped-behavior claims in code. The rest of that contract governs autonomous design and does not bind here.
2. When decisions the conversation left open are technical, with no fork that needs the owner's taste, dispatch one architect-tier pass over them with the `/explore` dossier and the owner's settled rulings. Its brief opens with the simpler-path question (`execution_depth.md` §Simpler-path probe). Its decisions enter the capture with their evidence; a fork it finds that needs taste goes to the owner stop. When `orchestration` reference `drive_dispatch.md` *Plan drafts* routes the plan draft to an architect arm, this pass drafts the plan from its decisions.
3. Check fit against `architecture_philosophy` (modular, data-driven, deletion-test, §Orthogonal axis, §Authored-Data Integrity). The owner stop settles taste, so this step verifies the design is non-redundant and extension-shaped.
4. Distill the design into the plan file at `.claude/plans/<slug>.md`, context-free per [*Plan-file format*](../skills/_brainstorm_shared/plan_file_format.md): decisions as fact, rationale inline, and owner rulings recorded as constraints, so an executor never needs the transcript. When the captured work carries a harness deliverable that stands on its own beside the production change, partition into one plan per `PLAN_SHAPE` per that file and audit them in one `/plan_check` invocation. Inseparable mixed plans use the code lenses plus the meta lenses `/plan_check` requires.
5. Set the header `**Roadmap:** ad-hoc — /feature_drive`, which keeps `/session_end` drift detection from hunting a Part.
6. Read `Claude/Meta/Development-Focus.md` and name any misalignment between the captured design and the current focus in the design-capture summary, per [*Plan-file format*](../skills/_brainstorm_shared/plan_file_format.md)'s focus-note rule.

The drive then enters the spine at plan. A `/plan_check` non-convergence that resolves to a taste fork routes to the owner stop above instead of halting.

## Close

No roadmap flip. Report any touchpoints with tracked roadmap topics in the final report, and route deferrals through `/worklog`.
