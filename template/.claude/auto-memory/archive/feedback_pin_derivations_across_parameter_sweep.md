---
name: feedback-pin-derivations-across-parameter-sweep
description: "Pin a derived quantity across a SWEEP of its parameter, never at the single production value — candidate formulas often coincide at exactly one value, so a pin there certifies the coincidence, not the derivation."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: dea0e670-a3ba-4764-a603-db211c552f1c
---

# Pin derivations across a sweep, not at the production value

A test that exercises a derivation at only the shipped parameter value cannot
distinguish it from any other formula that happens to agree there. Rival formulas
frequently coincide at exactly one point, and the shipped value is often that point.

**Why:** the pin then measures a coincidence. The suite is green, the derivation is
wrong everywhere else, and the failure only surfaces when someone tunes the
parameter — far from the change that caused it.

**How to apply:** sweep the parameter across several values, including its extremes,
and assert the derived quantity's actual value at each. Beware assertions that
discard information (an angle *between* two results, a magnitude, an absolute
value) — those can pass under both the right and wrong formula.

**Verified:** two pins at the sole shipped value passed under *both* a correct and
an incorrect inverse-trig function; a multi-value sweep turned the same suite RED
on the first run.

See also [[feedback-stress-test-extremes-first]] (investigation-time sibling),
[[feedback-no-production-data-value-pins-in-tests]],
[[feedback-guard-must-fix-cause-not-mask-symptom]].
