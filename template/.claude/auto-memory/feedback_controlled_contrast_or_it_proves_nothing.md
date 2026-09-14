---
name: feedback_controlled_contrast_or_it_proves_nothing
description: Two runs that differ in more than the named factor cannot attribute anything — hold every factor in ONE script; and a clean sheet is only evidence if that arm had exposure
metadata: 
  node_type: memory
  type: feedback
  modified: 2026-08-21T04:28:23.823Z
---

# A comparison across separate runs attributes to the wrong cause

**Signal (2026-08-20):** an investigation into local-model repetition loops published seven causal
findings across a day. **Six were retracted.** Every one died the same way — the two runs being
compared differed in a second thing nobody had named:

| reported | p | what killed it |
|---|---|---|
| task complexity drives it | 1.5e-08 | the hard-task arm also churned |
| KV-cache churn drives it | 1.7e-05 | held constant in a factorial: 6/90 vs 5/90, p=1.000 |
| quantisation drives it | 0.0061 | reversed on replication; pooled p=0.373 |
| runner age drives it | 0.036 | flat over 10 indices |
| the state is absorbing | — | 77% recover |
| sampling is refuted | — | those calls emitted 29 tokens |

Each was individually plausible, individually significant, and individually announced as resolved.
The p-values were real; the attribution was not. A factorial in one script found the actual cause
in one hour.

**Why:** a p-value describes the numbers you fed it, not the design that produced them. Nothing in
the arithmetic can tell you a second factor moved alongside the one you named — only the design
can, and separate scripts almost always differ in more than you remember. Cross-run comparison is
the *default*, because each probe gets written to answer its own question.

**How to apply:**
- **One script, all arms, interleaved.** If two conditions are being compared, they must be cells
  of one run, rotated so a period of machine load cannot settle on one cell. Two scripts written on
  different days are not a contrast no matter how carefully each was built.
- **Before reporting an effect, name the control it was measured against.** Can't name a run that
  held everything else fixed → it is a hypothesis, not a finding, and say so in those words.
- **A clean sheet is only evidence if that arm had exposure.** "0 loops in 90 calls" cleared
  sampling for half a day; those calls generated a median of 29 tokens and could not have looped.
  Report the rate per unit of *opportunity* (per long generation, per eligible row) beside the rate
  per trial — an arm that answers briefly, skips early, or filters everything out will post a
  perfect score while testing nothing. Same shape as [[gotcha_unit_filtered_test_run_fake_green]]
  and [[gotcha_batch_only_failure_needs_a_control_run]].
- **A detector reports zero for signatures it does not encode — so say what it cannot see, then go
  look.** The loop detector matched one repeated character, and 52 cap-hitting generations went
  unflagged; nothing in the count could distinguish "no loop" from "a loop of a shape I don't
  match". Reproducing all 52 and testing exact periodicity closed it — zero phrase-level loops, the
  detector was sound. Note the asymmetry that makes this worth doing: the check cost 25 minutes and
  could only ever have returned "sound" or "you have been miscounting all day".
- **Replicate before publishing a reversal-capable result.** The quantisation effect (p=0.0061)
  reversed completely on the next run — same arms, same cells, different seeds.

**The cost of getting this wrong is not a wasted hour, it is a wrong recommendation acted on.**
Two config changes were made on retracted findings and had to be reverted the same day.

Related: [[gotcha_local_llm_measurement_discipline]] carries the local-model-specific traps;
[[precedent_is_evidence_not_authority]] is the sibling for inherited claims.
