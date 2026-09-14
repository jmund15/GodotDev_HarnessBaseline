---
description: Measure this session's Workflow agents — cost per effort pin — and archive falsification outcomes.
---

Empirical counterpart to `/self_evaluate`: that captures *what the agent thinks* went well, this measures *what each effort pin cost*. Feeds `/eval_dashboard` → Effort Calibration.

Two sources, one command: Anthropic Workflow runs (harness records) AND DeepSeek sidecar runs (`deepseek_sidecar.sh -R`, read from `~/.claude/deepseek_spend.jsonl`). Sidecar rows render in their own table — real USD, requested-effort vendor coordinates — and are **never averaged into the Anthropic tables** (the axes are not commensurable). Only *labeled* sidecar runs surface (`-l "review:config-dup"` at dispatch); unlabeled rows are legacy/ad-hoc and appear only under `--sidecar-all` spend audits.

**One sidecar table per `servedModel`, never a blended one.** DeepSeek's tiers differ ~3.1× on fresh tokens (2.69× on a real 105-run historical workload), so a single averaged $/run describes no model that exists — it looks like information and is not. The grand total stays a *spend* figure; the per-model subtotals are the only comparable unit. Two caveats when reading the tables:
- **`effort` stays a requested vendor coordinate**, never an Anthropic rung — and pro's effort evidence is `unmeasured` in `.claude/reference/external_models.json`. Flash's never-request-`high` finding is a *flash* result; do not present it as a pro fact or infer a pro effort verdict from these rows.
- **Ledger rows written before 2026-08-12 were priced with hardcoded flash rates.** Any pro row from before that date under-reports by ~3.11×, and every older row used a `0.003` cache rate rather than the correct `0.0028`. Per-model tables therefore mix corrected and uncorrected `costUSD`; treat a cross-date comparison as approximate. Rows written since carry `costBasis` naming the model the price came from.

## Arguments

| Form | Behavior |
|------|----------|
| (none) or `archive` | Collect → rate → append to `.claude/orchestration_metrics.jsonl`. **The default.** |
| `summary` | Roll up the existing archive across sessions. Skips collection. |
| anything else | Unrecognized — run the default and name the ignored argument. Never silently skip the archive. |

Archiving is the point — an unarchived run is measured and then thrown away, and the aggregate is the only surface that can justify a ladder edit. **Invoking the command IS the decision to archive**; do not ask for confirmation before appending. Halt only on the stop-gate below.

## No-op gate

If the collector prints `No Workflow runs found`, stop and say so. A session that dispatched no workflows has nothing to measure — do not synthesize estimates from the `Agent` tool (it records no per-agent usage). Sidecar-only sessions are still measurable: labeled ledger rows report even with zero Workflow runs.

## Stop-gate — the one case that needs the user

**Synthetic arms.** This store calibrates effort pins against work that *shipped*; benchmark runs skew its per-effort cost/agent statistics permanently. Halt and ask before appending when any of these is true:

- the workflow's `meta.name` or agent labels carry bakeoff / calibration / benchmark / A-B / arm markers;
- two or more agents ran the *same* task differing only by a model or effort suffix;
- the session framed the run as a measurement or comparison rather than as delivered work.

If confirmed synthetic, append nothing here — those belong in `Claude/Meta/Model Effort Calibration Baseline.md`.

**Not synthetic:** the multi-model arms `orchestration` §0 prescribes for exploration, plan drafts, plan-check and review. Every arm's output was consumed into the shipped work, so they archive normally — rate each arm on what happened to its output (`clean` / `discarded` for the draft that lost).

Nothing else halts. The script's own refusals (already-archived run, unresolved `?` pin) are agent-resolvable and are reported, not escalated.

## Incremental rating — rate on consumption, not at session end

**Per-agent cost survives compaction; the verdict does not.** The usage data lives in the session
dir on disk, but rating asks whether an output was *accepted, reworked, or discarded* — context
compaction destroys. A long session is both the likeliest to compact and the heaviest dispatcher, so
deferring all rating to session end loses exactly the largest runs to `unrated`, which Effort
Calibration cannot consume.

**The rule:** when you consume a dispatch's result — accept it, send it back, or throw it away —
write its verdict immediately to `.claude/orchestration_verdicts.json`:

```json
{ "review:config-dup": "right-sized", "author:slice-3": ["undershoot", "medium"] }
```

Same `{label: outcome | [outcome, effort] | [outcome, effort, "probe"]}` shape as `--verdicts`;
the collector merges this file automatically, and an explicit `--verdicts` file layers on top for
anything still unrated. A `null` value is a **debt marker**, not an outcome — it names a dispatch
awaiting judgment. The third element (`"probe"`) marks a deliberate candidate comparison (see *Over-pin
candidates*).

The per-turn backstop is `budget_posture.py`'s `[rating-debt]` clause: it names the live unrated
count on any turn it changes, so a debt that survives a compaction is never silent for long. Rate
at consumption.

## Over-pin candidates — advisory, not a new default

