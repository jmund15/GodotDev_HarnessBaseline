---
name: feedback-spec-strength-sets-agent-tier
description: "Pick subagent tier from the SPEC's strength, not the task's surface area — exemplar + explicit per-file deltas + mechanical verification = sonnet, even for multi-file .tscn batches; opus is for judgment-bearing gaps in the spec."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 92e56590-48d0-4bba-afac-178408f0e359
---

When dispatching a subagent, choose the model tier by asking "what does the spec leave to the
agent's judgment?" — not "how many files / how fiddly does it look."

**Why:** 2026-07-09 the user flagged an opus dispatch (5-room .tscn disclosure/decoy edits) as
overqualified: the prompt carried a hand-verified exemplar file, exact per-room data tables,
hard format invariants, bake commands, and expected output counts — the same profile as the
zero-defect sonnet .tscn batch already cited in CLAUDE.md's delegation ladder. Writing a
sonnet-grade spec and then paying opus for it wastes the spec.

**How to apply:** After drafting a dispatch prompt, grade it: (a) exemplar present? (b) per-file
deltas explicit? (c) output mechanically verifiable (counts, greps, bake/build gates)? All three →
sonnet. Missing one AND the gap needs judgment (design choices, unscoped discovery, silent-failure
risk) → opus. The spec-writing effort is what buys the tier down — spend it, then actually take
the discount. Related: [[feedback-worker-model-bias-prompt-strength]].
