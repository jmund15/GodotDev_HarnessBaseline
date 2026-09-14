---
name: verify-plan-integration-target-is-live
description: Trace a plan's named wiring target to a live consumer before editing it.
metadata:
  type: feedback
---

Verify that each scene, Resource, registry entry, or other integration target named by a plan is still
used before wiring new work into it.

**Why:** A branch can rename, move, or replace the target after the plan was written. Wiring an orphan
may build and import cleanly while no runtime path consumes it.

**How to apply:** Trace the target through its live consumer, such as the scene tree, autoload list,
registry, selector, or parent Resource. If it is stale, update the plan to the verified live seam rather
than silently substituting a similarly named file.
