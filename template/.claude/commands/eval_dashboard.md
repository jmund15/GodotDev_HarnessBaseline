---
description: Build the self-evaluation dashboard
---

`tools/eval_dashboard.py` reads every archive in one pass and writes the note, the offline report and the month's history entry. The session model writes one file, the verdict, and reads nothing from the archives by hand. If a tool crashes or exits 2 on an unreadable input, fix it and rerun; do not model-read or rewrite an archive as a fallback. A finding is not a failure: step 3's exit 1 (a rule fired) and exit 2 (a malformed rule row) are results the dashboard reports, so carry on to step 4.

## Run order

1. `python3 .claude/tools/analyze_eval_archive.py` — read its bounded console output. It merges the frozen legacy snapshot (`.claude/self_evaluate_archive.json`) with the bounded JSONL ledger (`.claude/self_evaluate_archive.jsonl` and its rotations), keeps the newest row per `session_id`, and is the only full-archive session reader. Superseded rows are normal ledger revisions; no cleanup pass is required.
2. `python3 .claude/tools/load_census.py --budgets --json .claude/logs/load_census.json`
3. `python3 .claude/tools/rule_retirement.py --client-version <client> --tools <live tool names> --census .claude/logs/load_census.json --json .claude/scratch/eval_out/rule_retirement.json`. A missing census makes `load_census budget` triggers undecidable; the report says so. Its exit 1 or 2 is a finding (see above); only a traceback is a failure.
4. Run the analysis pass in `.claude/commands/reference/eval_analysis.md`; it names its inputs. Then delete and rewrite `.claude/scratch/eval_out/verdict.json` from it (schema below). Its `month` must equal this run's month. With no previous note, run step 5 once without `--verdict`, read the note it writes, then write the verdict and run step 5 again; a second run over the same sources changes nothing but the verdict.
5. `python3 .claude/tools/eval_dashboard.py --verdict .claude/scratch/eval_out/verdict.json --rules-json .claude/scratch/eval_out/rule_retirement.json`
6. On exit 0 or 3 only: `python3 .claude/tools/self_improvement_due.py --stamp`. It records the row count and date to `.claude/logs/eval_dashboard_last.json`, the row half of `session_context_loader`'s due-check.

Optional flags: `--today YYYY-MM-DD` (a real date), `--vault DIR`, `--root DIR`, `--refreeze YYYY-MM` (rewrite a closed month from the live sources; repeatable; exits 2 while the model registry is unreadable, because the month would lose its ladder), `--sessions-root DIR` (a directory of session directories, each holding `workflows/<run>.json`; it resolves ledger rows recorded before `session_id` to their session for the ladder's session counts; without it the dashboard reads this project's Claude Code sessions, matched from the working directory, so a run from another directory counts none). A malformed value, a `--sessions-root` that is not a directory, or an unreadable `--verdict` or `--rules-json` file exits 2 naming it.

## Outputs

| output | path | tracked |
|---|---|---|
| note | vault `Claude/Meta/Self Evaluation Dashboard.md` (one screen: a sessions block, a dispatched-agents block, at most 6 attention rows, ladder callout, inputs) | no |
| report | vault `Claude/Meta/EVAL-DASHBOARD.html`, nine tabs: Overview, Trend, Sessions, Agents, Ladder, Log, Inputs, Review, Findings | no |
| month notes | vault `Claude/Meta/Eval Dashboard Archive/Eval Dashboard <YYYY-MM>.md`, frozen when the month closes | no |
| history | `.claude/eval_history.json`, one snapshot per month, an earlier open month closed once, a missing earlier month backfilled; a month stores its sessions with the causes of the non-clean ones, and its agents as (model, effort) cells that sum to `n` plus the pay-per-token spend; a file of a previous schema is rebuilt month by month from the live sources on its next run, keeping each month's verdict, flags and stored skill-drift rows. Schema stays 3: the sessions block's `corrections`, `prompts`, `facts_rows`, per-domain/skill/per-duration-bucket sums, `drivers`, `cause_counts`, `rule_misses` (by kind) and `rule_miss_rows` are optional keys, so a month written before them is read as absent rather than zeroed, and the per-rule table stays live | yes |

