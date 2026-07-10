---
name: arch_rule_frozen_upstream_needs_informed_ordering
description: "Freezing an upstream artifact before downstream commits only NET-reduces retries if the downstream is ordered by the frozen artifact's REAL properties, not a proxy."
metadata: 
  node_type: memory
  type: project
  originSessionId: 69bb06f7-efc1-454f-83c7-447be9974fdd
---

Committing an upstream artifact early and freezing it (so downstream decisions become binding) does NOT automatically reduce retries — it TRADES one retry class for another. Downstream items that don't fit the frozen artifact now fail against it, where before they failed later. To NET-reduce retries, the downstream commit must SELECT/ORDER candidates using the frozen artifact's real measured properties, not the proxy that was adequate before freezing.

**Why:** the frozen artifact removes the joint-search flexibility that previously absorbed bad downstream choices; the only way back to first-try success is to make the downstream choice correct against the frozen reality up front. **How to apply:** when you add a freeze step, also add a real-property query the downstream consults to pick fitting candidates (and retry candidates LOCALLY before escalating to a full re-roll). **Evidence:** frozen-spine progressive embed initially did *worse* on first-attempt rate (graph-distance-chosen loop anchors didn't fit the frozen spine → whole-floor re-roll); ordering anchor pairs by real `GridStepDistance` + retrying pairs within one attempt restored the win. **Second instance (same generator, branches):** branches under-filled identically because a STATELESS first-spare-port picker re-selected the same loop-boxed-in anchor every retry iteration — a retry loop that re-invokes a deterministic stateless selector retries the SAME loser; fix = materialize candidates + exclusion-set retry gated by the real trial-embed. **Corollary:** an *enumerator* ("has an unbound port") is NOT a usability *ranking* ("that port opens into free space") — only the authoritative gate (here trial-embed) decides fit; ordering by the enumerator alone is cosmetic. Pairs with [[arch_rule_greedy_commit_honor_declared_choice]]; the proxy-vs-real distinction echoes [[feedback_no_magnitude_as_type_discriminator]].
