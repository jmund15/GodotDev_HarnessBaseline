---
name: arch_rule_greedy_commit_honor_declared_choice
description: "Progressive/greedy commit over a shared constrained resource must honor each committed structure's DECLARED choice — no cross-commit backtracking starves later structures."
metadata: 
  node_type: memory
  type: project
  originSessionId: 69bb06f7-efc1-454f-83c7-447be9974fdd
---

When structures are committed ONE AT A TIME into a shared, constrained resource pool (ports, slots, budget, cells) with NO backtracking across commits, an early structure that is free to *rebind* can greedily consume a resource a later structure needs — and there is no way to undo it. Fix: once a node/structure is FROZEN (committed in a prior step), edges/claims touching it must use the **declared** resource, not a freely-rebound one; rebind freedom is allowed only among the not-yet-frozen members of the current structure.

**Why:** holistically, the search backtracks and reshuffles to satisfy everyone; progressively, the frozen prefix is immutable, so a locally-valid greedy choice can be globally infeasible. **How to apply:** track a `FrozenNodes`/`committed` set; when enumerating candidate resources for an edge incident to a frozen member, restrict to the declared one. **Evidence:** the frozen-spine `GridFloorEmbedder` rebind closed a loop on `s1.pz_neg`, the exact spare port a later branch needed → `NoBinding`; honoring the loop's declared `pz_pos` on the frozen anchor freed `pz_neg`. Generic sibling of [[arch_rule_frozen_upstream_needs_informed_ordering]].
