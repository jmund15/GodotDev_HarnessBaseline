---
name: feedback_move_destination_must_be_read
description: "A content move has two ends — the source gets read, the destination gets characterized from memory; open the destination before asserting what it holds"
metadata: 
  node_type: memory
  type: feedback
  modified: 2026-08-20T05:59:18.210Z
---

A move has two ends. The source gets read line by line; the destination gets described from memory — which is how a move recreates *inside* the destination the duplication it was removing from the source.

**Why:** 2026-08-20 — a doctrine plan named `model_ladder_evidence.md` as the home for a table three times without opening it. That file already held the table, its `±` column a verbatim concatenation of the two source columns. `/plan_check` returned it critical; the move became a merge-and-reconcile, and the reconciliation surfaced four live disagreements between the two copies.

**How to apply:** open the destination before the plan asserts its contents. The question is not *"does this fit here"* but *"what is already here, and does it disagree with the source?"* — two copies that differ is the finding; silently picking one is the defect.

Related: [[feedback_parity_preserves_defects_semantic_spotcheck]], [[feedback_refactor_parity_audit]]
