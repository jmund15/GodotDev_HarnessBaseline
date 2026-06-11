---
name: Resolve plan questions during planning, don't defer to execution
description: When drafting a plan that will be handed off (especially to a lower-effort executor in a fresh session), every resolvable question MUST be answered in the plan body. Deferring decisions to execution defeats the plan's purpose
type: feedback
originSessionId: e9092f23-933b-4528-b9e3-ec8a6b452b62
---
When drafting a plan that will be handed off — especially to a lower-effort executor in a fresh session — every resolvable question MUST be answered in the plan body. Deferring decisions to execution defeats the plan's purpose.

**Why:** A plan is a handoff seam to a fresh session (per `process_rule_plan_high_execute_lower.md`). The executor lacks conversational context — they cannot re-derive why an ASK was open, what options were considered, or what the user's preference would be. Every deferred decision becomes either (a) a stall waiting for user input, (b) a wrong guess that ships, or (c) a senior-model re-spawn that wastes the cost-tier savings the handoff was designed to capture. A plan with open questions is not a plan; it is a partial design that pushes the missing work onto whoever runs it.

**How to apply:**
- **At planning time**, litmus: "could the executor act on this without asking?" If no, the plan is incomplete — resolve it now via grep, file read, or one focused AskUserQuestion to the user, then commit the resolution to the plan body.
- **Reserve AskUserQuestion at planning time** for genuine value/preference questions where the codebase is silent — design priorities, scope decisions, naming taste, trade-offs the user owns.
- **AskUserQuestion at execution time** is for items only the user can answer in real-time (genuine value judgments, scope shifts surfacing mid-execution). Never for items whose answer is a 30-second file read.
- When in doubt, verify first and present as a recommendation in the plan with rationale (user can still override during plan review). The recommendation in the plan is worth more than an unresolved ASK during execution.

**Concrete:** 2026-05-17 plan-mode revision pass on `using-this-info-please-sorted-salamander.md`. Asked user to choose between 3 `BoundEncounter` shape options (typed record vs tuple vs plain class) via AskUserQuestion when the answer was 4 tool calls away in `Jmodot/Implementation/Combat/StatusEffectComponent.cs`. User: "read now and determine the best course of action."
