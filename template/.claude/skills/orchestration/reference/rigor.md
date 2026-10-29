# Rigor

Rigor (`light | normal | thorough`) is the one lever for how much a run must catch. Every stage that dispatches, reviews or folds reads it here; a layer that adds stages lists what rigor changes in each of them beside its own admission rules.

## Recording and reading

Record the level for any orchestration with `python3 .claude/tools/triage.py rigor --set light|normal|thorough [--task <id>]`: with a task id on that task's record; without one on this session's most recently updated task record, opening a `session-<sid8>` record when the session has none. A level the owner names, in a command's arguments or a message, is recorded before the next dispatch and holds for every later stage in the session until the owner changes it. No call carries its own rigor: every stage reads the record, so no two stages of one run disagree.

`triage.rigor(task_id, root)` reads the named task's record when it holds a level, else the level the session named last (the newest `rigorAt` among its records that hold `rigor`), else the project-class default in `adaptation.json`'s `rigor_defaults` through `tools/adaptation.py`: harness and game use `normal`, side tools use `light`. Defaults use the task's triage paths, else scope files, else the working tree's changed paths. Without a task, they use the session's newest record's paths, else the changed paths. Mixed classes take the highest level; unmatched paths or no paths use `normal`. `python3 .claude/tools/triage.py rigor [--task <id>]` prints the level in force; every stage report names it.

## The bar

| Level | A finding counts when |
|---|---|
| light | it is a defect that forces rework or a wrong result |
| normal | it also is a significant gap |
| thorough | the Reporting Filter admits it at all |

A finding's optional `minRigor` is the lowest level whose row admits it, and it counts at a run when `rank(minRigor) <= rank(run rigor)`. A finding counts at every level when its `minRigor` is absent or unrecognized, when it is `critical: true`, an ASK, or a PLAN with no `options`. The bar decides which findings owe a later round and move a verdict, never what you report or where you look. Grade each finding at the lowest level whose bar admits it; in doubt between two, take the lower level (light before normal before thorough, so the finding counts at more runs). The bar never filters a coverage gap: an UNCOVERED mandate, an UNVERIFIED claim or a NOT RUN check stays open at every level. Rigor never narrows how widely a seat searches and never lowers a quality floor, an owner approval or a coverage gap.

## Every panel

- Width: the size rule of [`review_width.md`](review_width.md) (thorough seats every admitted mandate; below it, merge what one reader can judge at the bar), never below the stage's own floor.
- Seats are told the bar and report every finding the Reporting Filter admits, tagged `minRigor`. The fold applies a below-bar finding only as a verbatim FIX (`commands/agents/review_fold.md` step 5) and defers the rest `below-bar`.
- A later round is owed only by units that answer counting findings, since a below-bar finding applied verbatim keeps its reviewer's coverage. Its seat is told the same bar.
- Stop when coverage is complete, every counting finding has a disposition and no supported critical stands. No count of rounds stops it.
- The report opens `rigor <level>` and closes `below bar: <N> noted` with one offer to apply them. That close is the Do-now gate's "declined with a reason and an offer", so a below-bar finding needs no defer reason. The caller applies offered ids through a standalone fold with `--ruled <ids>` and no `Bar:` line.

## Per stage

| Stage | Owner file | What rigor changes |
|---|---|---|
| Model and effort selection | `skills/orchestration/SKILL.md` §5, `reference/model_ladder_evidence.md` selection line | Rigor is one of the bars the ladder's selection line buys quality against. It never lowers a row below the tier's floor. |
| Later rounds | `skills/orchestration/reference/later_rounds.md` | Seat only units answering counting findings; the session's own design edits keep their seat. |
| Fold and protocol | `commands/agents/review_fold.md`, `commands/agents/orchestrator_action_protocol.md` | The disposition set gains `below bar: noted`; APPROVE needs no counting actionable finding. |
| Review engines | `skills/orchestration/SKILL.md` | `review_fanout.js`, `review_result.py` and `post_check.py` carry `minRigor` through merges. Every fold route states the bar in a `Bar:` line and stamps each finding's `counts` at it (`finding_bar.counts`), except the offer-apply fold in the bullet above, which omits it. `post_check.py` reports `rigor` and `belowBar`. |
| `/session_end` Phase 1 | `commands/session_end.md` | Candidate width and the harness seat read rigor through the project's admission rules; the audit applies the bar. |