The note's **sessions block** lists clean, correction and failure sessions for the prior and current month, each as a count and a share of the sessions with a known outcome, the corrections-per-100-owner-prompts rate beside the clean share when the month stores it (D2: it replaces nothing; a month stored before `facts` existed reads no rate, never a zero one), then the non-clean sessions by recorded cause: A rules existed, not followed; B docs missing; D tool and its docs disagree; E tooling bug cascaded across sessions; and a row for the sessions that recorded none of them (shown only when one exists). Code C is the clean session under the old convention and is not a cause. One definition line states that `/self_evaluate` grades each session itself; a second defines the change words and the bars. The report's **Sessions tab** draws each domain, skill and duration row with the same rate beside its clean share, then a **driver** table (one row per store-derived `model / effort`, with its outcome and rate) and a live **rule** table (rule, kind, sessions, example ids, from `rule_misses`; the month stores the per-kind counts only). Its **dispatched-agents block** lists only problem cells, each as `model at effort: k of n rated` (a problem cell has `FLOOR_MIN_FALSIFIED` or more defects or rework among `FLOOR_MIN_ROWS` or more rated agents, the constants the effort-floor rule uses), one caveat line with the unrated share, and one `pay-per-token spend: $X over N runs` line only when the month has runs priced above zero on a route whose registry `costModel` is `marginal-usd`. The note carries no pooled cost row: cost lives per (model, effort) in the report's Agents tab. The report's Overview and Trend tabs use the same labels and definitions. The month-over-month word `rebuilt later` marks a base month that was reconstructed from the ledgers that remained when it was first seen, so its agent counts are not comparable.

The note links the report by `file:///` URL. The report is one HTML file with its data inlined and no network reference.

## Exit codes

- 0 published.
- 1 vault or write failure.
- 2 invalid argument, unreadable input or invalid verdict.
- 3 published with a stale or unparseable input listed on the Inputs tab. An absent optional producer, such as the routing audit, is listed `absent` and does not degrade the run. A producer older than `STALE_DAYS` is stale. The orchestration ledger is unparseable when any line cannot be read, and the model registry is unparseable when it cannot be loaded (the Ladder tab and the note then say the readout is unavailable).
- 4 history refused: the stored file is unparseable or has an unknown schema and is left untouched. The note and report are still written; month notes are withheld.

`verdict.json` is removed after exit 0 or 3 and kept after 1, 2 and 4.

## Verdict

The verdict is the only hand-written content. Write it from the analysis pass, not from memory of the session.

```json
{"month": "YYYY-MM", "state": "<=300 chars", "moves": [{"do": "", "why": "", "see": "overview|trend|sessions|agents|ladder|log|inputs|review|findings", "finding": ["<id>"], "metric": "<key>"}],
 "watch": ["at most 3"], "layers": {"sessions|guards|cascades|pins|ladder|review": "move | no move: <evidence>"}, "milestone": "optional", "profile": {"strengths": ["at most 5"], "weaknesses": ["at most 5"]}}
```

`moves` holds at most 5 entries. `layers` is optional: when present it names a subset of the six layers, each `move` or `no move: <evidence>`, and `move` needs a non-empty `moves`. `finding` and `metric` are one optional pair: both or neither. `finding` lists ids from the Findings tab and `metric` is the one metric key the fix should move; an id or key that this run does not compute exits 2 naming the move. A move that cites another readout (a ladder proposal, a pricier-pin pair, a rule retirement, a review proposal) carries neither and renders `not measured`. `profile` is optional and appears on the report only. A violation exits 2 naming the field. With no `--verdict` and none stored for the month, the note and report show "Verdict not written for this run" and the snapshot stores null; a stored verdict is shown and kept. A closed month keeps the verdict written in it.

## Where each former section went

