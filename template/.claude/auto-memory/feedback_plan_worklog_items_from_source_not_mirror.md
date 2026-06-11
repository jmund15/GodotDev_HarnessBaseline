---
name: feedback-plan-worklog-items-from-source-not-mirror
description: "When scoping/planning a worklog item, read its full Context block in Worklog.md — never plan off the title-only mirror. The mirror is a discovery index, not a spec."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 90d4ba24-4f8f-4037-aa55-f7b6b19254bb
---

When planning or scoping a worklog-sourced task, read the item's full **Context / Where / Source** sub-bullets in `Worklog.md` (the Obsidian source). Do NOT plan off the title-only mirror (`.claude/worklog-titles.md`) plus your own code exploration.

**Why:** 2026-05-19, Batch B quick-win sweep. Planned three items off mirror titles + code reads, never reading their source Context blocks until execution forced it. Two items were materially misread:
- *"Decide spell-vs-spell stacking semantics"* — assumed from the title to mean pierce/hit-source stacking; ran a whole Socratic decision on that. The actual Context was about **Reaction resolution** (`FindReaction` single-match vs `ResolveAll` stacking). The user answered the wrong question.
- *"Blackboard.NotifySubscribers Warning → Error"* — looked mechanical; its Context cited a **Design §12 commitment** (a documented design conflict) invisible in the title.
Both surfaced *after* plan approval, at execution time. User: *"bro why was this not decided in the plan .... these questions should not arise afterwards."*

**How to apply:** in plan Phase 1 for any worklog item, bounded-read its block in `Worklog.md` (grep the title → read the block). The mirror exists for always-loaded awareness and discovery; the Context/Where/Source lines are the spec. Title + code exploration is not enough — the title can mislead about scope entirely. Related: [[feedback_resolve_questions_in_plan_not_execution]], [[feedback_verify_explore_agent_empirical_claims]].
