---
name: arch-rule-rule-state-scope-via-context
description: Shared rule Resources take mutable state through evaluation context, never statics; the caller owns scope.
metadata:
  type: project
---

A shared rule Resource receives mutable runtime state through its evaluation context, never a static
singleton. A static fixes one scope for every caller and creates manual lifecycle cleanup.

Put state on the object that owns its lifetime. Pass that object, or a nullable reference to it, in the
per-call context. Null means no active scope.

**Why:** Context-threading lets each caller choose run, group, entity, or request scope without changing
the reusable rule.

**How to apply:** Treat repeated `Clear()` calls on static rule state as a scope bug. Move the state to
its owner and thread it through evaluation. Related: [[arch_rule_transition_condition_stateless]] and
[[arch_rule_resource_config_runtime_split]].
