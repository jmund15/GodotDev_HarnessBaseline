---
name: honor-execution-directive
description: "When the user says \"execute in this session\" (or similar), don't re-ask mid-stream whether to continue or hand off — that decision is made."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 87e1bba7-8413-4954-838d-6f3956e3c8d6
---

When the user gives an execution directive ("execute here in this session", "do it now", "go"), treat the decision as final. Do NOT pause partway through to re-offer alternatives you already presented (e.g., "continue here or hand off to a fresh session?") just because the work got deeper or context filled up.

**Why:** In the plan-purring-floyd session, the user picked "Execute here in this session" for a Batch A plan. Midway through Item 1 I paused again to re-offer the handoff option after surfacing a mid-task finding. The user pushed back firmly: *"If I say execute In this session, I MEAN IT. don't question it halfway through."* Re-litigating a settled decision wastes their time and reads as not listening.

**How to apply:** After an execution directive, surface findings and make reasonable calls inline — keep moving. State what you found and what you decided, don't ask permission to keep going.

**Boundary (does NOT suppress this):** genuine safety gates still warrant a pause — a `/regression_gate` failure, a destructive/irreversible action, or a materially changed premise. The user did NOT object to the later regression-gate pause; they objected to re-asking about an already-made *workflow* choice. The rule is "don't re-decide what's decided," not "never pause." See [[feedback_recommended_fix_means_implement]] for the sibling default-to-action preference.
