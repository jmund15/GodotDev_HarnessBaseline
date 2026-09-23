---
name: plan-mode-retired-from-planning-flow
description: "Plan Mode is retired from this harness — every planning path runs in the normal auto-mode conversation. Plan FILES remain mandatory; the approval gate is the user's explicit go-ahead; nothing replaces Plan Mode's tool-enforced write lock."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 80422bbb-fc4d-4b9f-a63e-ad998868bd38
  modified: 2026-09-12T21:28:16.584Z
---

**Plan Mode is retired from every planning path in this harness** (owner directive 2026-08-05: *"plans should exist but plan mode itself is not necessary and we've basically outgrown it"*). No command enters it, no gate depends on it, and `EnterPlanMode`/`ExitPlanMode` appear in no `allowed-tools` list.

**What survives unchanged — do not let the retirement erode any of it:**

- **Plan files are still mandatory.** `.claude/plans/<slug>.md` remains the plan-review command's audit surface, the parity ledger for deletions, the Decision-record home, and what `/session_end` drift-detects on. "No Plan Mode ⇒ no plan file" is the rationalization to refuse.
- **Every gate stays.** Plan review, the regression gate, the halt valves, the appetite invariant, roadmap state flips. Retirement removed a *mode*, not a *gate*.
- **The human approval gate stays; only its mechanism changed.** It is the user's explicit go-ahead on the finished plan file, presented in conversation — never inferred from convergence, from "0 critical", or from silence.

**The one real loss: Plan Mode was the only TOOL-ENFORCED write lock during planning, and nothing replaces it.** `/part_drive --plan-only`'s *Write surface* constraint is now prose, not a wall — verified: `settings.json` has empty `permissions.deny`/`ask`, no `defaultMode`, and no PreToolUse hook gates writes on a planning phase. So an edit to production code during a planning loop is a self-inflicted defect that nothing will stop. Treat the constraint as binding precisely because it is unenforced.

**Replacement:** the project's explore command reports evidence and UNCOVERED dimensions. `Explore` is a tool profile, not a fixed model identity; verify the dispatch pin and runtime evidence.

**Meta-lesson worth more than the fact.** The memory this supersedes (`feedback_plan_mode_is_claude_code_built_in`) carried a `**Verified:** 2026-07-09` stamp asserting *"zero `EnterPlanMode`/`ExitPlanMode` references in `.claude/commands/` + `.claude/skills/`"*. By 2026-08-05 that was false — a then-existing plan-drive command declared both in its frontmatter. A verification stamp certifies a claim **on its date**; it does not keep the claim true, and a stamped-false claim is more dangerous than an unstamped one because it suppresses re-checking. Re-verify a stamped absence claim before relying on it.

Related: [[feedback_plan_files_are_context_free_execution_docs]]

**Verified:** 2026-09-04 memory-claim audit — `git grep EnterPlanMode|ExitPlanMode` over `.claude/commands .claude/skills .claude/agents .claude/settings.json` = 0 hits.
