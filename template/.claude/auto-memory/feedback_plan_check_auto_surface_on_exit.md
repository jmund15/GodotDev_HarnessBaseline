---
name: plan-check-auto-surface-on-exit
description: "Nothing auto-invokes /plan_check. When a drafted plan meets the litmus (3+ files / new types / 2+ family refactor / deletions), invoke /plan_check BEFORE presenting the plan for approval. No hook enforcement — agent discipline only."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: df2b3626-2692-4176-8ae7-e5953e99385a
  modified: 2026-08-05T17:35:47.980Z
---

Once a plan file is drafted, evaluate the `/plan_check` litmus **before presenting the plan for approval**. If ANY criterion holds, invoke `/plan_check <plan-file-path>` first; act on findings; THEN present. Don't rely on the user to invoke it after the fact — by then approval has been requested and the workflow has moved on.

> Plan Mode is retired ([[feedback_plan_mode_retired_from_planning_flow]]), so the trigger is the **plan-file write**, not `ExitPlanMode`. The rule and its timing are unchanged; only the anchor moved.

**Why:** In the P10 Hub Scaffold plan session (2026-05-19), I called ExitPlanMode without invoking /plan_check despite the plan meeting all 4 litmus criteria (15 files, 3 new types, new UI/Overlay/ folder, OverlayStack autoload conversion replacing the old declaration). The user manually invoked /plan_check after ExitPlanMode, which surfaced 2 critical findings that should have been caught earlier:
- `InputProfileDatabase.Instance` autoload assumption unverified (catch should have happened in /plan_part Phase 3)
- Test pins #7-9 were config-shape, not behavioral (catalog #16 failure mode, recurring)

Both were resolved via post-hoc plan-file revisions, but the workflow gap meant the user had to do the gating I should have done. Composition between Plan Mode (Claude Code built-in) and /plan_check (slash command) requires explicit orchestrator discipline.

**How to apply:** Once the plan file is written, run this checklist BEFORE presenting it for approval:
- Does the plan touch 3+ files? → /plan_check
- Does the plan introduce a new type, folder, or top-level concept? → /plan_check
- Does the plan refactor a 2+ subclass family? → /plan_check
- Does the plan delete or replace existing files? → /plan_check

Any "yes" → invoke `/plan_check <plan-path>` first. Apply findings inline. Then present for approval.

**Enforcement: agent discipline only.** A hook backstop was considered and rejected — the user's preference (2026-05-19) is no hard hook for /plan_check surfacing. The litmus is self-applied at plan-draft completion, OR the user invokes /plan_check manually after the fact if they notice the agent missed it. Trade-off accepted: occasional missed gates in exchange for less workflow rigidity.

**Verified:** 2026-08-05 — `settings.json` wires only `plan_memory_reminder.py`, on PostToolUse/`Write|Edit` (a passive Memory+Skill nudge, fired by the plan-file write now that the `ExitPlanMode` matcher is gone); no hook or setting invokes `/plan_check`, so manual invocation is still required.

Related: [[feedback_invoke_named_skill_not_manual_equivalent]] — once the gate is established, use the named slash command rather than manually replicating its steps. [[feedback_delegate_output_trust]] — the same discipline applies to subagent claims at plan-draft time, which /plan_check would have caught for the "RunController is IGameScene impl" propagation error.
