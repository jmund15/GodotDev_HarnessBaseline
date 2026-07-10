---
name: feedback_test_fixture_must_match_production_topology
description: "Test fixtures that hand-wire what production discovers (graph chains, tree ancestry, singletons) validate a topology production never builds — green suite, broken game. At least one test per discovery chain must let production code build the chain itself."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 66931000-c20f-4f54-b483-af3fa3e136e8
---

When production resolves a dependency by **discovery** (ancestor walk, scene search, graph-chain attach), a fixture that hand-wires the result (`AttachParent(runGraph)` direct, `InitializeXForTesting(...)`) skips the very code that can break. The suite stays green over a topology the game never has.

**Why (2026-07-07 empty-floors playtest):** encounter gating tests attached room graphs directly to the run graph; production goes room→FloorRuntime→run, and the floor→run edge could never attach (RunController not an ancestor of SceneHost's subtree). Every gated room shipped empty while `MultiRoomFloor_ClearRoom_ActivatesGatedRoom` passed.
**How to apply:** keep the fast hand-wired tests, but add ONE production-topology pin per discovery chain — real tree layout (disjoint subtrees where production has them), production `_EnterTree`/wiring code building the chain. Also applies to bind-time capture vs lazy reads across lifecycle ordering.
