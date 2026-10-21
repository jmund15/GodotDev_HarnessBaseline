---
name: feedback_harness_rules_are_agent_actionable
description: "Harness instruction files are executed by the model, not read by the user — every rule proposed for one must map to a runtime agent decision, not a user habit."
metadata: 
  node_type: memory
  type: feedback
---

Harness files (`CLAUDE.md`, `skills/*/SKILL.md`, `commands/*.md`, hooks) are executed by the model, not read by the user. Every rule proposed for one must map to a decision the agent makes at runtime — a tool call, a dispatch choice, a routing branch.

**Why:** cache findings were first proposed as CLAUDE.md rules phrased as user habits ("compact before stepping away") — chat advice wearing harness clothes, with no agent decision point.

**How to apply:** name the tool call or dispatch the rule changes before proposing it. Can't name one → not a harness rule. The same finding usually has an agent-facing form; translate rather than discard.

Related: [[feedback_prescribe_verification_not_cognition]], [[feedback_orchestration_skill_model_agnostic]]
