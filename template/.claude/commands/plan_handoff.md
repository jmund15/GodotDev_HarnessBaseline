---
allowed-tools: Read
description: Adopt an approved, converged plan in an executor session
---

# /plan_handoff — Execute an Approved Plan

**Input:** an approved plan file: `/plan_handoff <plan-file-path>`.
**Enters at:** execute.
**Owner stop:** none.

Run the stages, halts, slices and close in [`drive_spine.md`](../skills/_brainstorm_shared/drive_spine.md). This file holds only what a handoff adds.

Begin only after the plan is approved and converged. Verify the plan and the diff with enough usable context; a fresh session is not a validity gate. Executor role and effort follow `orchestration` and the model registry.

## Procedure

1. Read `$ARGUMENTS`. If the path is missing, empty, or the Read fails, abort with:

   > `Plan file not found or unreadable: <path>.`

2. Adopt the following execution stance for the rest of this session:

   - The plan file is the authority. Follow it exactly where unambiguous.
   - Surface to the user (do not improvise) at spine halts H1, H4 and H5.
   - Commit gates follow `change_control`, whose commit guards enforce the regression gate.
   - Verify load-bearing empirical claims (file paths, type existence, prior-art assertions) before acting on them.
   - Mechanical execution is yours to make — don't ask permission for unambiguous steps.
   - Do not read prior session transcripts or memory snapshots; the plan file is authoritative for scope. **Carve-out:** auto-memory `arch_rule_*`/`gotcha_*` entries and path-scoped `.claude/rules` remain in force — when a slice introduces something the plan didn't describe (a new type, `[Export]`, or scene node), consult them; the plan bounds WHAT you build, never which design rules apply.

3. Begin execution of the plan's first step.
