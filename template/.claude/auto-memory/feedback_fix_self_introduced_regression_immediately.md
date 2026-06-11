---
name: fix-self-introduced-regression-immediately
description: "If a fix/addition CONSCIOUSLY introduces a regression (perf, behavior, coverage), resolve it in-session — never park it."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 87e1bba7-8413-4954-838d-6f3956e3c8d6
---

When you knowingly introduce a regression as a side-effect of a change — and you're aware of it at the time — fix it immediately in the same session. Do not log it to the worklog as a follow-up, do not defer it to "later if profiling shows it matters."

**Why:** In the plan-purring-floyd session, extracting `TrailGridMeshHelper` from `TrailMeshRenderer` introduced nested lambdas in `RebuildGridMesh` that allocated ~2N closures per topology rebuild (the pre-extraction code had zero). I flagged it and offered to park it as a profiling-gated Future Scope item. The user rejected parking: *"if a worklog item fix (or ANY fix/addition) ever CONSCIOUSLY adds a regression, that should be addressed and resolved IMMEDIATELY."* The fix was small (cache the delegates at `Initialize`) and shipped same-session as `2f32420e`.

**How to apply:** The trigger is *conscious awareness at authoring time*. If while making a change you notice "this makes X worse," that's a blocker for the change being done — close it before moving on, not a deferral candidate. Distinct from pre-existing issues you merely discover (those can be logged). The litmus: *"Did MY change cause this, and did I know?"* Yes → fix now. Applies to any regression class: perf/allocation, behavior, test coverage, API ergonomics.

**Relationship to deferral rules:** this is stricter than the general "don't defer immediately-addressable work" ([[feedback_dont_defer_immediately_addressable]]) — that's about scope-1 convenience; this is a hard rule about not shipping known self-inflicted regressions regardless of fix size.
