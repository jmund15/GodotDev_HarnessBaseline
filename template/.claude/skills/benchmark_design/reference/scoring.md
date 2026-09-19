# Scoring — the body behind SKILL.md §7

Detail layer for `benchmark_design` §7. The failure mode is punishing correct behaviour: a broken
scorer emits clean, confident numbers, and the shape it emits most often is *the arm failed* —
indistinguishable from the finding a calibration exists to produce. Transport labelling lives in
`configuration_and_transport.md`.

## A scorer that has only scored wrong answers is untested

Rejecting silence cannot prove a scorer accepts a right answer. Three fixtures: silence must fail *as
undelivered*, an exact correct answer must pass, and a wrong-but-well-shaped answer must fail *as
delivered*.

## Score-dict keys are a shared namespace

Score-dict keys are shared **across instruments**, because the collector formats from them. A name
reused with a different SHAPE crashes the collector rather than the scorer, where it would be obvious.
**Gate paired keys on both members, never the first**; and **give a new instrument's fields distinct
names** rather than borrowing a neighbour's.

## Scorer rules

- **Do the hand-check on the FIRST run against real responses**, not after a result looks strange — a
  wrong result matching your expectations never looks strange.
- **Test the scorer on a KNOWN-CORRECT answer**, not only on an empty or garbage one; the two failures
  point opposite ways.
- **The scorer's read-set is the arm's input set.** Every constraint source the key cites is handed to the judge or
  verifier that applies it; a verifier that cannot see a document the arm was shown charges a quote of it as a
  fabrication.
- **Parse the field the prompt asked for.** Scanning for the first, last, or any integer reads the arm's
  *working* rather than its answer.
- **Never conflate instruction-following with the measured capability.** Accept a bare answer when the
  wrapper is not the thing being measured.
- **Never require the absence of a term the correct answer must contain.** A migration note must NAME
  what changed, so scoring supersession by requiring the stale token absent scores textbook answers
  zero. Score the **claim**, not the token.
- **Never require a literal word from the source when paraphrase is correct behaviour.** Report overlap
  as a diagnostic; do not gate on it. **Its fingerprint is a RANK INVERSION, not a low score** — such a
  key rewards copiers and penalises writers, so the weakest model finishes above every reference arm and
  the grid stays orderly. Treat any inversion of a known capability ordering as a scorer bug until
  proven otherwise; that ordering is the only oracle a scorer has.
- **The ANSWER must be unique, not just the path to it.** "Report the value of `collision_layer`"
  against a `.tscn` whose many nodes each declare one keys the last occurrence while a top-down reader
  reports the first, scoring a correct arm zero. Restrict answers to properties occurring exactly once
  — tie-rejection applied to the answer: a rule with two legal outcomes cannot be scored.
- **Normalise case and separators.** `caster-separation` must match "Caster separation". Fold
  separators; keep the word sequence exact so an omission still fails.
- **Scope proximity rules to the sentence, not a character window** — a window wide enough to catch a
  real marker also reaches the neighbouring list entry.
- **Score sub-axes separately when they dissociate.** Refutation and aggregation are different
  capabilities; an arm can excel at one and fail the other in the same cell.
- **Be strict in the direction that UNDERSTATES.** A bound too low costs some routing headroom; a bound
  too high sends real work to an arm that cannot do it.

## Judge tier and key freshness

- **A judge's anchor is a claim about the deliverable, and the deliverable can check it.** Every
  found-vote's quote is matched mechanically against the deliverable (`score_cell.js`, ordered-token
  overlap at the calibrated cut, one span at a time when the quote is spliced); an unverified anchor
  discards that vote and lands under `coverage.anchorsRejected`. Re-calibrate with
  `scoring/anchor_calibrate.py` before moving the cut: zero rejections on real votes, one on the
  planted fabrication.
- **A recognition key tolerates a judge a tier below the arm.** Deciding whether a listed item was FOUND
  is recognition — the judge never has to produce the item — so the panel need not match the arm. An
  open-surface lens (unique valid defects, no item list) is generation: its judge must be able to
  produce the finding to weigh it, and a weaker judge inverts the ordering.
- **Beyond-key yield is the key's staleness signal.** Arms that keep returning valid claims outside the
  item list are measuring what the key knew, not what the arm can do. Assemble the beyond-key set as
  its own score and refresh the key when it grows; a key that never grows was never checked.
- **A new task scores OUTCOMES, never opinions.** Every item is a claim the root can verify — a file, a
  line, a behaviour. A judge asked whether the arm "argued well" scores its own taste.
