---
name: feedback-orchestrate-loads-orchestration-skill
description: "Load Skill(orchestration) before the first dispatch; a denial does not change §0's mechanism verdict, and absent Workflow approval means ask rather than downgrade to Agent."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: f387bceb-e25c-4a11-a4a7-24e93bc04e45
  modified: 2026-09-12T17:06:50.257Z
retire_when:
  - review-by: 2027-02-27
---

When the user says "orchestrate" or asks to dispatch execution slices, load `Skill(orchestration)` BEFORE the first dispatch — its §0 owns the dispatch-mechanism decision (single Agent vs Workflow vs sidecar) and the fan-out litmus. A known-up-front multi-slice drive is a Workflow, never a bare Agent.

**Why:** 2026-08-14 a bare-Agent dispatch of a 6-slice drive skipped §0's litmus; the SessionStart rail naming the skill was passive text with no enforcement at the dispatch moment. The user called it out ("led to the well to drink and yet somehow not drink"). The rail mention was then removed as bloat in favor of a mechanical guard.

**How to apply:** the `dispatch_mechanism_guard.py` PreToolUse hook (Workflow|Agent) enforces the load — it denies a first dispatch when the skill's §0 markers aren't in the session transcript, fail-open on any I/O error. A denial does not reclassify the work: load the skill and re-issue §0's mechanism. If §0 selects Workflow and user approval is absent, ask; never downgrade enumerable jobs to a direct Agent. One coherent plan-slice with a TDD cycle → single Agent; needs session context → `fork`. See [[feedback_enforce_not_promise]] for the enforcement-beats-promise doctrine this implements.

**Verified:** 2026-09-04 memory-claim audit — `hooks/dispatch_mechanism_guard.py:54,60` fail open on empty/unreadable transcript, `:157` denies when the §0 marker is absent; registered PreToolUse `Workflow|Agent`.
