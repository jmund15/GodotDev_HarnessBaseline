# Scoring — the body behind SKILL.md §7

Detail layer for `benchmark_design` §7. The failure mode is punishing correct behaviour: a broken
scorer emits clean, confident numbers, and the shape it emits most often is *the arm failed* —
indistinguishable from the finding a calibration exists to produce. Transport labelling lives in
`configuration_and_transport.md`.

## A scorer that has only scored wrong answers is untested

Rejecting silence cannot prove a scorer accepts a right answer. Three fixtures: silence must fail *as
undelivered*, an exact correct answer must pass, and a wrong-but-well-shaped answer must fail *as
delivered*.

## Score-dict keys are shared across instruments

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
  against a scene file whose many nodes each declare one keys the last occurrence while a top-down reader
  reports the first, scoring a correct arm zero. Restrict answers to properties occurring exactly once
  — tie-rejection applied to the answer: a rule with two legal outcomes cannot be scored. A research key
  obeys it per item: each item's premise follows from the arm's frame and inputs. When an item assumes a
  choice the frame leaves open (which stage an adoption replaces, say), the score measures which reading the
  arm picked: name the choice in the frame and rerun the arms, never key both readings. A key item no record
  finds is the trigger to check it.
- **A key split across runs credits a spanning fact in every slice it applies to.** Each run must establish
  the shared fact on its own to reach its deeper items, so each slice keys it with a slice-specific MATCH.
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
  found-vote's quote is matched mechanically against the deliverable (the battery's cell scorer, ordered-token
  overlap at the calibrated cut, one span at a time when the quote is spliced); an unverified anchor
  discards that vote and lands under `coverage.anchorsRejected`. Re-calibrate with
  the battery's anchor-calibration script before moving the cut: zero rejections on real votes, one on the
  planted fabrication.
- **A recognition stage's judge is set by Admit, never by its tier distance from the arm.** Deciding
  whether a listed item was FOUND, or ranking the arm against a fixed reference (and, for structural fit,
  against the code), is recognition: the judge never has to produce the answer, and the score depends on
  the deliverable and the key alone, never on which arm wrote it. Admit's calibration set therefore spans
  the deliverables hardest to recognize (correct answers phrased unlike the key, compressed or
  unconventionally cited), and a judge admitted on it judges every cell. An open-surface lens (unique valid
  defects, no item list, no fixed reference) is generation: its judge must be able to produce the finding
  to weigh it, so its scores are band-only until a fixed reference exists.
- **Judges are blind to the arm's identity: no path or label a seat reads names the arm.** A scoring root
  is an opaque `blind-stage/r-<id>` under the arms tree (the battery's score-root helper). Before dispatch
  the score daemon copies the deliverable, the input file, the battery verdict and a reference that sits
  in a campaign's cells dir into a per-cell `blind-stage/f-<id>/` stage (`args.blind`). The engine shows
  seats those copies and labels its dispatches with the stage id. `provenance.blind` records whether a
  cell was judged blind; a record without the field was not. Still open: deliverable prose that names
  its own model, and three channels of the exec scorer and its self-judge audit, which hold a shell:
  `git worktree list` (legacy arm-named roots of undiscarded campaigns included), the readable stage
  salt, and commit trailers `git am` keeps.
- **Each task's panel is the cheapest judge that passes Admit.** Price a judge by the tokens it spent on
  paired probes (the same fixtures as the incumbent), weighted at each model's list rate, or at the
  fitted plan-window rate once `plan_usage_calc.py` has one; never by a plan-quota label, list price
  alone, or an estimate. `python3 .claude/tools/run_cost.py --a <incumbent runs> --b <candidate runs>` prints
  that comparison. Candidates run as a ladder in measured-cost order, cheapest first, and a rung
  runs only after the one below it fails for that task. A recognition task starts at the fan-out tier;
  an architect-tier judge keeps a panel only after the cheaper row fails Admit at the descriptor's effort
  and its one retry. The self-judge audit and the exec scorer follow the same rule.
- **Every judge pin is an exact model id, declared once.** The descriptor's `defaultPanel` names it (a
  `verifierPanel` beside it when the verifier needs a different pin); a descriptor without one refuses to
  plan. Two seats at most: a 1-1 split is a wording defect, so reword the item rather than add a seat.
