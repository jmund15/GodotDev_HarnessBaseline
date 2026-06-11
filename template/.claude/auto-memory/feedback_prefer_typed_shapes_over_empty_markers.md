---
name: prefer-typed-shapes-over-empty-markers
description: User dislikes empty marker interfaces; prefer concrete typed shapes (discrete fields / typed records) that carry real data or contract
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 13d8bbd6-4d78-4b30-8bf5-595dad96f2df
---

When proposing a type to thread heterogeneous data through a boundary, prefer **concrete typed shapes** — discrete typed nullable fields, typed records, or an interface carrying a real member — over an **empty marker interface** used only for pattern-matching.

**Why:** The user stated (2026-05-28, Transition Payload Typing brainstorm) "usually not a fan of the 'marker' pattern (doesn't actually carry any functionality/contract/data, it's just for pattern matching)." The word *usually* signals a standing aesthetic, not a one-off. They reasoned correctly that a marker doesn't even improve the *read* (you pattern-match the concrete type either way), so its only value is a write-site constraint — thin justification for an empty interface. They chose discrete typed fields (`RunStart` + `Outcome` on `GameStateChangeContext`) over the audit's recommended `ITransitionPayload` marker.

**How to apply:** In `/architecture_brainstorm` and design proposals, when the design space includes a marker interface, present it honestly but lead with / recommend a concrete-typed alternative if one exists. Don't default to a marker just because an audit or convention suggests it. Legit marker case still exists (the C# Framework-Design-Guidelines exception: marker as a *field type* for compile-time write-site checking when no shared behavior exists and single-inheritance rules out a base class) — name that constraint explicitly if recommending one anyway. Weigh "transition-agnostic / decoupled core" as a *framework* virtue that is often overweighted for game-specific (non-Jmodot) classes. Related: [[feedback_inspect_existing_abstractions_first]], [[arch_rule_closed_set_switch]].
