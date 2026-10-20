---
name: feedback_one_observation_licenses_a_hypothesis_never_a_scope_decision
description: "A single run justifies a hypothesis, never a decision about what a model or system may be given. Twice in one session a scope was narrowed from n=1 — a whole role map withdrawn after one weak parity run, and a benchmark declared absent after searching two of four roots. The tell is a sweeping quantifier in the conclusion."
metadata:
  node_type: memory
  type: feedback
  modified: 2026-09-04T04:05:48.237Z
---

Jacob caught this twice on 2026-09-03, hours apart:

> "that's only n equals one and you just made a whole sweeping assumption to not give it any more work"

and later, on a claim that a model had never been benchmarked:

> "please actually verify the luna benchmark question. tbh i don't believe you."

Both times the evidence was one observation and the conclusion was a scope decision. The first
withdrew a model from every heavy task after one weak parity run — the run turned out to be a
2-turn non-engagement, an *effort* fault, not a capability one. The second declared a benchmark
absent after searching two of its four roots; the model had a full battery and 26 records on disk.

**Why the existing rule missed it:** `orchestration` §5 already says *never characterize a tier from
one observation*. Both failures sat just outside its wording — one was about a MODEL's role map, the
other about an ARTIFACT's existence. The principle is not about tiers.

**How to apply:** one run licenses a hypothesis and the next experiment; it never licenses a
decision about what a model may be given, what a system supports, or what exists. The tell is a
sweeping quantifier in the conclusion — *never*, *any*, *every*, *all*, *none*. When one appears,
name the observations behind it and count them; at n=1, state the hypothesis and the experiment that
would settle it instead. For an ABSENCE specifically, name the sources searched and the ones not
searched — a negative rests on coverage, so it is only as strong as the universe you checked.

Related: [[gotcha_benchmark_data_spans_four_roots]], [[gotcha_effort_gates_engagement_on_a_sidecar_model]], [[feedback_completion_is_not_engagement]]
