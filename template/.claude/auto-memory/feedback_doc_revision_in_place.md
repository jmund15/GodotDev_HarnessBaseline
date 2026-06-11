---
name: Doc revision discipline — rewrite in place, don't append revision footers
description: When correcting a saved doc, rewrite affected sections in the main body. The reasoning trail goes in a Revision History footer at the END — never as a "v1.1 addendum" buried after the wrong recommendation. Top-down readers internalize the headline; buried corrections don't save them.
type: feedback
originSessionId: 2026-04-30
---
When correcting a saved doc (design doc, plan, retrospective, README, etc.), rewrite the affected sections in the main body. Do NOT use append-mode to add a "v1.1 revision" footer that leaves the wrong recommendation at the top of the doc.

**Why:** top-down readers (future sessions, agents, the user in 6 months) internalize the doc's headline content. If the headline still contains the wrong recommendation and the correction is buried 8 sections later, the addendum doesn't save them — they'll cite the original wrong recommendation in downstream work.

**Where the reasoning trail goes:** a Revision History footer at the END captures what changed and why in a single audit-trail table. Per `feedback_no_unilateral_condensation`, preserve the trail — but in the footer, not by leaving wrong content in the main body.

**Tool choice (context-cost):**
- 1-3 section corrections: prefer `Edit` on the vault path. Diff-only context cost; ~40-70% savings vs full overwrite.
- 4-5 section corrections: judgment call (break-even between Edit-call overhead and overwrite context cost).
- Structural rewrite (6+ sections, new ordering, frontmatter changes): `Write` or Obsidian MCP `wholeFile/overwrite`. Edit-call orchestration exceeds context savings.

**Status flip:** when applying a substantive revision to a versioned doc, bump frontmatter `status: <state>-vN.M` so fresh sessions know "this is the corrected version, not an in-progress draft."

**Concrete:** 2026-04-30 brainstorm doc `BrainstormingDesigns/2026-04-30-core-elemental-spells-overview.md` shipped with appended v1.1 section keeping wrong ElementProfile recommendation at top of §6.1 until user pushed back. The "v1.1" footer didn't fix the doc; it just documented that the doc remained broken.
