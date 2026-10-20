---
name: feedback_absence_is_not_a_defect_until_dated
description: "A 'X lacks Y, and doctrine mandates Y' finding is a gap report, not a defect — git log -S separates a regression from a deliberate design, and the missing thing was often removed on purpose at a cost you already paid."
metadata: 
  node_type: memory
  type: feedback
  modified: 2026-08-20T05:46:18.319Z
---

A delegate reports that a surface lacks something doctrine requires. The finding reads as a defect
because the gap is real and the doctrine citation is correct. Both can hold while the conclusion is
still wrong: **the absence may be the fix, not the bug.**

Two provenance questions separate the cases, and both are one command:

- **Was it ever there?** `git log -S'<token>' -- <path>` — empty means it was never added, so
  "regression" and "enforcement point we lost" are both false framings.
- **Was it removed deliberately?** A non-empty result plus the commit message, and a memory search for
  the token, usually surfaces the incident that removed it.

**Why:** a mature harness accumulates absences that are *decisions*, and their reasons live in commit
history and memory rather than in the file. A reader seeing only the file sees an omission. Acting on
it re-introduces the thing that was removed, at the cost that removed it — and the delegate that
raised it cannot tell you, because it read the file and not the history.

Signal (2026-08-20): a fan-out lens reported `dispatch.js` "enforces no output schema while
orchestration §9 mandates one — a missing enforcement point for doctrine we already hold." Relayed
unverified into an assessment doc and a plan slice. The user challenged it from recollection. Checks:
`git log -S'schema'` on that file was **empty** across every commit — never present, so not a
regression; the unschema'd generic engine is the deliberate counterpart to a schema-owning sibling;
and what had actually been removed months earlier was `maxLength`/`maxItems` **caps** from the other
engines, after validator rejections destroyed finished deliverables twice
(see [[feedback_schema_caps_must_not_invalidate_delegate_work]]). The finding was withdrawn, and the
real defect underneath it — a dead job collapsing to `null` unflagged — needed none of the machinery
the original finding proposed.

**How to apply:**

- Before a "missing X" finding becomes a plan slice, a doc claim, or a fix: run `git log -S` on the
  token and semantic-search memory for it. Two commands, and they run before the finding is relayed,
  not after it is challenged.
- **State which case it is in the finding itself** — `never present` / `removed <date>, reason` /
  `unknown, history unavailable`. An undated absence reads as a defect to every downstream reader.
- Applies hardest to *engine* and *guard* surfaces, where the missing thing is usually a constraint,
  and constraints are what get removed when they cost more than they caught.
- The general shape of the trap is [[feedback_delegate_output_trust]]: a delegate reads the file, so
  it can only report the present tense. History is the orchestrator's job.
- Corollary for the reverse direction: a delegate's *replacement* numbers need the same scrutiny as
  the ones it corrected. In the same session the lens that correctly caught a bad category split
  supplied its own split, which summed to 65 against a total of 66.
