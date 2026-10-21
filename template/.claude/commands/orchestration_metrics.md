---
description: Measure this session's dispatch cost per effort pin and archive it; /delegation_usage reports all-time usage
---

Empirical counterpart to `/self_evaluate`: that captures *what the agent thinks* went well, this measures *what each effort pin cost*. Feeds the `/eval_dashboard` Agents and Ladder views.

Collect native Workflow records and labeled sidecar records through the existing collector. Keep served model, transport, native currency, cost basis and requested/effective settings separate. The sidecar ledger has a legacy filename; it is not evidence that every row belongs to one provider. Unlabeled legacy rows surface only under `--sidecar-all`, which is a spend report and never archives.

Group descriptive totals by served model and requested effort, Workflow and sidecar rows in one table, each cost in its own unit (quota tokens or USD), and report missingness. Never average across models. Do not treat heterogeneous task totals as causal routing evidence. `cost_usd` is money spent and is empty for a plan-quota run; `list_usd` is the registry list price of the same tokens, the comparison figure across models (`tools/run_cost.py` prices any run the same way). Normalized sidecar rows carry `cost_basis` from the launcher record's `costBasis`; missing basis is unknown, not evidence of a legacy row. Native list prices use the recorded served model or requested model. Do not copy old vendor price ratios into current recommendations.

## Arguments

| Form | Behavior |
|------|----------|
| (none) or `archive` | Collect → rate → append to `.claude/orchestration_metrics.jsonl`. **The default.** |
| `summary` | Roll up the existing archive across sessions. Skips collection. |
| anything else | Unrecognized — name the ignored argument and run the default. Never silently skip the archive. |

Archiving is the point — an unarchived run is measured and then thrown away, and the aggregate is the only surface that can justify a ladder edit. **Invoking the command IS the decision to archive**; do not ask for confirmation before appending.

## No-op gate

If the collector prints `No Workflow runs found`, stop and say so. A session that dispatched no workflows has nothing to measure — do not synthesize estimates from the `Agent` tool (it records no per-agent usage). Sidecar-only sessions are still measurable: labeled ledger rows report even with zero Workflow runs.

## Stop-gate — only real-world work enters the archive

This store calibrates pins against work done on the project's live tasks. A **benchmark run** executes predefined, static tasks: a battery or pilot on fixture tasks, a benchmark cell, a calibration probe, or a judge scoring those arms. It never enters the archive, even when its output was useful. Mark each such run `--ignore-run <runId> --reason "<benchmark name>"` before archiving its session; its evidence belongs in `Claude/Meta/Model Effort Calibration Baseline.md`. The collector already drops benchmark-instrument workflows (`MEASUREMENT_WORKFLOWS`) and names them. A proof or probe run (a `/codify` gate replay or text probe) is a benchmark run too: it declares `args.probe: true` at launch, beside `args.measurement` when it pins off-tier, and the collector drops it the same way and prints its spend.

An **A/B run on a live task** is real work: the same active project task, run with different pins or guidance per arm. It archives with each arm's actual outcome.

Classify by the run's declared purpose (the workflow description, the jobs' shapes and prompt paths), not by provider count or by an `arm`/`A-B` label alone. A `currencyReason` does not classify anything: the provider guard reads only a verified `owner:` citation out of one, so a class word there is not evidence of what the run was.

Nothing else halts. The script's own refusals (already-archived run, unresolved `?` pin) are agent-resolvable and are reported, not escalated.

## Incremental rating — rate on consumption, not at session end

**Per-agent cost survives compaction; the verdict does not.** The usage data lives in the session
dir on disk, but rating asks whether an output was *accepted, reworked, or discarded* — context
compaction destroys. A long session is both the likeliest to compact and the heaviest dispatcher, so
deferring all rating to session end loses exactly the largest runs to `unrated`, which Effort
Calibration cannot consume.

