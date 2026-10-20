---
disable-model-invocation: true
---

# `/eval_dashboard` — the analysis pass (step 4)

The passes are a method, not a form: a quiet month needs a shorter sweep, an acute regression needs depth on one layer. What does not bend is that every proposal can be checked later and every earlier proposal gets a verdict now.

## Inputs

The previous note, the report data (Sessions, Agents, Ladder, Review, Findings tabs), `python3 .claude/tools/eval_findings.py --dashboard` (findings, the `last 6 weeks` windows and measured fixes), `python3 .claude/tools/orchestration_metrics.py --pricier-pins 30`, and the ledger history of each earlier move's home: `python3 .claude/tools/autolearn_outcomes.py history --target <home path>`. The earlier verdicts are in `.claude/eval_history.json`.

## Pass A: close the loop on earlier moves

*Why:* a change that is never judged teaches nothing. Without this pass the harness grows rules without knowing which ones work, and the same kind of fix gets proposed again after it failed.

Read each earlier move's effect row on the Findings tab before proposing anything new.

| Effect | Reading | Next |
|---|---|---|
| `moved: improved` | the fix held | no move; say so in `state` |
| `moved: flat` or `worse` | the fix did not work, or another change masked it | a different kind of fix; `/codify` Step 0 treats a second fix of the same kind as a repeat miss |
| `waiting` | too few sessions since the landing date | `watch`, with the date the floor is reached |
| `baseline thin`, `baseline partial`, `baseline unavailable` | the window before the landing date is fixed, so waiting adds no rows | name another metric or a paired check, as for `unmeasurable` |
| `unmeasurable` | the metric cannot show this fix | name another metric, or a paired check (`/pin_ab`, `/ladder_ingest`) |
| no ledger row | the move never landed | still open: re-propose, or drop it with a reason |

A window shorter than a week hides a one-day spike, so when a ledger row's date falls inside one, count by day.

## Pass B: sweep every layer

*Why:* evidence is spread across layers, and a cause often sits a layer away from its symptom (a defect class that looks like a model weakness can come from a seat contract; guard friction can come from a rule nobody reads). Walking every layer finds those links, and writing `no move: <evidence>` for a quiet layer lets next month tell "no problem" from "not looked".

Each layer yields a move, or one line `no move: <the evidence>`. Record the result in the verdict's `layers` map. Ask of each finding whether its cause lies in another layer.

| Layer | Read | Question | A move changes |
|---|---|---|---|
| `sessions` | outcome shares, `outcome.corrections-per-prompt` (corrections per 100 owner prompts, beside the clean share), patterns, corrections, repeated takeaways, `rule.<slug>` / `rules.<slug>.sessions`, the Sessions-tab rule table | which corrections recur across sessions, and which rule existed but was not followed? | a rule's reach or wording through `/codify` |
| `guards` | `friction.*` and `denials.*` with their trends and ledger history | which guard friction rises, and does the deny name the retry? | a guard's rewrite, deny text or proof |
| `cascades` | `cascade.*` fix status | which failed or cause-E sessions have no landed fix? | a fix commit, or a worklog item per session |
| `pins` | `agents.*` by model, Agents-tab floors, pricier pins | which model repeats which defect class, and is a stronger row buying anything? | a pin, seat contract or driver note |
| `ladder` | Ladder-tab proposals, the session-start ladder line | which cell rests on stale or thin evidence? | a ladder cell through `/ladder_ingest` |
| `review` | Review-tab proposals | which seat yields nothing, and which escape has no mandate? | a seat merge, a tag or a triage rule |

*Limits:* a friction count lags a landed fix and counts sessions, not guard fires. `denials.*` counts the sessions with a guard denial, read from the transcripts; read it beside `friction.*` when the question is how often a guard denied, or run `python3 .claude/tools/guard_denials.py` for per-call counts.

## Pass C: turn each candidate into a testable proposal

*Why:* a proposal that cannot fail cannot be judged, and one that ignores its price adds rules that every later session pays for. Naming the check now makes next month's Pass A mechanical.

A candidate becomes a move when its `why` states, in one line:

1. **Cause:** the choice or mechanism behind the finding, read from an example. Keep what was observed apart from what is inferred.
2. **Change:** the file and the edit shape, on the surface `/codify` Step 4 picks. Prefer fixing or deleting a mechanism over adding a rule.
3. **Check:** the metric that should move, its direction, and the date the next run can judge it (the floors of the effect row). A change no metric shows (a pin, a ladder cell) names a paired check, such as `/pin_ab`.
4. **Price:** what each occurrence costs and how often it recurs, against the edit's loaded bytes, code and proof.

A finding whose decline reason still holds is not proposed again (`autolearn_outcomes.py lookup`). Evidence below the floor goes to `watch`. Keep the moves with the largest recurrence against cost; the rest become `watch` entries or worklog items.

## Before writing the verdict

A reminder, not a gate: every earlier move has a verdict; every layer has a move or a `no move` line; every move carries cause, change, check and price; every finding that worsened across the windows shown is a move, a `watch` entry or named in a `no move` line.

The owner approves each move before it lands. A landed move records its metric through `/codify` Step 6, and the next run's Pass A reads the result.