| former section | now |
|---|---|
| 1 Overview, 2 Timeline | sessions block with sparklines in the note; Overview and Trend tabs, with compare-two-months |
| 3 Patterns, 4 Domains, 4b Skills | Sessions tab; a skill falling 10 points or more with enough sessions is an attention row |
| 4c Routing stability | one Inputs row; a Routing card only when `routing_audit_stats.json` exists |
| 4d Effort calibration | Agents tab; attention rows for a moved floor and over-pin candidates |
| 4e Rules past their trigger | Log tab; up to the attention list in the note |
| 4f Model by tier | Ladder tab; verdicts and advisory proposals |
| 5 Corrections, 6 Valuable rules | Log tab |
| 7 Strength and weakness, 8 Conclusions | the verdict; the periodic review's proposals are on the Review tab |

`/autolearn` owns acting on skill drift and rule retirements; the dashboard reports them.

## Findings

`tools/eval_findings.py` computes the Findings tab from the archives, over the prior and current month: friction categories per session, session outcome shares, defect-note classes per served model, sessions with a guard denial per category (signature matches in the session transcripts, `tools/guard_denials.py`, not keyword counts), the `rules` family (one `rules.<slug>.sessions` per rule named in any row, emitted zero-filled in every window, with the sessions that recorded `rule_misses` as the denominator, and a `rule.<slug>` finding for a rule named in two or more sessions), and a fix status for each failed or cause-E session (`landed` only when a sha in its notes is an ancestor of HEAD). Two metrics of that set are not keyword counts: `outcome.corrections-per-prompt` divides the corrections by the store-derived owner prompts, times 100, over the rows carrying both (its floor still counts the sessions that carry them, and a row without `facts.owner_prompts` is typed absent, never a zero-prompt session), and `agents.unrated.all` / `agents.tier-absent.all` read the ledger rows' rating state instead of their notes. Every metric counts something bad, so lower is better, and each finding is addressable by its id for a verdict move. The remaining counts are keyword counts: they never assert a recurrence or a cause, the unclassified share prints beside each family, and the patterns are scored against hand-labeled strings (`tests/fixtures/eval_findings/`), so read an example before building a move on a count. The tool names no move; the verdict writer chooses which findings deserve one.

The Findings table's `last 6 weeks` column shows each metric in 7-day windows, oldest first (`eval_findings.trends`). The analysis pass owns how to read it.

A finding with a declined ledger row becomes a move only when the decline's reason no longer holds (`/autolearn` §Signal detection, Packet outcomes). The owner approves a move before it lands; a declined move is recorded with `python3 .claude/tools/autolearn_outcomes.py record --outcome declined --metric <key> --target <home path> --reason <why> ...`, so the Findings tab shows the decline beside the finding. It lands through `/codify`, whose Step 6 record carries the move's `metric` in `--metric`; the Findings tab then compares the month before the row's date with the days after it (the day it landed counts in neither) and shows `waiting`, `baseline thin`, `baseline partial` or `baseline unavailable` instead of a verdict on `moved` until both windows hold enough rows. `moved` is an association: other changes land in the same window.

## Review calibration

The Review tab (`tools/review_calibration.py`) shows the escape log beside the review yield log and lists the periodic review's proposals. It reads the live sources on every run; the monthly history does not store it. The tab holds the escape rows with file bytes and counts by mandate, domain, author model and tier; per mandate, the reviews it was reached in, its unique findings, its pre-merge `ledger_new` (single-mandate seats only; a null renders as `—`) and the escape rows naming it; the pre-merge seats by model family; and the ledger join counts (joined, absent, unreadable), so an absent join never reads as zero. An absent escape log or review ledger is an `absent` Inputs row and does not degrade the run. The note gains `review proposals: N — see the Review tab` only when N is above zero.

The dashboard edits nothing. The owner approves each proposal, then makes the edit.

| kind | fires when | proposes |
|---|---|---|
| `merge-seat` | `orchestration_metrics.seat_yield` returns the seat | merge the seat into one that reads the same input, with one evidence marker per mandate |
| `premerge-seat` | the newest pre-merge reviews of a seat and model family carry `ledger_new` 0 | drop the cross-family pre-merge seat |
| `sensitivity-tag` | an escape row has domain `untagged` | a tag over its paths |
| `triage-rule` | an escape row's recorded triage sits below `triage.raises` over its paths, or reads `unrecorded` | change the triage rule or tag |
| `compensation` | a model carries an owner `probation` mark or a driver note with `removeWhen` | removal per the note, shown beside the model's escape rows |

