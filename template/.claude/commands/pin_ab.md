---
description: Run one lens at two model/effort pins over identical input, then adjudicate which pin earned its cost.
---

# /pin_ab — paired pin comparison on a real lens

Measures whether a cheaper pin matches a more expensive one **on this harness's own lenses**, using
real inputs rather than a synthetic staircase.

**Why a comparison and not a flip.** A review lens that misses a defect emits a clean report, not an
error. There is no output signal distinguishing "found nothing" from "found nothing because the pin
was too cheap", so a pin change adopted without a paired run is unfalsifiable in normal use. The
paired run supplies the missing signal: the same input, scored against the other pin's findings.

**Both pins read a frozen input** (`orchestration` §0), or the cheaper pin is scored on a file the expensive one never saw.

**No new engine.** `dispatch.js` already takes N jobs with mandatory per-job `model`/`effort` pins and
prompts as file paths. A pin A/B is that engine called with the same `promptPath` twice under
different pins — never a bespoke script.

## Arguments

`/pin_ab <lens-key> [--pins <a> <b>] [--input <path>]`

| form | behavior |
|---|---|
| no argument | List the lenses with an open pin question (Phase 0), ask which to run, stop. |
| `<lens-key>` only | Default pins from the lens's current pin (control) vs the challenger recorded in Phase 0. |
| `--pins opus:low sonnet:high` | Explicit `model:effort` pair. Both must validate against `dispatch.js`'s `VALID_MODELS` / `VALID_EFFORTS`, or stop — a bad pin fails the whole job, not one arm. |
| `--input <path>` | Use this CONTEXT instead of capturing a fresh one. Required when replicating an earlier run. |
| unknown lens-key | Stop and list valid keys. Never substitute a near-match — scoring the wrong lens silently answers a different question. |

## Phase 0 — Is the pin question open?

**No-op gate.** Stop and report if the lens already has `n >= 3` paired runs recorded in
`.claude/scratch/pin_ab/<lens-key>.jsonl` with a consistent verdict. A pin question that is already
answered costs two dispatches to re-answer.

State the lens's **mandate shape**, which predicts the result and must be recorded before the run:

- **Closed / enumerable** — the mandate names what to enumerate and what to check each item against
  (evidence-grounding, doctrine-contradiction, call-site sweeps). The cheaper pin is expected to match.
- **Open-surface judgment** — the mandate asks which of an unbounded set matters (prior-art fit,
  friction realism, noise triage). The ladder records fabrication on open exploration for the lower
  rows, so a challenger here is being tested against its known failure mode.

Recording the prediction first is what makes the run falsifiable rather than a search for a
comfortable answer.

## Phase 1 — Capture the input once

Write the CONTEXT and the lens mandate to scratchpad files. **Both pins must receive byte-identical
input** — re-rendering the mandate per arm reintroduces the difference the run exists to isolate.

Done when: one `context.md` and one `mandate.md` exist, and their SHA is recorded in the results row.

## Phase 2 — Dispatch both arms

```
Workflow({scriptPath: ".claude/workflows/dispatch.js", args: {jobs: [
  {label: "<lens>@control",    promptPath: "<mandate.md>", model: "<a-model>", effort: "<a-effort>", agentType: "general-purpose"},
  {label: "<lens>@challenger", promptPath: "<mandate.md>", model: "<b-model>", effort: "<b-effort>", agentType: "general-purpose"}
]}})
```

`agentType` is required by the engine. Use `general-purpose` whenever the lens applies project rules;
a scout cannot follow doctrine it never received, and pinning it here would confound the agent type
with the model.

Done when: both jobs return non-null. A null arm is a failed dispatch, **not** a zero score — rerun
that arm rather than recording it as a loss.

## Phase 3 — Adjudicate

Per finding, from **both** arms pooled and de-duplicated by `file:line` + claim:

| verdict | test |
|---|---|
| `valid` | The defect is real and the lens's reasoning holds. |
| `invalid` | Reasoning does not hold, but nothing was invented. |
| `fabricated` | Rests on a file, symbol, line or quote that does not exist. **Verify first-party** — quote the raw tool output, per `feedback_delegate_output_trust`. |

Score three axes, never one composite:

1. **Unique valid defects** per arm — the only axis that decides whether the cheaper pin is adequate.
2. **Fabrications** per arm — a single fabrication outweighs a defect-count tie, because a fabricated
   finding costs verification time on every future run.
3. **Cost** in both currencies, from the PINS line via `/orchestration_metrics`.

Done when: every finding from both arms carries a verdict. An undispositioned finding reads as clean
and biases toward the arm that produced it.

## Phase 4 — Record, and do not conclude early

Append one row per run to `.claude/scratch/pin_ab/<lens-key>.jsonl`: lens, mandate shape, both pins,
input SHA, the three scores per arm, and the adjudicator.

**A single paired run does not move a pin.** Effort is non-monotonic at n=1 — a measured cell scored
7/7 on one task and produced one citation on another at the same pin. The floor for changing a pin is
**3 paired runs on distinct inputs with a consistent direction**; below that, report the run and leave
the pin alone.

Report the verdict as one of: `challenger matched` · `challenger lost — <axis>` · `inconclusive, n=<n>`.
