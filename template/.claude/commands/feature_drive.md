---
description: Drive a design resolved in conversation to shipped code — no roadmap Part required. Design capture → in-session plan → plan_check convergence → execute → regression_gate → commits.
---

# /feature_drive — Ad-Hoc Feature Drive (Conversation Design → Ship)

The entry point between inline edits and [`/part_drive`](part_drive.md): the design surface is **the current conversation** (analysis + user rulings), not a roadmap Part. The invocation is the execution directive (`feedback_honor_execution_directive`); the entry gate and halt valves below are the only legitimate pauses.

**Invocation:** `/feature_drive [<scope statement>]` — with no argument, the resolved design in the current conversation is the scope.

Use `/part_drive` when the work IS a roadmap Part. Use `/architecture_brainstorm` or `/design_drive` when the design is NOT yet resolved (open approach questions, large/novel scope). Logged worklog items route to `/worklog drive`, which owns selection and the same depth ladder.

## Entry gate

1. **A resolved design must exist** in-conversation or in the argument. Neither → exit and route per the sibling table above; never improvise a design inside this command.
2. **Batch remaining taste forks NOW** via `AskUserQuestion` — the user just spoke; this is the batching point `/part_drive` lacks. A load-bearing fork discovered mid-run also routes to `AskUserQuestion`, never a silent default.
3. **Scope litmus** — if planning reveals the work is roadmap-shaped (new system, crosses subsystems beyond the conversation design, file list won't bound), halt and propose promotion to `/architecture_brainstorm` or a roadmap Part instead of driving on.
4. **Assess the ladder tier** of the captured design against [`_brainstorm_shared/execution_depth.md`](../skills/_brainstorm_shared/execution_depth.md), and run the flow at that depth. Tier 1–2 takes the ladder's reduced path — no plan file at tier 1, an in-conversation plan at tier 2 (a plan *file* only when the executor is dispatched cold), and `/explore` narrowed per the tier — with the ladder's rule-1 litmuses binding throughout: any pass `/plan_check`'s litmus, CLAUDE.md gate 4, `/regression_gate`, or `/explore`'s SKIP litmus mandates still runs. Tier 3 runs steps 1–7 as written. Tier 4 is rule 3's promotion case.

## The flow

Steps 2–6 are `/part_drive` steps 2–6 verbatim (plan in-session → `/plan_check` converge → execute → gate → commits); only steps 1 and 7 differ:

1. **Design capture (replaces the roadmap brief)** — close context gaps before distilling: dispatch [`/explore`](explore.md) HERE, at capture, where a finding can still reshape the design rather than invalidate a written plan. `TOPIC` is the conversation's resolved design. Its floor lenses cover what this step used to inline — `exp-memory` the mandatory memory pass, `exp-prior-art` the existing-abstraction inventory at introduction granularity (families own exports/params/flags, not just types — `rules/design_litmus.md` #1). A `premise-contradiction` claim is a design correction to absorb before distilling, never a note to carry forward; an UNCOVERED dimension is closed before proceeding. Then ground every captured claim per [`design_contract`](../skills/_brainstorm_shared/design_contract.md) clause 1 (documentation is a claim to check, never a source to cite — the rest of that contract governs autonomous *design* and does not bind here), and check fit against `architecture_philosophy` (modular, data-driven, deletion-test, §Orthogonal axis, §Authored-Data Integrity — the conversation resolved taste; this step verifies the design is non-redundant and extension-shaped). Then distill into the plan file at `.claude/plans/<slug>.md`, context-free per [*Plan-file format*](../skills/_brainstorm_shared/plan_file_format.md): decisions as fact, rationale inline, user rulings recorded as constraints (an executor must not need the transcript). One design can still be mixed-shape: when the captured work carries a harness deliverable that stands on its own alongside the production change, partition into one plan per `PLAN_SHAPE` per that file — a mixed plan classifies `code`, and its `.claude/**` edits then get no gate at all. Header: `**Roadmap:** ad-hoc — /feature_drive` (keeps `/session_end` drift detection from hunting a Part). Read `Claude/Meta/Development-Focus.md`; name any misalignment between the captured design and the current focus in the design-capture summary — advisory, never a gate.
2. Plan in-session — `/part_drive` step 2, with the ad-hoc header above.
3. Audit & converge — `/part_drive` step 3, except taste forks route per entry-gate rule 2 rather than halting.
4. Execute — `/part_drive` step 4 verbatim (TDD per slice, strict in Logic; dispatch shape free per the `orchestration` skill, including its Workflow authorization; spec authorship, diff review, gate adjudication, and commits stay at the driving session).
5. Gate — `/part_drive` step 5 (full `/regression_gate`, single-flight).
6. Commits — `/part_drive` step 6 (categorical split, submodule-first, index hygiene, no push).
7. **Close out (no roadmap)** — no `/update_roadmap`. Instead: surface any tracked-roadmap-topic touchpoints in the final report; route deferrals through `/worklog`; offer `/pr_ready` on feature branches.

**Context checkpointing** applies throughout (`orchestration` SKILL §10) — same contract as `/part_drive`.

## Halt valves

`/part_drive` valves (a)–(e) and (g) apply unchanged. Valve (f) is replaced by entry-gate rule 2: taste forks ask via `AskUserQuestion` instead of halting — the user is same-session (a (g) non-convergence that resolves to a taste fork routes there too).

## Anti-patterns

| Rationalization | Reality |
|---|---|
| "The design is in the conversation — skip the plan file." | The plan file is the `/plan_check` audit surface and the context-free record; conversation context dies at compaction. Write it at tier 3+. The one sanctioned reduction is entry-gate rule 4's tier-1/2 path — and even there, a plan file lands whenever the executor is dispatched cold or `/plan_check`'s litmus trips. |
| "We discussed it thoroughly — skip `/plan_check`." | Resolved design ≠ audited plan. Convergence is step 3, not an option. The tier-1/2 reduction narrows the lens set, never the litmus: if `/plan_check`'s own litmus trips (3+ files, new types, deletions), it runs at every tier. |
| "It grew, but we're mid-drive — keep going." | Roadmap-shaped scope discovered mid-run is entry-gate rule 3: halt, propose promotion. |
| "It's ad-hoc, so relax TDD / the gate." | Domain split and `/regression_gate` bind on provenance-blind rules — all `.cs` changes, no carve-outs. |