<!-- basis: reasoned; reverses-if: an escaped defect falls under a mandate a merge-seat proposal folded into another seat; review-when: escape tier:!inline since:2026-10-01 >= 1 -->

`premerge-seat` lists every group whose newest rows are all zero, longest run first, with the run length L printed. A null `ledger_new` is unmeasured and ends a run. The owner judges whether L is enough evidence.

## Effort Calibration

Sessions measure whether skills carry their compliance. The Agents tab measures whether effort pins are sized right, the only failure surface whose cost is attributable. It is one table keyed by model and requested effort, ordered by model then low to max then `?`, holding Workflow (`wf`) and sidecar (`sc`) rows together with each row's route mix. A row's model is the served model when the row records one, else the requested pin, resolved to the registry's canonical id through its alias data (`model_tier_readout.model_identity`, shared with the floor cells and the ladder readout), so a dated and an undated id of one model share a row; a bare family name with no served id cannot name a version and reads `<family> (version unrecorded)`. Every row lands in one column: clean, defects, rework, discarded, unrated (sampled-out rows included) or `other` (no outcome, or one outside the vocabulary), so the columns sum to `n`. Quota cost (Workflow rows) and USD (sidecar rows) are separate columns, each averaged over the rows that carry it. The route control on the tab splits every row by route, to see whether a model does worse on one route; the default view is merged. `orchestration_metrics.py --archive-summary` prints the same table. The tab's footer adds one line naming why the unrated rows are unrated (`orchestration_metrics.unrated_reason`, read at collection and never stored): the deliberate exclusions are the coverage line's, a row no structural cause explains is plain rating debt and is not named, and a row set with no named reason prints no line.

- **`defects` or `rework`** falsify a shape's floor. One instance is noise; two in one label family (the label before its first `:`) on one model at one requested effort, with at least 8 rated Workflow agents there, make the attention row "effort floor moved" (`review on <model> at <effort>: ...`). The note's problem cells apply the same two constants to a whole (model, effort) cell, across label families and both routes.
- **`discarded`** and **`clean`** mean what `/orchestration_metrics` step 3 defines. Over-pin candidates are advisory; `/orchestration_metrics` *Over-pin candidates* owns promotion on matched quality and complete-task cost, including parent work and rework.
- **Pricier pins** are computed from records, never ratings: fanout and scout dispatches pinned while a cheaper row claimed the tier. A pin chosen because its plan had more quota for its window is the selection line working, not an error; judge each recurring (tier, pinned row) pair by the `pick` its dispatcher recorded at launch (`orchestration` §5), read from the row, rather than by the posture of its dispatch days. A pair the recorded reason does not explain — a row whose `pick` is absent, or one no plan quota accounts for — becomes a verdict action: the cheaper row's ladder cell gains the shape it clears, or the pinned row's cell states the work only it clears.
- **`?` efforts** mean the script's `PINS` log line is missing (`orchestration` §9); that data is unattributable.

## Ladder readout

A cell is a served registry model at a role tier. The minimum sample is code: `MIN_ROWS`, `MIN_SESSIONS`, `CONTRADICT_MIN_FALSIFIED` and `CONTRADICT_MIN_SHARE` in `.claude/tools/model_tier_readout.py`, printed with every verdict. Every proposal is advisory, names the owner as the editor of `external_models.json` `roles`, and points at `/ladder_ingest`, whose §What lands weighs it against `bench` and `pub` evidence. Rows without a recorded tier land in "tier absent"; the footer counts each bucket. Each cell also shows `mismatch`, the number of its Workflow rows whose requested pin the served model does not honor (`/orchestration_metrics` defines `MODEL MISMATCH`; a `[1m]` context suffix is ignored); the row still counts toward the served model's cell.
