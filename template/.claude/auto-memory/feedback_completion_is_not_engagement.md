---
name: feedback-completion-is-not-engagement
description: "A run's exit code and model attestation prove the machinery ran and who ran it — never that it engaged the target it was dispatched against; verify engagement before consuming any run as evidence."
metadata: 
  node_type: memory
  type: feedback
  modified: 2026-08-21T13:53:58.988Z
---

A delegated run has **three** independent things to prove, and the first two are the ones every
harness already checks:

1. **Completeness** — did it finish? (exit code, stop reason)
2. **Identity** — which model produced it? (attestation)
3. **Engagement** — *did it touch the thing it was supposed to measure?*

Nothing about a clean completion implies the third. A run that engaged nothing emits a record
**byte-indistinguishable** from a good one: `exit=0`, complete, attested, plausible token counts.
The failure is silent by construction, so it survives every check that reads the record alone and
surfaces only downstream — after a judge panel, or never.

**Why:** measured twice in one session (2026-08-20), both caught by a scorer hours later rather than
at dispatch:

- An arm was told to read a staged 32 KB survey that was absent from its root. `Read` returned
  "File does not exist"; it spent 104 turns assessing the document from the prompt's one-line
  summary and scored 24.5/219. The valid re-run scored 48/219 — so the void cell did not merely
  waste a cell, it **published a halved capability number** that a written assessment then reasoned
  from.
- An effort-sweep cell was dispatched with `-d <own-root>` while the prompt's line 1 still named its
  sibling. **The prompt text is the instruction; the flag is only a starting directory.** The arm
  worked in, and committed into, the sibling: 1455 references to the sibling against 15 to its own.
  It voided itself *and* made the sibling's branch jointly authored, so neither arm's work is
  separately attributable there any more.

The second is the sharper lesson: **when you parameterize something that was previously fixed, the
old fixed value is duplicated in places the new parameter does not reach.** Repointing one encoding
of an identity (a flag) leaves every other encoding (prompt text, embedded paths, labels) pointing
at the old target — and the one the *model* obeys is usually not the one you edited.

**How to apply:**

- Before consuming any delegated run as evidence, run
  `python3 .claude/tools/void_check.py engagement <arm>.progress.jsonl --root <root> --required <input>`.
  It VOIDs on a sibling root out-referencing its own, on zero references to the dispatched root, and
  on a required input whose read returned not-found. An *attempted* read is not a read — attempting
  is the T1 signature, not a defence against it.
- When a fix or a new parameter lands at one call site, grep for every other site encoding the same
  identity before claiming done — this is [[feedback_fix_the_class_not_the_instance]] applied to
  identities rather than to bugs.
- A guard that only writes to a log is not a gate. Both failures above had a detector that fired:
  `stage_luna_battery.py` printed `STAGING FAILED` and the caller logged `staging exit=1` and
  dispatched anyway. Put the check where the caller cannot proceed past it — see
  [[gotcha_prove_a_guard_by_making_it_fire]].
- Related: [[feedback_controlled_contrast_or_it_proves_nothing]] — a cell that measured the wrong
  target is a contrast whose named factor is not the factor that varied.

**Verified:** 2026-09-04 memory-claim audit — `.claude/tools/void_check.py:63-105` carries the VOID predicates (sibling-root out-reference, zero self-reference, required-input not-found).
