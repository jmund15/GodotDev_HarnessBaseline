---
allowed-tools: Workflow, Agent
description: Ship one-plan work with no roadmap Part, its design settled or open on technical choices, or on taste where the project's UI skill supplies the candidates; use /part_drive for a Part, /design_drive for multi-Part design
---

# /feature_drive — One-Plan Drive (Design Capture → Ship)

**Input:** work of size below parts (`execution_depth.md` §Fields), its design settled in the conversation, open on technical decisions, or open on taste where the project's UI skill supplies the candidates: `/feature_drive [<scope statement>]`, where no argument makes the conversation's scope the input. With no candidate approach, unless the `/idea_brainstorm` row of §Entry routing excepts the work, or at size = parts, exit and route per `execution_depth.md` §Entry routing.
**Enters at:** ground (design capture), then plan.
**Owner stop:** an owner fork is asked when it appears and before any check would assume its answer: the request's own forks at intake, the design's when it returns (`orchestration` reference `integration.md` §When it returns); a fork found later follows spine H3. Work a person sees takes its taste forks to the project's UI skill's review loop where it has one, which holds only the piece it shows.

Run the stages and halts in [`drive_spine.md`](../skills/_brainstorm_shared/drive_spine.md), slices in [`drive_execution.md`](../skills/_brainstorm_shared/drive_execution.md), and close in [`drive_close.md`](../skills/_brainstorm_shared/drive_close.md). This file holds only what a one-plan drive adds.

Which entry command fits a piece of work follows [`_brainstorm_shared/execution_depth.md`](../skills/_brainstorm_shared/execution_depth.md) §Entry routing.

## Design capture

A drive whose input is a batch of owner words (a review page's comments, one message holding several asks) folds the batch first, by the project's UI skill's fold step: one job reads the batch whole and writes `synthesis.md`, and capture and the plan's job map start from its causes.

Ground at the depth the sizing states (`drive_spine.md` §Stages): the session's own reads for a narrow change, else [`/explore`](explore.md) on the design's questions, with step 2's design job in the same dispatch where `explore.md` Phase 2's join applies, and launched with the closed dossier's path where it falls back. `TOPIC` is the conversation's resolved design. Then:

0. Work a person sees loads the project's UI skill first.
1. Ground every captured claim per [`design_contract`](../skills/_brainstorm_shared/design_contract.md) clause 1: cite documentation for authored intent, and verify shipped-behavior claims in code. Clauses 2 and 9 bind here too, at the design's depth; the rest governs `/design_drive`.
2. When decisions are left open and they need evidence the session lacks or judgment deeper than a ruling (`orchestration` reference `brief_checks.md` §Gather, then judge), dispatch one design job over them, joined to Ground where `explore.md` Phase 2's join applies: its brief holds the owner's words and rulings from the record and what this session knows that bears on the design, as evidence and questions, never conclusions; the grounding result reaches it through the engine's hand-off, or by the closed dossier's path where the join falls back. It returns `design_contract.md` clause 9's hand-off; its decisions enter the capture with their evidence, and the session routes what it returns (`drive_spine.md` §After each stage). When `orchestration` reference `drive_dispatch.md` *Plan drafts* routes the plan draft to an architect arm, this job drafts the plan from its decisions.
3. Check fit against `architecture_philosophy` (modular, data-driven, deletion-test, §Orthogonal axis, §Authored-Data Integrity). The owner stop settles taste, so this step verifies the design is non-redundant and extension-shaped.
4. Distill the design into the plan file at `.claude/plans/<slug>.md`, context-free per [*Plan-file format*](../skills/_brainstorm_shared/plan_file_format.md): decisions as fact, rationale inline, and owner rulings recorded as constraints, so an executor never needs the transcript. When the captured work carries a harness deliverable that stands on its own beside the production change, partition into one plan per `PLAN_SHAPE` per that file and audit them in one `/plan_check` invocation. Inseparable mixed plans use the code lenses plus the meta lenses `/plan_check` requires.
5. Set the header `**Roadmap:** ad-hoc — /feature_drive`, which keeps `/session_end` drift detection from hunting a Part.
6. Read `Claude/Meta/Development-Focus.md` and name any misalignment between the captured design and the current focus in the design-capture summary, per [*Plan-file format*](../skills/_brainstorm_shared/plan_file_format.md)'s focus-note rule.

The drive then enters the spine at plan. A `/plan_check` non-convergence that resolves to a taste fork routes to the owner stop above instead of halting.

## Close

No roadmap flip. Report any touchpoints with tracked roadmap topics in the final report, and route deferrals through `/worklog`.
