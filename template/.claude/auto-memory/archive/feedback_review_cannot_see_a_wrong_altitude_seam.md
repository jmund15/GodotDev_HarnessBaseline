---
name: feedback_review_cannot_see_a_wrong_altitude_seam
description: "Every design lens asks whether a seam is correct; none asks whether it was cut at the right level. A well-built seam at the wrong altitude passes every check that shares its framing, so the altitude question has to be asked deliberately."
metadata: 
  node_type: memory
  type: feedback
  modified: 2026-08-19T06:12:12.910Z
---

A destination-strategy seam went through a 29-finding `/plan_check`, two convergence rounds, and a
full TDD implementation. Every lens asked *"is this strategy correct, complete, well-named, tested?"*
None asked *"is **destination** the right thing to strategize over?"* It wasn't: many jumps are natively
angle + force, and forcing them through a destination made a simple recoil unexpressible. The user
caught it in one sentence after the code had shipped.

**Why:** a review inherits the plan's framing. Once the plan says "the seam varies X", every downstream
check evaluates the X-seam — thoroughness makes this *worse*, not better, because a dense finding list
reads as proof the design was examined. Nothing in a correctness lens can surface "you are varying the
wrong noun", since by that lens the artifact is genuinely fine. The same blindness produced a
a launch-spec type whose two generic constraint slots were mathematically complete and completely
wrong as an authoring surface.

**How to apply:**

- **Before locking any strategy/config seam, name the quantity it varies and ask what the CONSUMER
  natively expresses.** If a consumer has to compute a fake value to satisfy the seam (a destination
  invented for a bounce; a range invented for a fixed hop), the seam is one level too high or too low.
  A fabricated input is the diagnostic.
- **Two orthogonal concerns fused into one seam is the usual shape it takes.** Aim (which way) and arc
  (what shape) were one slot; separating them made both simple. Ask whether the seam's name covers one
  axis or silently two.
- **Read a user's "this feels convoluted" as an ALTITUDE report, not a complexity complaint.** They are
  describing vocabulary that doesn't match how they think about the domain. Answer by naming what the
  system makes them say versus what they want to say — not by defending the implementation, which is
  usually fine and beside the point.
- **A complete, closed, correct engine is not automatically an authoring surface.** When the two are the
  same type, the designer must reason in the engine's internal vocabulary to author validly. Put
  archetypes in front rather than warnings on top: closure by type beats closure by validation.

Related: [[feedback_seam_generic_over_subtype_naming]],
[[feedback_inspect_existing_abstractions_first]], [[feedback_exhaust_review_findings_before_locking]].
