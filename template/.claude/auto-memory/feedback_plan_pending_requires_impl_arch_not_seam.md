---
name: plan-pending-requires-impl-architecture-not-just-a-seam
description: A Part is plan-pending only when the architecture behind its interface is designed; a locked seam is not a designed implementer (seam-only → arch-pending).
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 6f623596-406e-41a1-9127-1f292b509e43
---

# Plan-pending requires impl-architecture, not just a seam

A Part is **plan-pending** only when the architecture BEHIND its interface is specified — a locked seam makes the *consumer* plannable, not the *implementer*. "Seam locked" ≠ "impl designed." A clean interface makes something plannable-LATER, not plannable-NOW; if only the seam exists → **arch-pending**, not plan-pending.

Plan-pending promises Plan Mode can enumerate files / sigs / tests. A seam-only Part can't honor that promise.

Concrete: P3b (graph-engine) was marked plan-pending with only the `INodeRealizer` seam designed; the packing / port-alignment / corridor architecture behind it was undesigned → corrected to arch-pending.

Related: [[process_rule_spec_doc_coverage]], [[feedback_parts_forward_reference_ordering_audit]].
