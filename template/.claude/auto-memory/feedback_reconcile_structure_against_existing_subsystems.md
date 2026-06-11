---
name: feedback_reconcile_structure_against_existing_subsystems
description: "When restructuring folders/subsystems, map concerns onto existing subsystems + roadmaps before inventing parallel folders."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 1a3a7c1e-484f-4be0-8144-de54a37c43b9
---

When restructuring or renaming folders/subsystems, first map each concern onto the **existing** `project_subsystems` registry + the topic-folder roadmaps/brainstorms. A concern usually belongs to a subsystem that already exists, not a new parallel folder. Create a new top-level folder only when no existing home fits (then register it per `structure_rules` R12).

**Why:** inventing parallel structures duplicates subsystems and drifts from the planned architecture; the existing registry + roadmaps already encode where things belong.

**How to apply:** at restructure-planning time, read the subsystem registry + the relevant topic roadmap/brainstorm *before* proposing the target folder layout. Distinct from [[feedback_inspect_existing_abstractions_first]] — that is the type/subclass axis; this is the folder/subsystem-taxonomy axis.

**Signal:** PvE-folder reconciliation (2026-05-27) — proposed parallel new `Crafting/`+`Waves/`+`Arena/` folders; user redirected: "look at the existing systems and subsystems … waves would probably be in its own encounters subsystem … if you look at the roadmaps and brainstorms." Outcome: waves → existing `Dungeon/Encounters/Combat/`, arena → `Prototype/` (test-floor concept from the roadmap), camera → existing `Camera/`.
