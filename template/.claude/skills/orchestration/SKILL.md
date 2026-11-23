---
name: Orchestration
description: >-
  Use before delegating or coordinating multi-step work: picking inline, a native job or an external seat, a model and effort, a fan-out's width, or consuming a delegate's result. SKIP small inline edits and known lookups where handoff costs more than the work.
---

# Orchestration

Every dispatch choice (route, width, model, effort) serves the goal in `CLAUDE.core.md` §Core Principles. Settle each question at the first stage whose inputs can settle it. A question a read, search or check settles now is settled now; one handed forward names the input it waits on (a run's result, an owner answer, an independent reading).

## 0. Route

| Route | Use |
|---|---|
| Inline | Small, understood edits and lookups; the parent's judgment and cross-system synthesis. Price each inline call at the parent's whole context. |
| Native job | A delegated job on the session's own login: `dispatch.js` through `/delegate`, one job, a parallel set, or `chains` when each hop feeds the next with no judgment between. |
| Seat | A job on another login or model family through `seat.sh` (`/delegate`): when the session's 5-hour window will not last the job (§5b), when a review wants a second family, and always when the job's workdir is another checkout, so the project's launch hooks run. |

Can you enumerate the jobs now? Yes: `/delegate`. No: the unknown is itself one job, a scout that returns the map, then `/delegate` over it. A loop over a large file, images or long output (edit, run, inspect, fix) is a job even on one known file, because it would fill the parent's context. The `Agent` tool is denied: Workflows are the one native route. A delegate never dispatches; the parent launches every job of a nested fan-out itself.

**Size each dispatch from its work.** Measure the units, their size, their coupling and their distinct risks, then set at the least that delivers the accepted result: the route; one job per disjoint write set, per unit past one context, or per distinct risk that needs its own reader (a copied roster or a vote count never adds a job); the model and effort that clear the job's remaining ambiguity (§5); and the reading, as paths and hunks, never a whole artifact for a few rows.

**Standing authorization.** Dispatch is opted in for every session: send jobs without asking, within the session's runtime and context. A command that prescribes a fan-out authorizes it.

Before launch, tell the owner one line: the work measure, each job with its model, effort and route, the cheapest shape that would deliver the same result (fewer jobs, a lower pin, inline) and the fact that made this shape beat it. A status of running work names each job by model, task and progress.

## 2. Width

Run independent work together when it cuts elapsed time without hiding integration cost. Keep coupled debugging, one shared design decision, overlapping writes and one voice of prose with one owner: width costs reconciliation even when agents only read. Map everything open (slices, findings, failing tests, files to change) to jobs by write set and dependency, and state the map. Items with disjoint write sets and no shared seam run as concurrent jobs, in worktrees (§7) when they share a checkout. Items sharing a file or a seam serialize. Split a long job at a natural boundary where its growing context would cost more than another job's start; never cap it by turns.

## 4. Briefs

Every brief is open or execute, with `inputs:` and `targets:` lists ([`reference/brief_checks.md`](reference/brief_checks.md)). The launcher or engine adds the delegate rails (`guards/rails.md`) to every job, so a brief never copies them.

## 5. Model and effort

Pin a model and an effort on every job from the table below, by the work shape. Claude rows run as native jobs or on a Claude seat; every other row runs only on the seat `.claude/seats.json` lists for it. Prices, logins and seat limits live in `seats.json` alone.

<!-- model-table-begin -->
| Model | Work shapes it suits | Starting effort | Move when |
|---|---|---|---|
| fable | Running a long session; cross-system seams and the final design verdict; delegate specs | `high`; `medium` for a long session | As a delegate it buys nothing over opus: send opus |
| opus | Architecting and planning; debugging an unknown root cause; an added plan-review seat | `medium` planning, rising as the design deepens; `high` debugging | A lone review, chained research, tight-spec work or a parity refactor: sonnet matches it for less |
| sonnet | The lone holistic review seat; surveys, explore lenses and chained research; scoped architecting; tight-spec execution; a fix whose cause is evident | `medium` tight-spec work and checklist lenses; `high` review and surveys, `xhigh` as the subject grows | A cause hiding behind a second mechanism, or open design: opus. Converged checked work: haiku |
| haiku | Converged and tight-spec execution; extraction; bounded lookups; surveys | `medium`; `low` copyable extraction; `high` surveys | An in-depth defect hunt or a lone review seat: sonnet |
| neon | The cross-family review seat beside a Claude seat; design and agentic coding | `high`; `xhigh` as the problem deepens | Unmeasured here, so never the lone seat: check its cites against source |
| inkling | Copyable reads and doc prose, on the `inkling-local` seat: its answer comes back as text; a run holds the one local GPU, so a local `ai-worker` call waits until it ends | `low`; `seat.sh -e` is required and fixed for the run | Derived work (judgment, synthesis, code) goes to sonnet or above; never a review seat; unmeasured here, so check its output against the source |
<!-- model-table-end -->

- **Add a model:** one row in `seats.json` (a seat row too when it needs a new login or CLI, with that CLI's adapter) and one row here. Its evidence comes from HarnessBench, never from one dispatch. A row states a tendency an orchestrator can act on; the run behind it (a date, a score, a task id, a duration, a ratio, a line citation) stays in HarnessBench (`tests/test_model_table_prose.py`).
- **Clearly defined or ambiguous.** A slice is clearly defined when a check fails before the work and passes only when its unknown is resolved correctly: a failing test, a count against a hand-labeled sample, a diff against a golden file. It takes the tight-spec shape. A slice whose check is subjective, or one a first guess already passes, is ambiguous and takes the row's ambiguous-execution effort. A job takes the pin of its least-scored slice.
- **Fix the brief before raising effort.** When a job under-resolves, find why: a missing input or premise is fixed in the brief; raise effort only on a complete brief; change the model when a raise did not help. High stakes buy verification (a RED-first case, an independent review), never a stronger executor.

## 5b. Capacity

A job goes only to a login whose 5-hour window will last until the job finishes, counting every job already live there. Among the logins that pass, weekly room ranks, and using up a 5-hour window early is fine. Before a fan-out, `/delegate` prints each login's two windows with `tools/capacity.py`; the judgment is yours, and `seat.sh` refuses only a login already at its exhaust floor.

## 7. Worktrees

A job that writes beside other running writers gets its own linked worktree, which the parent creates before launch. A writing seat refuses to start outside one. Cut its branch from the land branch, so the `.claude/` it runs under is current. Concurrent runtime-test lanes never exceed the slot count the project's test harness reports. Retire a worktree per `CLAUDE.core.md` §Shell Discipline.

## 8. After a job returns

- **Read the whole result.** A Workflow's return is a bounded preview: get the complete set with `/session_digest --workflow-dir <transcriptDir>`. A native job that writes leaves its deliverable at `<spillDir>/<label>.md`; a seat leaves `<out.md>`, and a non-zero exit carries the CLI's stderr verbatim.
- **Check its claims.** Run `python3 .claude/tools/quote_check.py <file> --root <checkout>` over a claims or findings file, with the job's checkout as root. Read the cited source for each miss, and keep a claim only when the source supports it. Resolve every cited path in a design or plan deliverable before consuming it. Failed, empty or malformed output leaves its scope uncovered, never clean.
- **Decide findings as one set.** Group them by the decision they touch and look for the one change, often a cut, that answers several. A finding that adds a mechanism names the failure it would have caught and why the existing design cannot answer it; otherwise decline it. A finding that widens or narrows the owner's scope goes to the owner. `/review` §4 caps the rounds.
- **Recover before paying again.** Read the deliverable, then the transcript, and confirm a job ended before replacing it. A dead Workflow resumes with `resumeFromRunId`, which replays completed agents from cache only while the engine script and `guards/rails.md` are byte-identical to the dead run's; after an edit to either, recover from its journal instead.
- **What lands is the parent's.** Read every diff and decide it. A delegate in a worktree committed on its branch; one in the shared checkout returned its changed paths, which the parent commits by pathspec.

## 10. Goal tracking

A long run (a drive, an `/overnight` run, any work with a state file) is steered by its goal's requirements, never its plan's step list: a finished step is not a met requirement. At each checkpoint (a job's return, a compaction, a finished slice), judge each requirement met, partial or not met against current evidence, and say whether the current path still reaches it. Settle an open question with the cheapest check (a probe, a measurement), never another round. A partial or unmet requirement is the next work unless an owner fork blocks it; work the fork does not block still runs. Close only when every requirement reads met or blocked by an owner fork.

## 11. Dispatch doctrine

Dispatch a converged spec (enumerated work with a check: a plan slice, a known follow-on, a verified finding with its exact edit) at the tight-spec shape; the parent keeps the final judgment. Author the check and a first exemplar before batching clones. A job that must wait on a long run (a gate, a build, a main check) stays with the parent: start the run in the background and act on its one wake. The parent wakes once per fan-out: a native Workflow returns once for its set, and a seat fan-out is one background call that exits with its last seat. Count complete-task cost: preparation, cold starts, model work, repair, verification and recovery.
