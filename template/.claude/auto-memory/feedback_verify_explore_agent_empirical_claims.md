---
name: Verify Explore agent empirical claims via prior-art grep
description: Verify a delegate's empirical capability or absence claim before it changes a decision.
type: feedback
---

When a delegate claims that the codebase or runtime cannot do something, verify the claim against
first-party code or prior art before using it in a design.

**How to apply:** Ask whether one focused search could disprove the claim. If yes, run it. A plausible
serializer, API, or framework limitation is still unverified until the real source supports it.

A session-diff-scoped delegate also cannot prove that a caller, test, or resource is absent from the
full tree. Search the full relevant scope before acting on `untested`, `unused`, `dead`, or `orphaned`.

**Why:** A correct claim within a narrow input can be false for the codebase. Scope and evidence travel
with the finding.
