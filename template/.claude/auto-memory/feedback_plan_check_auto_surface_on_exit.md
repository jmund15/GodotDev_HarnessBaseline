---
name: plan-check-auto-surface-on-exit
description: "Plan Mode's ExitPlanMode doesn't auto-invoke /plan_check. When drafting a plan that meets the /plan_check litmus (3+ files / new types / 2+ family refactor / deletions), invoke /plan_check BEFORE ExitPlanMode. No hook enforcement — agent discipline only."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: df2b3626-2692-4176-8ae7-e5953e99385a
---

When drafting a plan inside Plan Mode, evaluate the `/plan_check` litmus before calling `ExitPlanMode`. If ANY criterion holds, invoke `/plan_check <plan-file-path>` first; act on findings; THEN ExitPlanMode. Don't rely on the user to invoke /plan_check after the fact — by then, ExitPlanMode has already requested approval and the workflow has moved on.

**Why:** In the P10 Hub Scaffold plan session (2026-05-19), I called ExitPlanMode without invoking /plan_check despite the plan meeting all 4 litmus criteria (15 files, 3 new types, new UI/Overlay/ folder, OverlayStack autoload conversion replacing the old declaration). The user manually invoked /plan_check after ExitPlanMode, which surfaced 2 critical findings that should have been caught earlier:
- `InputProfileDatabase.Instance` autoload assumption unverified (catch should have happened in /plan_part Phase 3)
- Test pins #7-9 were config-shape, not behavioral (catalog #16 failure mode, recurring)

Both were resolved via post-hoc plan-file revisions, but the workflow gap meant the user had to do the gating I should have done. Composition between Plan Mode (Claude Code built-in) and /plan_check (slash command) requires explicit orchestrator discipline.

**How to apply:** After Plan Mode Phase 4 (Final Plan write), run this checklist BEFORE Phase 5 (ExitPlanMode):
- Does the plan touch 3+ files? → /plan_check
- Does the plan introduce a new type, folder, or top-level concept? → /plan_check
- Does the plan refactor a 2+ subclass family? → /plan_check
- Does the plan delete or replace existing files? → /plan_check

Any "yes" → invoke `/plan_check <plan-path>` before ExitPlanMode. Apply findings inline. Then ExitPlanMode.

**Enforcement: agent discipline only.** A hook backstop was considered and rejected — the user's preference (2026-05-19) is no hard hook for /plan_check surfacing. The litmus is self-applied at plan-draft completion, OR the user invokes /plan_check manually after ExitPlanMode if they notice the agent missed it. Trade-off accepted: occasional missed gates in exchange for less workflow rigidity.

Related: [[feedback_invoke_named_skill_not_manual_equivalent]] — once the gate is established, use the named slash command rather than manually replicating its steps. [[feedback_verify_explore_agent_empirical_claims]] — the same discipline applies to subagent claims at Plan Mode time, which /plan_check would have caught for the "RunController is IGameScene impl" propagation error.
