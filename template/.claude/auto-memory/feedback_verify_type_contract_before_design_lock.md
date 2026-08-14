---
name: feedback_verify_type_contract_before_design_lock
description: "Verify a type's mutability/identity/equality contract against code before locking a design; independent red-team consensus inherits shared premises."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: e47019d7-3235-4772-920c-5cd1c8d0148d
---

Before locking a design that mutates, replaces, snapshots, or compares a type, verify that type's REAL contract against the code — mutable-vs-immutable, reference-shared-vs-copied, value-vs-reference equality. Independent red-team passes reason from the design's stated premises, so N agreeing passes can converge on a false foundational assumption they all inherited: **consensus ≠ verification.**

**Why:** review passes sharpen design *shape* but can't catch a wrong type-contract baked into the shared context — "code is truth" applies to design brainstorms, not just runtime.

**How to apply:** grep/Read the type declaration for the contract BEFORE the Step-5 design lock. Links: [[feedback_delegate_output_trust]], [[feedback_inventory_implementation_not_just_contract]].

**Concrete:** P4 gated-edge brainstorm (2026-07-04) — 4 per-decision red-teams + 1 integration red-team + the draft all assumed a mutable `SetGated`; `GraphEdge` was `sealed` + all-get-only (immutable), so the mutate-in-place mechanism was impossible → replace-and-swap. Only reading `GraphEdge.cs` caught it.
