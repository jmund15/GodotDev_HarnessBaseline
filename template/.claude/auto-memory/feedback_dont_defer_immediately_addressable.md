---
name: don-t-defer-immediately-addressable-work-to-the-worklog
description: "Evidence behind worklog_reference's Do-now gate: surfaced work is done now, inline or dispatched; the worklog holds only items with a named defer reason."
metadata:
  node_type: memory
  type: feedback
  originSessionId: 84adc6c1-1ab3-4b4c-ade9-5bed49091669
  modified: 2026-09-23T16:00:25.850Z
---

The rule lives in `skills/worklog_reference/SKILL.md` §Do-now gate: relevant work surfaced mid-session is done now, inline or dispatched through `orchestration`, and a deferral names one of five reasons (owner judgment, external block, design first, owner deferral, collision). This file keeps the evidence.

**Why:** a logged item feels like progress and is not. The propose → confirm → write vault → write mirror → read next session → eventually do loop costs more than doing the work, and spurious adds clog the ready pool. With background dispatch, "it needs an agent" or "it spans files" is not a reason to wait.

**Rulings:**
- 2026-07-28: scope ≤2 in session context → fix now; "it needs an agent" is not a defer reason.
- 2026-09-23: the owner found agents still too hesitant given the dispatch system, and set do-now as the default for all relevant, applicable work, with deferral only for a named reason. The skill's old gate had drifted stricter than the 2026-07-28 ruling: it fired only on scope-1 mechanical wording, asked "do now (y) or log (a)?", and made logging the default route. This file's old "worklog is appropriate for >1 file / >30 min" list contradicted orchestration.

**Invisible deferral is the same failure.** A defect named in a report and left without a disposition is deferred with nothing tracking it.
- 2026-08-20: a live test-pollution bug was diagnosed, written up as "worth investigating", and left.
- 2026-09-23: a memory admission guard for launches was written into a design doc as "tracked apart" with no worklog item or dispatch.
