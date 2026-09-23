---
name: feedback-single-gate-no-secondary-approval
description: "An upstream gate's answers ARE the approval — never stack a secondary approve/confirm prompt on a decision the user already made (design-lock batch → roadmap authoring is one approval, not three)."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 8ee50aeb-8ae0-480e-927e-752ec12adf64
---

When a flow's human gate is a question batch (e.g. /design_drive's taste-fork batch), the answered batch IS the approval. Do not follow it with a separate "approve the design?" question, and do not re-gate mechanical downstream consequences (e.g. /update_roadmap's batch diff) that the gate's option text already named.

**Why:** Stacked sequential approvals on one decision read as noise and process theater — the user corrected this live at the Frozen/Ice-Block design-lock (three prompts where one sufficed).

**How to apply:** One decision → one gate. Downstream steps that are direct consequences of the gated decision inherit its approval; present their output informationally and let the user redirect after the fact. Ask a follow-up ONLY when reconciliation introduces content that is not a direct consequence of the answers, or when answers conflict. Encoded in [[design_drive]] ("The answered batch IS the approval"), architecture_brainstorm_redteam --auto ("Batch-first, never deferred"), and update_roadmap ("Gate inheritance"). Related: [[feedback-honor-execution-directive]].
