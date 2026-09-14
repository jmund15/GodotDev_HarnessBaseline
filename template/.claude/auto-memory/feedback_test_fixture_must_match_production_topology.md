---
name: feedback_test_fixture_must_match_production_topology
description: "Test fixtures that hand-wire what production discovers (graph chains, tree ancestry, singletons) validate a topology production never builds — green suite, broken game. At least one test per discovery chain must let production code build the chain itself."
metadata: 
  node_type: memory
  type: feedback
---

When production resolves a dependency by **discovery** (ancestor walk, scene search, graph-chain attach), a fixture that hand-wires the result (`AttachParent(runGraph)` direct, `InitializeXForTesting(...)`) skips the very code that can break. The suite stays green over a topology the game never has.

**Why (2026-07-07 empty-floors playtest):** encounter gating tests attached room graphs directly to the run graph; production goes room→floor→run, and the floor→run edge could never attach (the run controller was not an ancestor of the scene host's subtree). Every gated room shipped empty while the gating test passed.
**How to apply:** keep the fast hand-wired tests, but add ONE production-topology pin per discovery chain — real tree layout (disjoint subtrees where production has them), production `_EnterTree`/wiring code building the chain. Also applies to bind-time capture vs lazy reads across lifecycle ordering.
