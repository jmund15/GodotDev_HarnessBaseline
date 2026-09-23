---
name: gotcha-design-doc-cross-section-prescriptions
description: "Part briefs must grep the WHOLE design doc for the Part's name/streams — prescriptions for a Part can live in sibling sections and section-scoped reads miss them"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 5ab89c41-ab74-4ddc-bb14-6bbc29dfed42
  modified: 2026-08-04T05:38:36.207Z
---

A design doc's prescriptions for a Part are NOT confined to the Part's home section. Sibling sections routinely carry corrections that name the Part directly (e.g. a determinism section prescribing "the same correction applies to Part X's pins" while X's own section says nothing).

**Why:** A Part pipeline that scopes every read to the Part's home section (survey brief, draft brief, plan_check context) ships defects the doc itself already warned against. Observed: a test-shape correction ("distribution assertion, not two-seed inequality — the naive pin is flaky by construction") lived two sections away from the Part's design surface, named the Part explicitly, and survived a survey + draft + 4 plan_check lenses + close-out review + fix pass — it surfaced only during the post-ship arch-amendment read.

**How to apply:** When authoring a Part's survey/draft brief, add one grep sweep of the ENTIRE design doc for the Part's name, its key new symbols, and its stream/config keys — and inject any hits outside the home section into the brief as mandatory reading. Cheap (one Grep), catches the cross-section class the section-bounded verbatim read structurally cannot.

Related: [[feedback-brief-loads-full-roadmap-trigger]]
