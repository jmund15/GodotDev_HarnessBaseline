---
name: feedback_verbatim_content_to_review_agents
description: Send VERBATIM file contents to review/audit subagents — abbreviating code in the CONTEXT makes agents report the abbreviation as defects.
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 31499a7a-c91c-4ab1-975f-14648380d358
---

When dispatching review/audit subagents with a Claude-assembled CONTEXT, send **verbatim** file contents — never abbreviate or summarize code to save args size. Agents treat the CONTEXT as ground truth and report the *abbreviation itself* as defects (false positives, often flagged "critical").

**How to apply:** if args size is a concern, use the workflow's shared `contextPrefix` (passed once, not N× per agent) instead of trimming content; or pass real file paths and let agents read them (accepting the [[gotcha_workflow_fanout_search_false_absence]] risk). Either way, do not hand a digest of code you want audited. When the verbatim CONTEXT is too large even for a `contextPrefix` arg (JSON-fidelity hazard), concatenate the files into a few bundled scratch files (each < ~2000 lines) and have agents `Read` those — fewer reads than scattered original paths and content is guaranteed present, sidestepping both the arg-size and false-absence hazards.

**Why:** the audit can only be as faithful as its input — a summarized input yields findings about the summary, wasting a verification cycle.

**Verified (observed):** a session_audit produced 2 false "critical" `.tres` findings traced entirely to 3-of-4 sub-resources being shortened in the CONTEXT; the live file was complete and the green Logic test already proved it. Companion: [[feedback_verify_explore_agent_empirical_claims]].
