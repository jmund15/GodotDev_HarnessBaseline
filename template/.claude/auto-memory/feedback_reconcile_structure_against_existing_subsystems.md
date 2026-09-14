---
name: feedback_reconcile_structure_against_existing_subsystems
description: Map folder changes onto the current subsystem registry before adding parallel roots.
metadata:
  type: feedback
---

Before restructuring folders, map every concern to the existing `project_subsystems` registry and the
relevant roadmap or design source. Create a top-level folder only when no current owner fits, then add
its registry row in the same change.

**Why:** Parallel roots duplicate ownership and drift from the planned architecture.

**How to apply:** Read the registry and live roadmap before drafting the target tree. Record each
concern's current owner, proposed owner, and reason. This is the folder-taxonomy sibling of
[[feedback_inspect_existing_abstractions_first]], which owns type families.
