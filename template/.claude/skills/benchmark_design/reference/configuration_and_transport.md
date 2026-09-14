# Transport match and corpus pinning — the mechanics behind SKILL.md §7–§8

Detail layer for `benchmark_design` §7 (the instrument's transport must match the deployment's) and §8 (fixed corpus, every-input SHA pin, item-id intersection).

### The instrument's TRANSPORT must match the deployment's transport

An axis only predicts fitness for a job whose SHAPE it reproduces. Change how the material reaches the
arm — inlined versus fetched with tools, one call versus a loop — and you have measured a different
capability under the same axis name, in numbers that stay comparable-looking.

**Name each deployment shape the battery is meant to route, and label every axis with the shape it
tests.** An axis matching no deployment measures for its own sake; one reported next to an axis of a
different shape invites exactly the ranking it cannot support.

When the transport differs, the direction of the mismatch decides what survives — a tool-equipped arm
is strictly more capable than a closed-book one:

- a **FAIL** measured with the richer transport still holds for the poorer one, *a fortiori*;
- a **PASS** measured with the richer transport establishes nothing about the poorer one.

**Stamping the row with its transport is necessary and not sufficient** — a stamp prevents pooling, it
does not make a verdict transfer.


### Hold the corpus fixed and vary only the REQUEST, wherever the axis allows it

The flatness rule (every rung within a few percent on characters) is usually satisfied by padding. It
can often be satisfied *exactly*: ship one byte-identical corpus to every rung and let difficulty live
entirely in what the arm is ASKED. Length, composition, ordering and retrieval load are then the same
object, and no confound survives.

**This is not always available** — an axis whose difficulty IS corpus size cannot use it. Where it is,
it dominates padding: padding balances a confound, this removes it. Ask first whether the thing you are
scaling can move out of the corpus and into the question.

### The corpus pin must cover EVERY input, or the instrument drifts between arms

A battery pinned to a commit is only pinned where it reads that commit. One input read *live* — a
submodule head, a working-tree path, a generated cache — silently re-renders the instrument as the
repository moves, so arms measured a day apart are measured on different instruments. Nothing errors,
and every arm reports a clean percentage over "the items" while the denominators differ.

- **Pin every SHA the builder reads, and record all of them in the key.** A SHA obtained by calling git
  at render time is not a pin.
- **Score across arms on the INTERSECTION of item ids**, computed at scoring time and reported with its
  size. A per-arm denominator is what hides the drift.
- **Diff the item set whenever an arm is added late** — one set-difference, and the only cheap check
  that catches this; a drifted instrument still looks perfectly healthy.