`--archive-summary` groups historical rows by shape family and emits provisional candidates in
`.claude/orchestration_candidates.json`. The clean-only, cost-ratio and turn-count heuristics
screen for a comparison worth running; they do not establish matched tasks, equal coverage or
avoidable effort. Zero candidates does not prove convergence.

Before testing a candidate, declare the changed factor, frozen inputs, required quality floor,
independent review and complete-task accounting: parent work, delegates, failures and rework.
Keep currencies and cost bases separate. A missing cost or outcome is unknown, not zero.
Retain failed and partial arms. Use `probe` in the verdict record for a deliberate candidate test;
it is provenance, not permission to lower a pin.

Promote only when comparable complete-task evidence meets the quality floor across the required
sample. `/eval_dashboard` owns the minimum archive sample for ladder proposals. Until then,
retain the current default; no automatic substituted downgrade or pin-table update follows
from a clean result, a cheaper delegate, or candidate-list membership.

## Procedure

**1. Collect.**
```bash
python3 .claude/tools/orchestration_metrics.py
```
Joins `<session>/workflows/<runId>.json` (label, model, phase per agent) against the per-agent transcripts (tokens, turns, tools, wall-clock). Cost is normalized to base-input-token equivalents — output ×5, cache-write ×1.25, cache-read ×0.1 — so tiers compare in one number.

**2. Resolve unpinned agents.** The harness does not record `effort`. The collector reads it from a `PINS` log line, falling back to a literal `effort:` in an `agent()` opts object. Data-driven dispatch (`effort: job.effort`) defeats the fallback and reports `?`. Supply those in step 3's verdicts file as `[verdict, effort]` pairs, and add the one-line convention to the script so the next run resolves itself:
```js
log('PINS ' + JSON.stringify(Object.fromEntries(JOBS.map(j => [j.label, j.effort]))))
```

**3. Record each agent's falsification outcome.** Sidecar rows are recorded identically, keyed by their `-l` label; their `effort` is archived as the requested vendor string (`max`/`low`), which is correct — never translate it to an Anthropic rung. This is what makes the archive worth keeping — cost without an outcome has no denominator and rewards under-pinning. Record **what happened**, never an effort verdict:

| Outcome | Means (what happened — objective) |
|---------|-----------------------------------|
| `clean` | Output accepted as-is; review found no defects. A null measurement — says nothing about whether a lower pin would have sufficed. |
| `defects` | Review caught defects; corrected without a re-dispatch. |
| `rework` | Sent back or re-done; a differently-sized pin plausibly avoided it. |
| `discarded` | Output reversed, discarded, or superseded — the pin is irrelevant to the loss. |

`discarded` is the highest-signal outcome: it usually indicts the *spec*, not the tier (an open decision pushed into an agent instead of settled first).

*Why outcomes, not verdicts:* "was the effort right?" is a counterfactual no participant can observe — the agent only experienced its own pin, and a flawless high-effort output is consistent with both "high was needed" and "medium would have sufficed". What IS observable is whether the output was accepted, corrected, reworked, or thrown away. Undershoot is derived from the falsification outcomes above; overshoot is derived at the aggregate (see *Over-pin candidates*). Legacy verdict words map through: `right-sized`→`clean`, `overshoot`→`clean`, `undershoot`→`rework`, `wasted`→`discarded`.

Anything already recorded on consumption (see *Incremental rating*) is merged automatically — this step
covers only what is still unrated. Write `{"<label>": "<outcome>"}` — or
`{"<label>": ["<outcome>", "<effort>"]}` for unresolved pins — then:
```bash
python3 .claude/tools/orchestration_metrics.py --verdicts <file>.json
```
Outcomes merge before the table prints, so the roll-up you read is what gets written. Three refusals, all non-fatal to the flow — resolve and re-run:

| Script says | Meaning | Fix |
|---|---|---|
| `REJECTED unknown outcome` | Typo'd outcome string | Use one of the four above (legacy verdict words map through) |
| `Already archived … nothing appended` | This run is in the store | Nothing to do — the guard exists because duplicates silently inflate `--archive-summary` |
| `REFUSED: N agent(s) have no resolved effort pin` | Would write records Effort Calibration can't use | Add `[outcome, effort]` pairs; `--allow-unresolved-effort` overrides |

Unlisted labels archive as `unrated`.

**4. Report.** Print the per-effort roll-up and name any `defects`/`rework`/`discarded` agent with its cost. Overshoot is not a rated outcome — it is derived (see *Over-pin candidates*). Do **not** propose edits to the ladder's `effort` column (`reference/model_ladder_evidence.md`) from one session — n is 3–5 for a typical session, and per-session conclusions are noise. Tuning happens from the aggregate.

## Cross-session roll-up

```bash
python3 .claude/tools/orchestration_metrics.py --archive-summary
```
Also rendered by `/eval_dashboard` → *Effort Calibration*, which is the sanctioned surface for proposing ladder edits (CLAUDE.md *Self-Improvement Loop*: aggregate before tuning). The roll-up also derives the over-pin candidate queue (written to `.claude/orchestration_candidates.json`) and prints the convergence line (see *Over-pin candidates*).
