---
name: gotcha-synthetic-fixture-hides-real-input-failure
description: A test fixture engineered to satisfy the checks can pass every stage yet hide a failure mode only real input triggers
metadata:
  node_type: memory
  type: feedback
  originSessionId: e33ca9f0-be37-4de3-a1d8-1deb2c741274
  modified: 2026-10-07T13:44:38.889Z
---

A synthetic fixture built to *pass* the assertions exercises the happy path of every pipeline stage but can structurally exclude the failure mode that real input guarantees — so "proven end-to-end on synthetic" overstates coverage.

**Concrete case:** an image-conform pipeline's synthetic source was clean solid-color bands that passed every lint check, "proving" the pipeline. Conforming a *real* detailed sprite failed a hard connected-region check immediately — nearest-neighbor downscaling of detailed art fragments thin features (thin lines, glints, motes) into sub-threshold specks the clean bands never produced. The pipeline was missing a despeckle pass entirely; the fixture couldn't reveal it.

**Why:** the fixture was reverse-engineered from the checks, so it can't test what the checks-plus-real-data interaction surfaces.

**How to apply:** for any pipeline that transforms *external/arbitrary* input (downscale, parse, import, decode), the acceptance proof needs at least one *adversarial-by-nature* input — real data, or a fixture deliberately built to violate the invariant (specks, ragged alpha, off-grid frames), not one built to satisfy it. If real data isn't available in CI, encode its hostile shape into the fixture.

**Same failure for runtime artifacts:** the rule lives in `rules/harness_tooling.md` §Every guard has a re-runnable proof (a fixture of written output is cut from a captured live output). Evidence:
- 2026-09-22, session e33ca9f0: the dispatch-table hook passed 31 planted cases that put the per-run `workflows/<runId>.json` in place at launch. A live Workflow writes only `journal.jsonl`, `agent-*.meta.json` and agent transcripts until the run ends; the owner saw 2 rows marked `unresolved` for 6 agents.
- 2026-10-07, session ae2238a9: the Stop audit's planted journal lacked the live `key`, `phase` and `checked` fields and gave claims a lens and index, which hid three defects (journal ids read instead of archive F-ids, `review-fanout` vs `review_fanout`, explore keys from 1 instead of 0). Live runs found them; fixtures cut from those runs replaced the plant.
