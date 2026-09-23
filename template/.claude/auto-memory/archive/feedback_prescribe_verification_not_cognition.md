---
name: feedback-prescribe-verification-not-cognition
description: "Harness procedures should prescribe verification/artifacts (gates, contracts, classification rules), not cognition (exploration order, option counts, fixed step pipelines) — cognition scaffolding built for weaker models caps frontier-model quality."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 5b51989a-86af-4d24-986d-83eb441291eb
  modified: 2026-09-23T17:23:07.241Z
---

User directive (2026-07-16, designing an autonomous design-drive command): the detailed brainstorm/drive pipelines were authored when agent judgment wasn't trusted; they bundle two content kinds that age differently. When authoring or refining autonomy surfaces, split them:

- **Cognition scaffolding** (fixed Socratic step order, "propose 2–3 approaches", round caps, prescribed panel compositions) — compensates for model weakness; ages into a quality ceiling. Make advisory or delete.
- **Invariants/interfaces** (grounding-in-canon, appetite invariant / taste-forks, independent adversarial dispatch, artifact gates like Part-readiness + a single roadmap-update executor, decision ledgers) — exists because the agent *can't know* project facts, *doesn't own* taste, or is *structurally biased* about its own output. Keep as hard contract regardless of model tier.

**Why:** enumerating options "just for the sake of it" (fixed counts, filler alternatives) is a flaw at ANY model tier — it costs the user fake decisions and trains rubber-stamping. Root fix shipped as the live-option litmus in the design-brainstorm skill's option step.

**How to apply:** per clause/step, ask *"does this exist because the model was weak, or because it can't know / doesn't own / is biased about it?"* Weak-model compensation → free it; the rest → contract. Canonical instances: a contract-style autonomous design drive vs an interactive design brainstorm (pipeline stays for continuous-taste sessions); a red-team panel whose fixed lens floor is verification and whose panel extension is judgment; execution gates (TDD RED, the regression gate) are verification, never cognition — don't free those. Related: [[red-team-must-be-independent-dispatch]].