- **Judges ride the current version of their tier, through succession, never frozen.** A judge kept only
  for parity dies with its model. The battery's judge-succession script runs over each task's frozen
  calibration set (its probes plus 4 stored cells spanning the score
  range), and answers two separate questions:
  - **Admit — is the candidate at least as correct?** Its probes are green. Probes screen for a broken
    judge; they do not rank two sound ones. A probe failure is read before it gates: one the incumbent
    passed only on a split of its two seats, or one resting on a synthetic fixture the candidate rightly
    reads as gaming, is a warning, and the calibration rulings decide. Every calibration item where
    candidate and incumbent disagree is ruled from the recorded quote and the key: the anchor check where it
    decides, a recorded ruling for the rest. The candidate is admitted unless the rulings favour the
    incumbent on more items. Agreement alone never admits, because a better judge disagrees with a worse
    one. A task that fails Admit keeps its incumbent.
  - **Pool — do stored records still rank beside new ones?** Item disagreement at or below the
    incumbent's within-panel floor reads POOL, and the cross-panel band is published. Above it reads SPLIT:
    no band, the board ranks the two panels' records apart, and a comparator is re-judged at the new panel
    before any comparison with it is published. A one-seat panel takes its floor from the incumbent's stored
    multi-seat records at the latest generation that has them; with none it is SPLIT by rule.
- **A confidence threshold, vote weight or headline-credit policy change is a correctness question, so it
  takes Admit's test, never Pool's agreement figure alone.** Reliability across stored confidence tags is
  free to compute and says nothing about accuracy. Before recommending a switch to another policy, inventory the live and
  archived policies and each one's actual panel size. Before claiming an accuracy gain or none, rule a
  blinded sample with controls against the recorded quote and the key. Report correctness and reliability
  separately, each with its uncertainty: "no detectable gain" is not "no gain".
- **An arm judged by its own model gets a self-judge audit.** One extra judge call from a different model
  (the tier's prior version while served, else the other tier's current row) writes its own `-j<tag>`
  record, never joins the headline, and flags the cell when the gap exceeds the task's band.
- **A seat whose inputs have not changed since a keyed dispatch replays its cached return.** The key
  covers the seat's label, prompt, dispatch options, transport, the fields it saw unstaged, the bytes of
  every file it reads, and the driver's tool grant and permission mode, which every seat inherits. For a
  seat that reads the root, it also covers the root's identity: the HEAD commit, the content of every
  changed tracked file and every untracked file, the name, size and modification time of every ignored
  file, and each submodule's commit and its own identity. A root that cannot be identified leaves its
  seats keyless. The record is built by the engine from cached and fresh returns alike. A seat with an
  alias pin, a proxied transport, an unhashed or directory input, a mechanical scorer, an unattested
  served id, or an output a C8 ruling corrected always dispatches; `SCORE_NO_REPLAY=1` forces every seat
  fresh. A change to a seat's prompt or options changes its key, so that seat re-judges.
- **A judge leaving service is a warning before it is a failure.** `bench doctor` §0e names every declared
  judge whose registry row is gone or carries a `deprecation` date, and the tier's current row as its
  successor candidate.
- **Beyond-key yield is the key's staleness signal.** Arms that keep returning valid claims outside the
  item list are measuring what the key knew, not what the arm can do. Assemble the beyond-key set as
  its own score and refresh the key when it grows; a key that never grows was never checked. A full-key review classes each true, relevant finding `own` (an item of this run's add-on), `other` (an item of another add-on), `beyond` (substantive and held by no item) or `shallow` (a restatement of the survey, or visible without a chain of reads).
- **A key refresh admits facts by rule.** The harvest reviews every stored cell's findings against the full
  key, so no cell gains from having its own finds admitted; the refreshed key's gate runs on deliverables
  outside that pool, and every row scored on it is labelled in-sample. At least two curators from different model
  families rule each candidate, blind to its source cell; the owner adjudicates splits. A candidate at the
  key's lowest tier (SURFACE, by hop depth) is admitted only as a hop of an admitted higher-tier chain, a
  standalone shallow fact earns 0, and the key's no-credit section binds admission.
- **A new task scores OUTCOMES, never opinions.** Every item is a claim the root can verify — a file, a
  line, a behaviour. A judge asked whether the arm "argued well" scores its own taste.
