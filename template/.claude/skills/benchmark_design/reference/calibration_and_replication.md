# Calibration and replication — the bodies behind SKILL.md §2 and §3

Detail layer for `benchmark_design` §2 (the two calls that decide whether a sweep is worth running) and
§3 (what reps buy, per arm class). Ladder ordering and the two causes of a ceiling failure live in
`staircase_construction.md`.

## Two calls decide whether the sweep is worth running

- **CEILING** — the **weakest** arm on the **hardest** rung must **FAIL**. If it passes, the
  instrument's ceiling sits below the entire field and nothing can be bounded. **Conclusive.**
- **FLOOR** — the **strongest** arm on the **easiest** rung must **PASS**. If it fails, suspect the key
  before the arm: a bottom rung a strong arm fails is usually unanswerable, not hard. **Suspicious**,
  not conclusive.

**Run it against the cheapest reference arm too**, not only the weakest local one — the cheap cloud arm
is the ceiling that matters when the decision is "escalate or not".

## What zero passes in n trials bounds

**One PASS is conclusive. N FAILURES are not.** A weak arm clearing the hardest rung once proves it
clearable, so the ceiling is below the field. 0 passes in *n* trials only bounds the true pass rate.

| trials with 0 passes | true pass rate could still be as high as |
|---|---|
| 1 | ~95% |
| 3 | ~63% |
| 5 | ~45% |
| 10 | ~26% |

So **n=1 refutes a ceiling but never establishes one**. Treat 3 as the floor for a calibration claim
and say what the number does *not* prove. A single failure reported as "the arm cannot do this" is the
most inviting overstatement in this discipline.

## Determinism is per arm class

A rep that changes nothing is theatre.

- **Local, fixed seed, single-shot — NOT byte-identical on a GPU. Verify it; never assume it.** A seed
  fixes the SAMPLER'S DRAWS, not the DISTRIBUTION: GPU reduction order, quantised KV-cache (`q8_0`)
  rounding and batch shape inside a multi-arm sweep all move it. Assuming byte-identity is what
  licenses collecting a battery at n=1. **Check it with two runs of one cell and a byte compare.**
- **Separate the two variance questions, because they indict different things.** PINNED-seed reps
  measure *reproducibility*, which decides whether a grid can be published; VARIED-seed reps measure the
  arm's *sampling variance*. Pooled they answer neither: unstable-at-fixed indicts the harness,
  unstable-only-at-varied indicts the arm.
- **A conjunctive pass bit is the least stable statistic in the grid.** ANDing k field checks inherits
  the union of k error rates while reporting one bit. Report the components beside it.
- **A ONE-SEED BOUND CAN BE BIASED, NOT MERELY NOISY.** Only VARIED seeds measure the arm — fixed-seed
  reps are one measurement repeated on a deterministic arm and harness noise on a non-deterministic one.
  A single draw of a transition-zone cell can sit on the wrong side of it, understating an arm by a
  whole rung. **Replicate every BOUNDARY cell across seeds and report a pass RATE**, not a bound.
- **Cloud** — stochastic without help; repeat the call.
- **Multi-turn / agentic** — non-deterministic even locally; the fixed-seed exemption does not apply.

**Repeat with independent agents, not one agent asked N times** — an agent seeing its own earlier
attempts measures self-consistency, not reliability.

## Reading reps into a bound

**A bound is a reliability claim**: a rung passes only when EVERY rep passes. And a **flaky bottom rung
invalidates everything above it** — every bound inherits that instability.

## Boundary reps and the effort bracket

The boundary is where reps buy the most, and effort placement is a bracket, not a point.
`bench.py next` prints, per task, the cheapest-competent pin and the pin one rung/tier below it
(each with its n) — those cells go to n=3 on the unrestricted arms; and per (model, task) the next
effort rung: at the maximum or tied with the cheapest-competent pin → one rung LOWER; below the
maximum and not at the model's top rung → one rung HIGHER; closed when a pass sits beside a fail or
two adjacent rungs fall within the cell's uncertainty band. A pin the ladder cites is a closed
bracket, never a single rung.
