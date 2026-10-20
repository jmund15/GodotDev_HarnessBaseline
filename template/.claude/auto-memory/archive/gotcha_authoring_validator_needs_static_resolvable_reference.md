---
name: gotcha_authoring_validator_needs_static_resolvable_reference
description: "An authoring-time validator/analyzer can only lint statically-resolvable references; a reference/selector type resolved against a runtime context has no authoring-time enumeration seam."
metadata:
  node_type: memory
  type: gotcha
---

An authoring-time validator/analyzer (runs in-editor, no scene tree, no placement) can only inspect data that is **statically resolvable** from the Resource graph. A reference/selector type whose resolution API takes a **runtime context** (a placement context, a build context, a live blackboard) has **no authoring-time enumeration seam** — you cannot ask it "what could you resolve to" without the runtime context it doesn't have at authoring time.

**Why:** two similarly-named reference types can sit in different layers with opposite resolution models. Example: an outer-layer `Selector` exposes `EnumerateCandidates()` → authoring-enumerable; an inner-layer `TargetSelector` exposes only `Resolve(placement)` / `ResolveAll(placement)` / `MatchesCandidate(candidate, placement)` → **runtime-only**. A plan that assumes "enumerate the reference's candidates and check a property" silently assumes the enumerable one; if the real field is the runtime-resolved sibling, the lint's mechanism doesn't exist. This is a **halt valve (a)** class (plan-fact mismatch), not something to improvise around.

**How to apply:** before planning a lint/validator that reads "what a reference points at," open the reference's **actual declared type** and confirm it has a context-free read (an `[Export]` you can inspect, or an `EnumerateCandidates()`-style seam). If resolution needs a runtime context, the lint is only feasible for **statically-named subclasses** — e.g. `TargetByInstanceId` exposes `[Export] StringName InstanceId`, so an outer-level check can resolve it by looking that id up among the container's own children; dynamic subclasses (by-capability, sibling, any-of) stay authoring-opaque and must be skipped, not guessed. Related: [[feedback_verify_plan_integration_target_is_live]], [[feedback_verify_explore_agent_empirical_claims]], [[arch_rule_validate_strategy_selector_by_enumerating_candidates]].

**Evidence:** a dead-source-activation lint planned `Trigger.Source.EnumerateCandidates()`; `Source` was the runtime-resolved selector, so the mechanism was absent. Reshaped to a container-level `TargetByInstanceId` resolution (read the literal id, find that child in the container, flag the persistence mismatch); dynamic target selectors skipped.
