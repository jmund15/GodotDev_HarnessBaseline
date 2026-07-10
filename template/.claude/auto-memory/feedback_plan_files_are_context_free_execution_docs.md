---
name: feedback_plan_files_are_context_free_execution_docs
description: "Plan files must read as clean end-to-end execution docs for a context-free reader (executor + reviewer) — resolved design as fact, rationale inline, forks resolved before approval; discovery/audit process-meta stays in the presentation message, not the plan."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 1088c47a-48d1-4302-9f46-cf19e12290b9
---

A plan file is a context-free execution document — for the executor AND the user reviewing it for approval, not for the audit. Write it for someone with zero discovery/planning context who just wants the instructions: a comprehensive, sequential, methodical procedure. State the **resolved** design as fact — never "the design said X, we changed to Y"; write Y. The reader doesn't care what the original said or what changed.

**Why:** appendix/footnote sections that narrate what `plan_part` drifted, what `plan_check` flagged each round, or the convergence basis are process history — they bloat and disjoint the execution doc, and a cold executor (`/part_execute`, `/plan_handoff`) has to wade through discovery noise to reach the procedure.

**How to apply:** weave load-bearing rationale **inline** at the decision it justifies (one concise WHY where a choice is non-obvious or dodges a named gotcha), in the relevant section/slice — never a trailing "Decision record" / "critique trail" dump or a leading "Scope resolution" preamble. Keep the process narrative (drift, per-round critique, convergence basis) in the `ExitPlanMode` **presentation message** to the user. Resolve any fork needing the user's input via `AskUserQuestion` BEFORE presenting for approval (`ExitPlanMode` asserts the plan is ready) — the approved plan carries zero open user-decisions. Canonical rule: `skills/_brainstorm_shared/plan_file_format.md` (referenced by `plan_drive`/`part_drive`/`part_execute` + a `CLAUDE.md` pointer). Related: [[feedback_doc_revision_in_place]].
