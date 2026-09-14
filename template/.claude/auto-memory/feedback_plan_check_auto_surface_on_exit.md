---
name: plan-check-before-approval
description: Run the project plan-check gate after writing a qualifying plan and before asking for approval.
metadata:
  type: feedback
---

After writing a plan, evaluate the project's plan-check trigger before presenting it for approval.
If the trigger matches, run the named gate, resolve its findings, then present the plan.

**Why:** Approval should review a checked execution contract. A later gate makes the user catch defects
the planning workflow should have found.

**How to apply:** Use the current project-owned trigger. Common triggers include multi-file changes,
new types or folders, family refactors, and replacements or deletions. Do not hand-roll a named gate
or rely on the user to invoke it.
