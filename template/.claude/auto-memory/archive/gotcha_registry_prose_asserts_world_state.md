---
name: gotcha_registry_prose_asserts_world_state
description: "A registry comment asserting what has NOT happened goes stale silently and reads as a live reason — a later session built a whole commit on one, four days after the benchmark it denied had run"
metadata:
  node_type: memory
  type: reference
  originSessionId: 692c41fb-b5c7-45db-a3bb-3220ff2ee4c2
  modified: 2026-09-19T19:25:46.879Z
---

# Registry prose asserting world state

`.claude/reference/external_models.json`'s `flash` row carried
`_evidenceComment: "No V4.1 cell has run"` and a `_comment` saying roles stay empty "until V4.1 cells
are scored". Campaign `deepseek-v41-flash-2026-09-15` scored **five** V4.1 cells on 2026-09-15 and
published `SCORES-INDEX.md`. The row was untouched, unroutable, and asserting the opposite of the
board for four days.

The cost was not just staleness. On 2026-09-19 a session read that comment, believed it, and wrote a
commit whose whole premise was "no V4.1 evidence exists, so inherit the retired version's" — then
had to amend it twice once the board was found. The plan for that campaign had *named the registry
row as its own close-out target*, and a worklog item carrying the wave-1 scores was sitting in
`## Active` the entire time.

**Why it is invisible:** a negative world-state claim cannot be falsified from inside the file it
lives in. It has no date, no pointer to the artifact that would overturn it, and nothing runs when
the world moves. It also reads with more authority than a positive would — "no cell has run" sounds
like a checked fact, where "measured at X" invites a date check.

**Three rules that would each have caught it:**

1. **A negative names the artifact that would falsify it** — the results tree, the board, the
   worklog item. Then one step checks it. A bare "nothing has run" is unfalsifiable in place.
2. **Closing a campaign includes the row it scores.** Scoring cells for a model that has a registry
   row makes the row's `effort.evidence` / `roles` / `evidenceRef` update part of the deliverable,
   not a follow-up. The scheduling plan already names the row.
3. **A reader checks the negative before relying on it.** `CLAUDE.core.md` §Core Principles already
   requires verifying a decisive premise; a negative world-state claim IS one, and the results tree
   is the one-step check. Reading it as "someone checked" is the failure.

**Version pooling is a separate rule this incident produced:** where a newer version has a cell it
supersedes the older version's cell by cell; where it does not, the older cell STANDS rather than
dropping to unmeasured. Dropping an untested rung discards the only measurement of it — the first
draft of the fix did exactly that, and lost `low`-on-converged, which only the retired version had
ever tested. Canon: `rules/harness_authoring.md` §Any harness markdown.
