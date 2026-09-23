---
name: briefing-context-gates-not-flow
description: "When a command/skill briefs a downstream context (a drafting step, another agent, a follow-on skill), provide CONTEXT + GATES only — never the receiver's internal flow steps. Receiver already knows how to do its job."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: d72ceee1-1360-43a4-861a-e1c6aeae48c9
  modified: 2026-08-05T17:41:39.257Z
---

When designing a command or skill that hands off to a downstream context (a drafting step, a Task subagent, the next skill in a chain), the briefing surface should contain ONLY:

1. **Context** — verbatim source-of-truth content the receiver needs (design surface, codebase verification, drift findings, dep state, out-of-scope declarations).
2. **Gates** — hard-stop conditions and contracts the receiver must respect (file-list-bounded, "don't redesign", scope boundaries, kick-back triggers).

NOT in the briefing:

- **Internal flow prescription** — sequencing steps, verification-command authoring guidance, "do X then Y", per-step recipes. The receiver picks its own internal flow.

**Why:** First draft of the project's Part-briefing command (briefing for Plan Mode) included a "Plan Session Scope" section with five bullets — three were gates/context (file-list-bounded gate, micro-drift to resolve, don't-redesign contract) and two were flow prescription (sequence the work, author verification commands). The flow bullets duplicated what Plan Mode already does natively and pushed past the user's stated framing ("give it all necessary context and initial workflow instructions and gates to respect, before it goes ahead with its main Plan Mode flow"). User flagged the overshoot; tightening required removing the flow bullets and renaming the section "Gates & Contract" to make the discipline explicit.

**How to apply:** When drafting any briefing/handoff surface (slash command that emits a packet, skill that hands off, agent template that briefs a sub-agent), apply the **gates-vs-flow litmus** per bullet:

- *"Could the receiver figure this out themselves with their existing knowledge?"* → flow prescription; REMOVE.
- *"Does the receiver need this fact / contract to operate correctly?"* → context/gate; KEEP.

Section naming reinforces the discipline: prefer "Gates & Contract" / "Context" / "Constraints" over "Steps" / "Scope" / "Plan" (the latter invite flow content).

**Generalizes beyond the drafting step** — same rule applies to: Task subagent prompts (give them the question + constraints, not the methodology), skill-to-skill handoffs (the design-brainstorm skill → the roadmap-update command passes the Parts list + spawn-placement decision, not the validator sequence), the plan-review command → orchestrator action protocol (gives the agents their CONTEXT block, not how to write findings).

Related: [[feedback_no_unilateral_condensation]] (don't summarize context the receiver needs verbatim — sibling concern, "what's IN the context" vs this rule's "what shape the briefing takes").