**The rule:** rate at consumption, when you accept a dispatch's result, send it back or throw it away.
Consume what a watcher wake delivered (`reference/sidecar_dispatch.md` step 6 lists it; its closing `UNRATED` line names the session's unrated labels), then write all its verdicts in one `--rate-batch` call per wake. A lone result may use
`--rate`. The outcome rates the task's result, never the process exit. The tool resolves the label to its exact `run_id:label` key and takes model, tokens
and cost from the records at archive time; optional `--shape right|over|under` records a separate judgment (`over`: cheaper width, model or effort sufficed; `under`: the result needed more), while an omitted shape and `--accept-rest` leave it unjudged:

```bash
python3 .claude/tools/orchestration_metrics.py --rate <label|run_id:label> <clean|defects|rework|discarded> [--note "<D13>"] [--effort <pin>] [--shape <right|over|under>]
```

- A non-`clean` outcome needs `--note`; name the task-record decision instead of restating it.
- A record with no effort pin needs `--effort`.
- `--probe` marks a deliberate downgrade comparison (see *Over-pin candidates*).
- `--measurement` marks a comparison arm (native arms declaring `args.measurement` are marked automatically); a measurement row on a tier its model does not claim in registry `roles` stays out of the model x tier readout and is counted as `off-role measurement`.
- An ambiguous bare label is refused with the exact keys to use. A label that names no pending dispatch is refused with the pending keys and record paths that contain it, and the count of the rest.
- A run that ended with an empty record (stopped through `reap.py`, a killed launcher) is pending with no model, effort, tokens or cost.
- Several results at once: `--rate-batch <file|->` takes a JSON list of `{label, outcome, note?, effort?, tier?, shape?, mandates?, probe?, measurement?}`. Each entry follows `--rate`, and a refused entry is printed while the others are written.

A review-panel seat adds `--tier <role tier> --mandates '<json>'`, one call per seat. The role tier is one of orchestrator, architect, executor, fanout or scout (`validation` folds onto `fanout`):

```bash
--mandates '{"plc-memory-alignment": {"outcome": "clean", "unique": 0, "duplicate": 1, "invalid": 0, "fabricated": 0}, "plc-evidence-grounding": {"outcome": "findings", "unique": 3, "duplicate": 0}}'
```

A mandate's `outcome` is `findings`, `clean` or `not-reached`. A review seat's `clean` or `defects` measures precision, never recall: the tool computes it from the counts (`defects` on any fabricated finding or more than a quarter invalid), and recall between pins comes from `/pin_ab`. The quarter's basis: in the four-run plan-review pin A/B, qualified seats ran about one in nine to one in six findings invalid and supplementary seats about one in three; a quarter separates them. The archive keeps `tier` and `mandates`, and every report prints the panel-yield line: `UNCOVERED` for each not-reached mandate, and each mandate with zero unique findings across its reached merged-seat window (`PANEL_YIELD_WINDOW`, `tools/orchestration_metrics.py`). That line is the trigger to re-open the seat map that merges it. Each `review-fanout` run also prints a `Seat overlap` line, and the panel-yield line names a seat key with no unique finding in its recent runs; both come from the engine journal and need no manual rating. `--run <runId>` prints one run's per-seat cache write, cache read, output and input-equivalent, with the consolidator on its own row; it never archives.

Review-seat rows gain `ledger_join` when eligible for ledger enrichment. Joined rows carry `author_model`, `domain` and `review_stage`; `ledger_new` is present only when the joined source carries `seatLedgerNew`. An absent/unreadable join or missing newness is unknown, not zero (`tools/review_ledger.py`).

**Phase close** (any drive's close step, compaction, `/session_end`): run `--pending`, `--rate` each result that was not accepted as-is, then `--accept-rest`. `--accept-rest` is your assertion that every remaining completed dispatch was consumed and accepted as-is, so it writes `clean` for those. It skips unfinished or failed agents, missing effort pins and review-panel seats (Merge, Verify and Fold phases are not review-panel seats), plus any `lens-a+lens-b` label, names each skip, and prints each seat's `--rate <run:label> <outcome> --mandates '<json>'` line. Sidecar dispatches are rated only with `--rate`. Completed, effort-pinned Verify and Fold jobs without a merged-seat label are eligible for automatic `clean` rating. A dispatch you have not consumed stays pending; consume it or leave it unrated. The compaction hook prints a `[rating-debt]` line when debt exists.

**Sampling.** Only lens-engine seats (`review-fanout`, `explore-fanout`) sample. A lens group (label without its trailing number, model, effort) is saturated when the archive holds `SATURATION_N` rated rows for it, the last `SATURATION_RECENT` are `clean`, and none carries `mandates` (`tools/orchestration_metrics.py` owns both constants). One non-clean kept sample unsaturates the group. `--pending` lists only the stable crc32 pick of a saturated group's dispatches, one in `SAMPLE_EVERY`, and counts the rest as sampled out. Sampled-out dispatches need no rating and archive as `unrated` with `sampled_out: true`; a rating you do give still wins.

**Why a row is unrated.** `--archive-summary` names the unrated rows it can explain, by reason, read at collection and never stored (`orchestration_metrics.unrated_reason`, `UNRATED_REASONS`): `sidecar` (rate it with `--rate`), `panel-seat` (a review lens seat, needs `--mandates`), `engine-step` (a review engine's Merge, Verify or Fold stage, never hand-rated), `not-completed`, `no-effort`, `sampled-out` (reported separately), and `session-never-rated` (no row in that session or run was rated). A row in a session that was rated and otherwise rateable names no reason — it is plain rating debt.

`.claude/orchestration_verdicts.json` holds verdicts not yet archived, in the same `{key: outcome | [outcome, effort] | [outcome, effort, "probe"] | {outcome, effort, note, tier, mandates, probe}}` shape as `--verdicts`. A dict verdict's `tier` must be a role tier: any other value (a legacy panel width such as `Standard`) is refused with a `REJECTED unknown tier` line and never written to the row. A report merges it for display and an explicit `--verdicts` file layers on top; archiving a session prunes the keys it consumed. When the verdict file passes `PENDING_WARN_KEYS`, `--pending` prints a bounded preview of resolvable other-session owners and archive commands, with unresolved and sidecar keys aggregated. Apply the Stop-gate before archiving one.

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
sample. The minimum archive sample for ladder proposals is code, in `tools/model_tier_readout.py`.
Until it is met, retain the current default; no automatic substituted downgrade or pin-table update follows
from a clean result, a cheaper delegate, or candidate-list membership.

## Procedure

**1. Collect.**
```bash
python3 .claude/tools/orchestration_metrics.py --archive
```
Without `--archive` (or `--verdicts`) the tool only reports and leaves the archive, its shards and the verdicts file byte-identical. Joins `<session>/workflows/<runId>.json` (label, model, phase per agent) against the per-agent transcripts (tokens, turns, tools, wall-clock). Each Workflow row also carries `served_model`, the model its transcript reports (`null` when unrecorded); a `MODEL MISMATCH` line means the pin was not honored — a family pin such as `sonnet` is honored by any `claude-sonnet-*`. Effective effort stays unknown. Sidecar rows never gain `served_model`: a proxied child's self-report is not authority. Cost is normalized to base-input-token equivalents — output ×5, cache-write ×1.25, cache-read ×0.1 — so tiers compare in one number.

**2. Resolve unpinned agents.** Requested effort is retained separately from client-observed `observed_effort`; server-effective effort remains unknown. The collector resolves requested effort from a `PINS` log line, falling back to a literal `effort:` in an `agent()` opts object. Data-driven dispatch (`effort: job.effort`) defeats the fallback and reports `?`. Supply those in step 3's verdicts file as `[verdict, effort]` pairs, and add the one-line convention to the script so the next run resolves itself:
```js
log('PINS ' + JSON.stringify(Object.fromEntries(JOBS.map(j => [j.label, j.effort]))))
```

**3. Record each agent's falsification outcome.** Sidecar rows are recorded identically, keyed by their `-l` label; their `effort` is archived as the requested vendor string, which is correct — never translate it to an Anthropic effort level. This is what makes the archive worth keeping — cost without an outcome has no denominator and rewards under-pinning. Record **what happened**, never an effort verdict:

| Outcome | Means (what happened — objective) |
|---------|-----------------------------------|
| `clean` | Output accepted as-is; review found no defects. A null measurement — says nothing about whether a lower pin would have sufficed. |
| `defects` | Review caught defects; corrected without a re-dispatch. |
| `rework` | Sent back or re-done; a differently-sized pin plausibly avoided it. |
| `discarded` | Output reversed, discarded, or superseded — the pin is irrelevant to the loss. |

`discarded` is the highest-signal outcome: it usually indicts the *spec*, not the tier (an open decision pushed into an agent instead of settled first).

*Why outcomes, not verdicts:* "was the effort right?" is a counterfactual no participant can observe — the agent only experienced its own pin, and a flawless high-effort output is consistent with both "high was needed" and "medium would have sufficed". What IS observable is whether the output was accepted, corrected, reworked, or thrown away. Undershoot is derived from the falsification outcomes above; overshoot is derived at the aggregate (see *Over-pin candidates*). Legacy verdict words map through: `right-sized`→`clean`, `overshoot`→`clean`, `undershoot`→`rework`, `wasted`→`discarded`.

Anything already recorded on consumption (see *Incremental rating*) is merged automatically. This step
covers only what is still unrated: `--rate` each one (or `--accept-rest`, as at phase close). For a batch
from another tool, write `{"<label>": "<outcome>"}` — or `{"<label>": ["<outcome>", "<effort>"]}` for
unresolved pins — then:
```bash
python3 .claude/tools/orchestration_metrics.py --verdicts <file>.json
```
Outcomes merge before the table prints, so the roll-up you read is what gets written. Three refusals, all non-fatal to the flow — resolve and re-run:

| Script says | Meaning | Fix |
|---|---|---|
| `REJECTED unknown outcome` | Typo'd outcome string | Use one of the four above (legacy verdict words map through) |
| `Already archived … nothing appended` | This run is in the store | Nothing to do — the guard exists because duplicates silently inflate `--archive-summary` |
| `REFUSED: N agent(s) have no resolved effort pin` | Would write records the dashboard's Agents tab can't use | Add `[outcome, effort]` pairs; `--allow-unresolved-effort` overrides |

Unlisted labels archive as `unrated`.

**4. Report.** Print the per-effort roll-up and name any `defects`/`rework`/`discarded` agent with its cost. Ladder `effort` and registry `roles` edits come from `/eval_dashboard` §Ladder readout, whose minimum sample is code.

## Cross-session roll-up

```bash
python3 .claude/tools/orchestration_metrics.py --archive-summary
```
Also rendered by `/eval_dashboard`: its Agents tab is the sanctioned surface for `effort` proposals and its Ladder tab for `roles` proposals (CLAUDE.md *Self-Improvement Loop*: aggregate before tuning). The roll-up also derives the over-pin candidate queue (written to `.claude/orchestration_candidates.json`) and prints the convergence line (see *Over-pin candidates*).
