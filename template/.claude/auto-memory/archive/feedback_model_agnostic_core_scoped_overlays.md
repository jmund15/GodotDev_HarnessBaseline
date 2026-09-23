---
name: feedback_model_agnostic_core_scoped_overlays
description: "Owner's harness mindset: the core stays model-agnostic and optimal for general use; a model- or archetype-specific correction goes only to the models whose evidence shows the failure, so one model's quirk never taxes every model"
metadata:
  node_type: memory
  type: feedback
  originSessionId: fda900c8-fd6a-47da-a5ad-4d19c68e3402
  modified: 2026-09-23T02:19:43.717Z
---

**Rule home:** `rules/harness_authoring.md` *A correction takes the scope of its evidence*; `/codify` Step 2 gates on it.

**Owner's words (2026-09-22):** keep the harness optimal, "remain model agnostic for general use, while also containing model/archetype-specific guidance if needed". The examples were hypothetical: GPT may loop more, Sonnet may need more explicit instructions, Opus may drift from orchestration instructions. "We're not overcorrecting for every model when only one or two specific models have that issue."

**Why:** a universal rule costs every model context bytes, and it can over-constrain a model that never had the problem. The step orders and round caps that `feedback_prescribe_verification_not_cognition` frees are this cost. A per-model channel costs only the model that needs it.

**Diagnosis first:** if the harness text is ambiguous, the defect is the harness's, and the fix is universal. `gotcha_sidecar_lane_routes_report_through_write_doc` is one: a luna failure whose root cause was ambiguous doctrine. Only clear text that one model still breaks is a model defect.

**Channels:**
- A session driver: `driverNotes` on its registry row or its transport's row (`hooks/session_model_rails.py` prints them).
- Rail depth for a session or a delegate: `railTier` (`detailed`, the default, or `condensed` / `minimal`, which need `railTierEvidence` per the rule home; the project's rail battery measures it) on the row (`hooks/_model_tier.py`, and `args.__rails` injected into the fan-out engines).
- Delegate selection: the ladder `±` cell.
Every note carries an `evidenceRef`, and `model_registry.py --check` rejects one without it.

**Still open:** sidecar delegates always read `detailed`, because no sidecar row has earned a `railTier`. Workflow scripts outside the four fan-out engines still pin model names. The 2026-09-22 audit (F1–F10) produced this list.

Related: [[feedback_one_observation_licenses_a_hypothesis_never_a_scope_decision]], [[feedback_orchestration_skill_model_agnostic]], [[gotcha-long-context-gpt-sessions-cost-by-context-size]].
