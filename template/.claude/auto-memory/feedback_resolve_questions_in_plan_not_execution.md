---
name: resolve-plan-questions-during-planning
description: Resolve every code-answerable question before handing a plan to an executor.
metadata:
  type: feedback
---

A plan handed to another session must answer every question the codebase can resolve.

**Why:** The executor lacks the planning conversation. A deferred code question causes a stall, a
guess, or a second costly design pass.

**How to apply:**
- Ask whether the executor can act without requesting more context.
- Resolve code, API, placement, and lifecycle questions with focused reads before approval.
- Ask the user only for choices the repository cannot own, such as product priorities or taste.
- Put the chosen answer and its evidence in the plan.
