---
name: feedback-orchestrate-loads-orchestration-skill
description: "Load Skill(orchestration) before the first dispatch; a denial does not change §0's mechanism verdict, and a Workflow within the size guideline is pre-authorized, never downgraded to Agent."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: f387bceb-e25c-4a11-a4a7-24e93bc04e45
  modified: 2026-10-12T15:26:47.509Z
retire_when:
  - review-by: 2027-02-27
---

When the user says "orchestrate" or asks to dispatch execution slices, load `Skill(orchestration)` BEFORE the first dispatch — its §0 owns the dispatch-mechanism decision (single Agent vs Workflow vs sidecar) and the fan-out litmus. A known-up-front multi-slice drive is a Workflow, never a bare Agent.

**Why:** 2026-08-14 a bare-Agent dispatch of a 6-slice drive skipped §0's litmus; the SessionStart rail naming the skill was passive text with no enforcement at the dispatch moment. The user called it out ("led to the well to drink and yet somehow not drink"). The rail mention was then removed as bloat in favor of a mechanical guard.

**How to apply:** load the skill (and read the ladder in full, or its `ladder-slice` for every tier you pin, per `orchestration` §5) before the first dispatch after a session start or compaction. `model_ladder_gate.doctrine_check`, called by `dispatch_mechanism_guard.py` (Workflow) and `sidecar_dispatch_context.py` (sidecar launches), denies until both are in context and names each retry. History: the deny went advisory in `affad2479`/`61914c6e5` (2026-10-07/08, a sweep that filed "is the doctrine loaded" as a judgment); sessions then dispatched without it (2026-10-09 lone Haiku seats, 2026-10-11 skill-less Workflow); the owner restored it 2026-10-11 as a last resort behind the existing instructions (instruction_quality §3). A denial does not reclassify the work: load the skill and re-issue §0's mechanism. If §0 selects Workflow, dispatch it: §0's standing authorization covers any fan-out within the session's size guideline, so only a larger one asks. Never downgrade enumerable jobs to a direct Agent. 2026-09-22: de-bloat `a0bd0df01` had deleted that authorization, so an audit fan-out stalled on an opt-in question and was first mis-routed to Agent. One coherent plan-slice with a TDD cycle → single Agent; needs session context → `fork`. See [[feedback_enforce_not_promise]] for the enforcement-beats-promise doctrine this implements.

**Verified:** 2026-10-11 — the restored deny fires on a fresh session id and allows this session's real state (both loaded).
