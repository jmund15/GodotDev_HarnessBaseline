---
name: feedback_found_defect_is_fixed_now_peer_owned_is_a_git_fact
description: "A defect you find mid-task is fixed in the same turn; \"that file is a peer's\" is a git-status fact to check, never a reason to log the fix for later."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 7e51004a-1b0c-4a30-bc93-82cc001da23b
  modified: 2026-09-09T00:51:23.112Z
retire_when:
  - review-by: 2027-02-01
---

A defect found mid-task is fixed NOW, in the same turn, and a task row is written only when a
VERIFIED fact blocks the fix. "That file is a peer's / under active edit" is a claim about
`git status --short <file>`: run it; a clean file is yours to fix.

**Why:** 2026-09-08 — two defects surfaced in one dispatch (a sidecar refusal that reached the
orchestrator only when the background task "completed" minutes later; a quota band reading a 3%-used
fresh week as Hot). Both were logged as tasks with the reason "peer-owned file". The file was clean.
The owner had to demand the fixes, and named it dodging accountability. The cost of the check is one
command; the cost of the dodge was the owner's trust and a round-trip.

**How to apply:** when a sentence you are about to write contains "logged as task", "follow-up",
"peer-owned", "later": stop, run `git status --short <file>`, and either fix it in this turn or quote
the blocking fact (a dirty peer file, a missing credential, a decision only the owner can make).
Companion rows: CLAUDE.md §Rationalizations to Refuse; hot index "Never defer an immediately
addressable item".
