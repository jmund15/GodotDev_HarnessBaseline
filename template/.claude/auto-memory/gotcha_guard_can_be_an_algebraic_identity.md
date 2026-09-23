---
name: gotcha-guard-can-be-an-algebraic-identity
description: "A planted RED proves only the one mutation you planted; if the guard derives its expected value from the same expression production uses, it is an identity in its own inputs and every other mutation passes it."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: ab6b2825-2e2c-4014-8113-4102f2d79c3a
  modified: 2026-09-21T21:10:22.643Z
---

A guard whose expected value is computed from the same terms production computes the actual value
from **pins nothing**, however real its planted RED was.

Verified 2026-09-21, `ForagePromptFeedbackTests.AssertClears`. Production placed a label at
`artCentre + (artHalf + pad + h/2) / screenUp`. The guard measured
`((label.Position.Y - artCentre) * screenUp) - artHalf - h/2`, reading `artCentre`, `artHalf` and
`h` from the same fields production used and re-deriving `screenUp` with a copy of production's own
clamp. Substituting production's expression reduces the whole thing to `pad`, so the assertion was
`pad >= pad * 0.95`. The log said so on its face — `labelGap=0.0800m` against `padding=0.0800m`, at
two different art sizes — and I read it as confirmation rather than as the identity showing.

**Why:** I *had* proven it with a planted RED, so it looked verified. A planted RED proves exactly
one thing: that guard catches THAT mutation. Mine deleted the `/ screenUp` conversion, which is the
one term that does not cancel. Everything else — a wrong measurement, a wrong rendered height, a gap
ten times too large — passed untouched. "Prove guards fire on a planted violation" is necessary and
not sufficient; the plant tells you nothing about the mutations you did not plant.

**How to apply:** after writing a guard, substitute the production expression into it by hand and
simplify. If the free variables cancel and you are left comparing a constant with itself, it is an
identity — rewrite it against an INDEPENDENT oracle rather than tightening the tolerance. Here the
oracle was `camera.UnprojectPosition`, which asks the engine where a point actually lands on screen
and shares no arithmetic with the code under test; it immediately read `0.0873m` against an authored
`0.0800m` and exposed a real 9% unit-mix error the identity had been cancelling away.

Two smells that travel with this: a guard that copies a production clamp or constant verbatim, and a
guard whose logged value equals the authored input exactly on every run and every fixture.

Related: [[feedback-prove-guards-fire-on-planted-violation]],
[[gotcha-getaabb-reports-render-state-not-authoring]].
