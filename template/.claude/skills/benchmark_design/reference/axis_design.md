# Axis design — the bodies behind SKILL.md §1 and §6

Detail layer for `benchmark_design` §1 (scale depth, not count) and §6 (make guessing worthless). The
law and its litmus stay in SKILL.md; this file carries the tables, the traps and the per-decision rules
you apply while drafting rungs.

## Count-scaled versus depth-scaled

| count-scaled (fails) | depth-scaled (works) |
|---|---|
| sum N numbers, N rising | traverse N sequential hops, each knowable only after the last |
| copy N labelled items | resolve a conflict whose ordering signal gets subtler per rung |
| plant N irrelevant facts | plant one fact whose *relevance to the task* rises per rung |

Count scales tedium: a cliff wherever the arm breaks, no gradation either side, so the middle rungs
separate nobody.

## Repetition is not depth — the trap that catches the fix

"Follow N sequential hops" looks like depth and is not. **N lookups is still N of the same
operation** — eight steps that cannot go wrong are eight easy things.

**What makes a step hard is a DECISION that can go wrong**, so errors compound instead of accumulate:

- several plausible continuations at each step, resolved by a stated rule
- a dead end that must be recognised and backed out of
- a step whose result changes how the next step is interpreted

If every step is a lookup, the axis is still count.

## The axis must be constructed, not observed

**An observed quantity cannot be an axis**: mining a corpus for the property at level N fails because
the corpus sets it — a graph that fans out by 2 or by 99 yields nothing for most rungs and a
context-pressure rung where it yields anything.

**Invert it: make the rule range over what you RENDER.** "Among the candidates *included below*,
follow the one with the most references" makes branching a build parameter while every file, reference
and value stays mined from real data. The move is *find data with property P at level N* → *present
exactly N*.

**Constructing one parameter and leaving its neighbour observed fails the same way.** Rendering
exactly k candidates is worthless if the right one is obvious. Constrain the margin too.

**Check the selection criterion is INDEPENDENT of what the item structurally requires.** If the step
the chain must continue through is by construction the one the rule selects, the rule is decidable
without doing the work, and no metric fixes a structural conflict. Escape: a criterion orthogonal to
continuation — an authored value, not a structural one — which also restores tight margins.

## Two obligations that come with constructing an axis

- **Hold the confound flat in the unit that causes it.** Flat *document count* is not flat prompt
  length; pad to a fixed CHARACTER budget and assert the spread (≤25%). A rung that failed because its
  prompt was longer is measuring the neighbouring axis.
- **Assert the ladder rises.** Difficulty is computed (hops × branching), so check monotonicity in the
  builder. A staircase whose difficulty does not rise is not one, and the grid will not say so.

## Make guessing worthless

- **High-entropy answers.** A terminus of 0, 1, 2 or 3 is guessable without doing the work. Prefer the
  most distinctive value available and say so when none exists.
- **Decoy floor.** Pad so a blind guess is worth ≤1/12. Three rendered documents means a 1-in-3 guess
  contaminates the bound.
- **Decoys must be indistinguishable by shape.** Every decoy declares the same attribute as the answer,
  so scanning returns many candidates and only the real work picks one.
- **The join operator must not be satisfiable from one input.** A *maximum* returns one of its inputs,
  so a single-branch agent scores correct without joining; use a **sum**, a count, or a traversal.
- **Verify decoys are unreachable.** A decoy the real path can reach creates a second legal answer.
