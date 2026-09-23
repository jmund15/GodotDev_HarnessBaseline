---
name: feedback-orchestration-skill-model-agnostic
description: "orchestration skill (and other role-based mechanism docs) must speak in role names only — model-specific evidence/benchmarks belong solely in the ladder in reference/model_ladder_evidence.md"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: c2a2d3b7-b66e-4590-9e56-41338781f9d4
  modified: 2026-08-20T05:44:04.329Z
---

Never name a specific model (Sonnet, Opus, etc.) or cite model-specific benchmark evidence inside `skills/orchestration/SKILL.md` (or any other mechanism-layer doc). That skill is the *dispatch mechanism* layer and must speak only in `role` names (default fan-out, validation, executor, orchestrator) — model *attributes*, including any stage-shape effort split and the evidence behind it, live in exactly one place: the ladder in `.claude/reference/model_ladder_evidence.md` §Role guidance.

**Why:** the SSOT contract for this is stated inline in the skill's own opening line ("it names roles, never models — including in command examples — so a roster change never edits this file") — yet it was still violated by adding a "Worked example (Sonnet): ..." bullet with named-model benchmark claims directly into `orchestration/SKILL.md` §5. Having the rule physically present in the file being edited did not prevent the violation; it has to be actively checked, not assumed absorbed.

**How to apply:** when adding a stage-shape effort split or any per-model behavioral finding, put the actual model name + evidence in that model's row of the ladder (effort cell for the split itself, `±` column for the supporting evidence/caveat — mirror the executor row's inline-split format, currently `xhigh hardest design + buried-fork verification / high while ambiguous / medium exec / low tight specs`). The orchestration skill may only say something generic like "check the role's effort cell for a pre-encoded stage-shape split before assuming a flat value" — never name which model or cite which benchmark. Applies to any other mechanism-layer file (commands, other skills) that references the model ladder by role.

**Second violation + placement corollary (2026-07-16):** a delegation-stance correction was first encoded into ONE command's execute step with named model tiers ("dispatch to opus/sonnet") — wrong twice: named models in a mechanism-layer file, AND a universal session-model-relative doctrine placed where only one command (and only some tiers) would read it ("what if a lower tier model is running it? they might not dispatch"). Corollary: **universal delegation doctrine never lives in a command; a command gets at most a one-line pointer.** Fix it where every tier reads it, not where one command runs it.

**Which shared home, by when the decision is made (2026-08-20):** a model *attribute* → the ladder (`reference/model_ladder_evidence.md` §Role guidance). A *dispatch rule* stated in role names → `orchestration` §5/§5b/§11, which the skill description already triggers on the decision. Only a rule decided **before any dispatch is attempted** — so that nothing loading at dispatch time could deliver it in time — earns a place in always-loaded CLAUDE.md; currently that is copyable-vs-derived and the which-currency rule, and nothing else. Ordering, not subject matter, is the test.
