---
description: Send scoped jobs to native Workflow agents or external seats, each with its model and effort pinned
argument-hint: <task> | <jobs.json>
---

# Delegate

The one dispatch command. A native job runs as a `dispatch.js` Workflow agent on the session's own login; a seat job runs on another login or model family through `seat.sh`. `orchestration` picks the route (§0), the width (§2), the model and effort (§5); this page carries them out.

## Arguments

- `<task>`: one job. Write its brief first.
- `<jobs.json>`: a native jobs file (step 3).
- No argument: print these two lines and stop. A brief, input or output directory that does not resolve is named, and nothing launches.

## 1. Write each brief

Each brief is open or execute, with its `inputs:` and `targets:` lists (`skills/orchestration/reference/brief_checks.md`), in `.claude/scratch/<run>/<label>.brief.md`. The engine or launcher adds `guards/rails.md`; never copy it into a brief.

## 2. Read capacity before a fan-out

Run `python3 .claude/tools/capacity.py <seat>` for each seat the fan-out uses, and `python3 .claude/tools/capacity.py session` for native jobs on the session's own login. Each prints the login's 5-hour and 7-day used percent and reset time. Place each job by `orchestration` §5b and put the windows in the pre-launch line (§0). With no seat in `.claude/seats.json`, the session line is the only read.

## 3. Launch

**Native.** Write the jobs file and issue one `Workflow` call to `.claude/workflows/dispatch.js` with args `{"jobsFile": "<path>"}`:

```json
{"spillDir": ".claude/scratch/<run>/",
 "jobs": [{"label": "<label>", "promptPath": "<brief.md>", "model": "<model>", "effort": "<effort>", "kind": "<kind>"}]}
```

`kind` names the job's tool profile in `guards/tool_profiles.json`: a job whose deliverable is a file it writes is an `author`; a read-only job is a `scout`, `lens` or `reviewer`. For dependent hops with no judgment between them, replace `jobs` with `"chains": [{"name": "<lane>", "jobs": [...]}]`: lanes run in parallel and each lane runs its jobs in order. Args carry paths and short scalars only, because a large multi-line argument fails the permission check.

**Seat.** One Bash call with `run_in_background`. One seat: `bash .claude/scripts/seat.sh <seat> <brief.md> <out.md>`. A fan-out puts every seat in the same call, which exits when the last seat does and fails if any seat failed, so the session wakes once:

```bash
pids=""
bash .claude/scripts/seat.sh <seat> <a.brief.md> <a.out.md> & pids="$pids $!"
bash .claude/scripts/seat.sh <seat> <b.brief.md> <b.out.md> & pids="$pids $!"
fail=0; for p in $pids; do wait $p || fail=1; done; exit $fail
```

A writing seat starts from inside its own linked worktree (`orchestration` §7). A set that mixes routes issues the `Workflow` call and the seat call in one message.

### How the routes differ

| Behaviour | Native | Seat | Why |
|---|---|---|---|
| Unresolved inputs; a read-only job naming another target | refused per job at the Workflow entry (`tools/launch_check.py`) | refused before spawn, same module | one check, two callers |
| Project hooks `dispatch.pre_launch` / `dispatch.post_return` (`adaptation.json`) | before each job at the entry; after the Workflow returns, once per job, at the entry's PostToolUse mode (a refusal fails that job) | before spawn; after the CLI exits 0, in the job's workdir | one module and one hook; the route name passed is `native` or the seat, and the out file is `<spillDir>/<label>.md` or the seat's `<out.md>` |
| Own worktree, `max_concurrent`, login at its exhaust floor | not checked | refused before spawn | they govern a CLI process and its login; the engine queues past its own concurrency cap, and the session's login is judged in step 2 |
| Workdir in another checkout | never | the only route | the project's launch hooks must run on it |
| Floor | the session's hooks run in every agent | a writing Claude seat runs the project's settings; a read-only seat returns on stdout and cannot write | the CLI decides what runs inside it |
| Rails and wait checks | `guards/rails.md` injected by the engine; the session's `hooks/foreground_wait_guard.py` | `guards/rails.md` and the same guard, armed by the launcher | same text and checks, each route's channel |
| Wake | the Workflow returns once for its set | the background call exits with its last seat | same |

## 4. Take back

- **Native:** the Workflow's return is a preview. Read each `<spillDir>/<label>.md`, and the complete set with `/session_digest --workflow-dir <transcriptDir>`.
- **Seat:** read `<out.md>`. A non-zero exit carries the CLI's stderr verbatim: read it before any relaunch. A writing seat committed on its worktree's branch.
- Then `orchestration` §8: check claims with `quote_check.py`, read every diff, decide what lands, and commit by pathspec. Stop a running seat only through `python3 .claude/tools/reap.py`.
