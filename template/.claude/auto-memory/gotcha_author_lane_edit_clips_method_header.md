---
name: gotcha_author_lane_edit_clips_method_header
description: "Authoring-only delegate lanes (luna at low) repeatedly replace a method's signature line with a new method and leave the old body orphaned — a compile error the lane never sees because it cannot build; build once before reviewing any lane's diff."
metadata: 
  node_type: memory
  type: feedback
  modified: 2026-08-22T06:43:12.417Z
---

Measured 2026-08-22, three instances in one drive (two in L1's test edits, one `using` drop): an
`Edit` whose `old_string` ended at a method signature inserted the new `[TestCase]` method and left
`{ … }` of the displaced method dangling. A second lane deleted a `[sub_resource]` still referenced
by an unrelated node (`Steering._considerations`) — a scene parse failure with the same shape.

**Why:** an authoring-only lane (no build, no test run, per the single-flight concurrency rule)
has no signal for either class; its report says "done" in good faith.

**How to apply:** the orchestrator's first act after lanes land is `dotnet build` + a text lint of
every edited `.tscn`/`.tres` (dangling `SubResource`/`ExtResource` refs) BEFORE reading any diff —
the fixes are one-line and cheap, the review without them is wasted. Give a lane that must edit a
scene the rule "never delete a sub_resource without grepping its id first". Related:
[[gotcha_workflow_single_flight_concurrency]].
