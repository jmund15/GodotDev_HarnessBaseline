---
allowed-tools: Read
description: Begin execution of an approved plan in this (fresh executor) session. Takes plan file path; loads plan, sets executor stance, begins implementation. Invoke as the first command in a fresh executor session — not in the planning session.
---

## Usage

`/plan_handoff <plan-file-path>` — invoke **in the executor session** (typically a fresh lower-effort session), not in the planning session.

## Procedure

1. Read `$ARGUMENTS`. If the path is missing, empty, or the Read fails, abort with:

   > `Plan file not found or unreadable: <path>.`

2. Adopt the following execution stance for the rest of this session:

   - The plan file is the authority. Follow it exactly where unambiguous.
   - Surface to the user (do not improvise) when you hit any of:
     - a referenced file/type/symbol doesn't exist as the plan describes
     - a decision not covered by the plan's Decision record
     - an unexpected tooling failure (test infra, build error)
     - an integration step produces results the plan didn't anticipate
   - CLAUDE.md still applies. Run /regression_gate before any .cs commit.
   - Verify load-bearing empirical claims (file paths, type existence, prior-art assertions) before acting on them per feedback_verify_explore_agent_empirical_claims.
   - Mechanical execution is yours to make — don't ask permission for unambiguous steps.
   - Do not read prior session transcripts or memory snapshots; the plan file is self-sufficient by design.

3. Briefly confirm to the user:

   > `Plan loaded from <PLAN_PATH>. First step: <first step from plan>. Beginning execution.`

4. Begin execution of the plan's first step.
