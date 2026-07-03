---
name: gotcha-synthetic-fixture-hides-real-input-failure
description: A test fixture engineered to satisfy the checks can pass every stage yet hide a failure mode only real input triggers
metadata:
  type: feedback
---

A synthetic fixture built to *pass* the assertions exercises the happy path of every pipeline stage but can structurally exclude the failure mode that real input guarantees — so "proven end-to-end on synthetic" overstates coverage.

**Concrete case:** an image-conform pipeline's synthetic source was clean solid-color bands that passed every lint check, "proving" the pipeline. Conforming a *real* detailed sprite failed a hard connected-region check immediately — nearest-neighbor downscaling of detailed art fragments thin features (thin lines, glints, motes) into sub-threshold specks the clean bands never produced. The pipeline was missing a despeckle pass entirely; the fixture couldn't reveal it.

**Why:** the fixture was reverse-engineered from the checks, so it can't test what the checks-plus-real-data interaction surfaces.

**How to apply:** for any pipeline that transforms *external/arbitrary* input (downscale, parse, import, decode), the acceptance proof needs at least one *adversarial-by-nature* input — real data, or a fixture deliberately built to violate the invariant (specks, ragged alpha, off-grid frames), not one built to satisfy it. If real data isn't available in CI, encode its hostile shape into the fixture.
