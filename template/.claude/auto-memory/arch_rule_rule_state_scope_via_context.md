---
name: arch-rule-rule-state-scope-via-context
description: "Shared rule Resources take mutable state through their evaluation context, never statics — the caller picks the scope."
metadata: 
  node_type: memory
  type: project
  originSessionId: 66931000-c20f-4f54-b483-af3fa3e136e8
---

A shared rule Resource (DispositionRule, TransitionCondition, strategy .tres) must receive mutable runtime state via its evaluation CONTEXT parameter, never a static singleton. A static bakes ONE scope into the rule forever and demands scripted cleanup (Clear() calls in lifecycle code = the smell); context-threading makes scope a caller decision and ties state lifetime to its owner.

**Why:** `ProvokedHostileRule` read static `ProvocationLedger.Current` → grudges were run-global when the design wanted pod-local, and GameLifecycleManager had to manually clear it twice. Threading `ctx.Provocations` fixed scope without touching the rule again.

**How to apply:** state goes on a scope-owner object (e.g. `Squad`, injected at spawn under `BBNPCSig.Squad`); the per-call context carries a nullable reference; null = "no scope" degrades gracefully. Related: [[arch_rule_transition_condition_stateless]], [[arch_rule_resource_config_runtime_split]]. Seam doc: `Claude/Architecture/faction-squad-seam.md`.
