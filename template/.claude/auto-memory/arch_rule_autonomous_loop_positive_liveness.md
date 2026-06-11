---
name: autonomous-loop-positive-liveness
description: "An autonomous convergence/gate loop must require a POSITIVE liveness signal from each fanned agent/gate before treating \"no findings\" as success — absence is not a pass."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 328355ad-d25a-4be6-b511-a11b6412b744
---

An autonomous loop that consumes fan-out results — a convergence loop, a gate, an adversarial panel — must NEVER read an empty/absent result as success. A fanned agent that errors, times out, or false-absences returns the SAME shape as a genuine clean: "0 findings" is indistinguishable from "never ran" ([[gotcha_workflow_fanout_search_false_absence]]).

**Why:** the loop's terminal/advance condition is usually "no critical findings remain." A silent panel failure trivially satisfies that, so the loop converges (or a gate passes) on a fabricated clean and auto-advances to the human gate — or commits — over analysis that never executed. The empty-reads-as-success path is the single most common robustness hole in autonomous-orchestration commands.

**How to apply:** make the clean a POSITIVE assertion, not the absence of findings. Require each agent/lens to echo *what it examined* (refs/hits count, abstractions checked) and/or check the workflow's `perAgent` liveness counts; if any lens is missing → HALT ("panel incomplete — N/M returned; cannot certify"), never CLEAN. This is the consumer-side companion to [[gotcha_workflow_fanout_search_false_absence]] (which is the producer-side observation). Evidence: the `--auto` liveness valve in `architecture_brainstorm_redteam`, and valve (d) in both `plan_drive` and `part_execute`.
