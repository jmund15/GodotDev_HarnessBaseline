---
description: Compare model/effort pins or tool profiles on one real lens
allowed-tools: Workflow, Read, Write, Bash(git worktree add:*), Bash(python3 .claude/tools/sidecar_fanout.py:*), Bash(python3 .claude/tools/lens.py:*)
---

# /pin_ab — paired pin comparison on a real lens

Measures whether a cheaper pin matches a more expensive one **on this harness's own lenses**, using
real inputs rather than a synthetic staircase.

**Why a comparison and not a flip.** A review lens that misses a defect emits a clean report, not an
error. There is no output signal distinguishing "found nothing" from "found nothing because the pin
was too cheap", so a pin change adopted without a paired run is unfalsifiable in normal use. The
paired run supplies the missing signal: the same input, scored against the other pin's findings.

**Both pins read a frozen input** (`orchestration` §0), or the cheaper pin is scored on a file the expensive one never saw.

**No new engine.** Two existing engines carry the comparison: `dispatch.js` for pins (N jobs with
mandatory per-job `model`/`effort` pins, the same `promptPath` twice) and `tools/sidecar_fanout.py
--compare` for profiles. Never a bespoke script.

## Arguments

`/pin_ab <lens-key> [--pins <a> <b>] [--profiles <a> <b>] [--input <path>]`

| form | behavior |
|---|---|
| no argument | List the lens candidates (Phase 0), ask which to run, stop. |
| `<lens-key>` only | Default pins from the lens's current pin (control) vs the challenger in the lens's Phase 0 `planned` row. |
| `--pins opus:low sonnet:high` | Explicit `model:effort` pair. Both must validate against `dispatch.js`'s `VALID_MODELS` / `VALID_EFFORTS`, or stop — a bad pin fails the whole job, not one arm. Dispatch is Phase 2's Workflow call. |
| `--profiles 'arm' 'arm,+mcp:ai-worker'` | Explicit tool-profile pair (see §Profile pairs). Model, effort, transport and task built-ins stay identical on both sides. Its `--compare` call replaces Phase 2's dispatch. |
| `--pins` and `--profiles` together | Refused before any child starts: one run answers one question. |
| `--input <path>` | Use this CONTEXT instead of capturing a fresh one. Required when replicating an earlier run. |
| unknown lens-key | Stop and list valid keys. Never substitute a near-match — scoring the wrong lens silently answers a different question. |

## Profile pairs (`--profiles`)

A profile spec is the sidecar's own contract: a `shape` with `+`/`-` items relative to that shape's
row (`arm,+mcp:ai-worker`, `survey,-mcp:ai-worker`). The row itself — every shape's built-ins and
MCP servers — lives in the project's tool-profile table; nothing here restates it. An `arm` spec needs its
task built-ins, passed as the job's `baseBuiltins` (`-B`); `reference/sidecar_dispatch.md` §Tool profile
owns why.

Lower each spec to a `tools/sidecar_fanout.py` job (`{kind: "arm", armDisclosure, tools:{add,remove}, baseBuiltins}`; `armDisclosure` only where the spec names a `-D`) and
run both through

```
python3 .claude/tools/sidecar_fanout.py <jobs.json> --compare --out-dir .claude/scratch/pin_ab/<lens-key>
```

`--compare` refuses to spend unless both cells read ONE detached worktree (`git worktree add
--detach <path> HEAD`) with byte-identical prompt, context and schema files. `tools/sidecar_fanout.py`
is the only route: a native Workflow child enforces a profile only as the `<prefix>-*` type an engine
selects on proven brief coverage, so a profile A/B there measures nothing.

## Phase 0 — Is the pin question open?

**Candidates.** `python3 .claude/tools/lens.py index` lists lens keys with their tier; the ladder's
cell for that tier is the lens's current pin. `/orchestration_metrics` names the adjacent-effort
comparisons worth running.

**No-op gate.** Stop and report if the lens's paired-run count n (Phase 4) already reaches the floor
with a consistent verdict. A pin question that is already answered costs two dispatches to re-answer.

State the lens's **mandate shape**, which predicts the result. Record it with the control pin, the
challenger pin (the next cheaper effort level the ladder rates for that tier) and the prediction as one
`planned` row in `.claude/scratch/pin_ab/<lens-key>.jsonl` before the run:

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

Both arms pin rows on purpose, so the call states `args.measurement` (what the comparison measures); the provider guard then skips tier fit. Each job takes the `arm` shape with the task's built-ins in `tools.base` (the `arm` row carries none of its own).

```
Workflow({scriptPath: ".claude/workflows/dispatch.js", args: {
  measurement: "<lens>: <a-model>·<a-effort> vs <b-model>·<b-effort>",
  jobs: [
  {label: "<lens>@control",    promptPath: "<mandate.md>", model: "<a-model>", effort: "<a-effort>",
   agentType: "general-purpose", shape: "arm", tools: {base: ["Read", "Glob", "Grep"]}},
  {label: "<lens>@challenger", promptPath: "<mandate.md>", model: "<b-model>", effort: "<b-effort>",
   agentType: "general-purpose", shape: "arm", tools: {base: ["Read", "Glob", "Grep"]}}
]}})
```

`agentType` is required by the engine. Use `general-purpose` whenever the lens applies project rules;
a scout cannot follow doctrine it never received, and pinning it here would confound the agent type
with the model.

Done when: both jobs return non-null. A null arm is a failed dispatch, **not** a zero score — rerun
that arm rather than recording it as a loss.

## Phase 3 — Adjudicate

Per finding, from **both** arms pooled and merged by claim within a file (the engine's merge stage):

| verdict | test |
|---|---|
| `valid` | The defect is real and the lens's reasoning holds. |
| `invalid` | Reasoning does not hold, but nothing was invented. |
| `fabricated` | Rests on a file, symbol, line or quote that does not exist. **Verify first-party** — quote the raw tool output. |

Score three axes, never one composite:

1. **Unique valid defects** per arm — the only axis that decides whether the cheaper pin is adequate.
2. **Fabrications** per arm — a single fabrication outweighs a defect-count tie, because a fabricated
   finding costs verification time on every future run.
3. **Cost** in both currencies, from the PINS line via `/orchestration_metrics`.

Done when: every finding from both arms carries a verdict. An undispositioned finding reads as clean
and biases toward the arm that produced it.

## Phase 4 — Record, and do not conclude early

Append one result row per run to `.claude/scratch/pin_ab/<lens-key>.jsonl`: lens, mandate shape, both pins,
input SHA, the three scores per arm, and the adjudicator. A `--profiles` run adds the axis it varied:
both cells' `-R` record paths, both `toolProfile` objects as recorded, and the `--compare` input hash.
Read the profile objects from the records, never from the spec strings — a spec is what was asked
for, and only the record says what the child was given.

**The paired-run count n** is the number of the lens's result rows with distinct input SHAs that answer the current question: the same axis (`--pins` or `--profiles`) and the same control/challenger pair. Rows of another axis or pair never count toward it. A single paired
run does not move a pin: effort is non-monotonic at n=1. The floor for changing a pin is n=3 with a
consistent direction, the smallest count where two agreeing runs outvote one dissent; below it, report
the run and leave the pin alone.

Report the verdict as one of: `challenger matched` · `challenger lost — <axis>` · `inconclusive, n=<n>`.

The scratch file is working state. At the floor, land the verdict where the ladder reads it: `/ladder_ingest` for a
model-pair result, `/codify` for a registry row. The lens's pin lives in the ladder cell for its tier and in the
command that dispatches it; this command edits neither, so the owner moves the pin.
