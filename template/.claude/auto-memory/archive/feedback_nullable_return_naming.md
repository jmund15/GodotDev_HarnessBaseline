---
name: nullable-return-naming
description: "Method names communicate the contract — `Find*` / `TryGet*` for nullable returns, `Get*` for guaranteed-non-null."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 3458cc73-6e07-455f-8d44-8d1251b9edee
---

When authoring extension methods or helpers whose return type is nullable, prefer `Find*` prefix (or the `Try*` + out-param convention) over `Get*`. `Get*` reads as "guaranteed resolution"; a caller sees `bb.GetGraph()` and is statistically more likely to skip the null check than one seeing `bb.FindGraph()` / `bb.FindParentGraph()`.

**Why:** Naming is the cheapest enforcement of nullable contract. The compiler will warn on `?` returns, but humans skim past warnings. Method-name discipline catches the bug at the read site.

**How to apply:** When adding a helper that returns `T?`, default to `Find*` prefix. Reserve `Get*` for methods whose nullability is non-existent or wrapped in a throw-on-miss contract. Existing `Node.GetGraph()` is borderline (returns nullable but reads like a getter); future helpers should not propagate the pattern.

**Concrete:** `NodeExts.FindParentGraph(this IBlackboard)` added 2026-05-18 in [[arch-rule-pragmatic-narrowing-at-boundary]] session, named per this convention after user pushback on initial `GetGraph(this IBlackboard)` proposal.
