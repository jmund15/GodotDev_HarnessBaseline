---
description: Run a goal unattended; decide or park every fork
---

# /overnight — Unattended run to completion

**Input:** the goal text, or no argument to resume the armed goal through `--status`.
**Enters at:** intake of the goal's own triage.
**Owner stop:** none until the owner's next prompt; Step 2 turns every would-be stop into a Decide or Park row.

The user is leaving for hours. Every question you would normally ask has no one to answer it, and a session that ends on one has delivered nothing. This command turns the goal into an unattended run: decide what you can defend, park what you cannot, and finish everything that does not depend on a parked item.

## Arguments

`/overnight <goal>` — preserve the meaningful goal; arming trims surrounding whitespace. No argument → run `python3 .claude/hooks/overnight_ask_guard.py --status`; resume its goal only when `armed` is true. Missing or invalid `CLAUDE_CODE_SESSION_ID` → stop with the error, never claim another session's goal.

Pair it with `/goal <same text>` so the Stop hook blocks an early exit. This command owns the conduct; the host built-in `/goal` owns the persistence — never search the repository for a goal file. Treat host-goal state as evidence only when the owner invoked or queried `/goal`; otherwise record `HOST_GOAL_UNVERIFIED` in the Close doc and claim no cross-turn persistence. **The goal is met by an artifact, never by self-report**: a condition phrased as confidence ("when you are fully confident in X") is met only when the mechanical check that owns X is green and its output line is pasted in the Close doc (`bench.py gate` for the benchmark, the project's regression gate for code as `change_control` §Gate cadence names it, `harness_tests.py` for the harness); a red line or a missing one means the goal stays unmet and the run continues or parks.

## Step 1 — Arm

**Departure sweep.** Before arming, list the forks the run can already foresee: decisions the goal text or its plan leaves open, and each goal step whose triage reversibility class is none (a force-push, an external publish, a push of another branch). Skip a step that Step 2 already decides: one an owner prompt in this session authorized by action class, with its gate green. With none, arm now. Otherwise ask them as packets (`CLAUDE.core.md` §Preferences) in one batch of `AskUserQuestion` calls, at most four questions per call (the tool's capacity), then arm. The answers join the run's scope (Step 2). A fork found after arming goes to Step 2.

```bash
python3 .claude/hooks/overnight_ask_guard.py --arm "<goal>"
```

Writes `.claude/scratch/overnight/active-<session-id>.json` atomically. The hook checks the payload's session identity and the marker's goal and timestamp; only that session's valid marker under 16 h old denies questions (`hooks/overnight_ask_guard.py` enforces `TTL_HOURS = 16`). The arming message tells the owner the goal, what completes it and the wall time it needs, in plain words; an internal label never reaches the owner. Estimate that time from the plan's jobs, the `durationMs` of recent sidecar records of the same job shape and the proof runs in `.claude/logs/harness_runs.json`; when it runs past the owner's expected return, name the part that fits. Restate it when the owner adds scope to an armed run. Done: `--status` reports `armed: true` and the exact goal, and the arming message states the wall time. Other sessions' markers and anonymous legacy state stay untouched.

## Step 2 — Decide or park (the one rule)

Every fork that would have been a question routes here — gate FAIL adjudication (the project's regression gate), pre-merge `[ ]` items (the PR merge checklist), worklog confirm-prompts, plan-file forks, "A or B?" design calls, and any hook or command step that says to ask (a classifier-denial coach, `/session_end`'s owner-only forks). The run stays unattended until the owner's next prompt; no step of yours ends it.

**Decide** when all three hold:

- you would have marked the option "(Recommended)";
- its triage reversibility class is clean or residue, or an owner prompt in this session authorized its action class ("publish when green", "the sync is finished by session end", an `/overnight` arming that grants it) and that class's gate is green. A fast-forward push of the active branch, including `main`, follows its class: a push of `main` is decidable only under such a prompt, after fresh evidence and ownership checks. Landing on `main` a branch or PR this run built for in-scope work is clean or residue (a revert commit undoes it): it is Decide once its gate is green and no peer holds its files, so the owner wakes to landed work, not a merge queue;
- it stays inside the goal's scope. Scope is the `/overnight` text plus every standing directive and requirement the owner stated earlier in the session. A fix that brings delivered work up to a stated requirement is in scope.

Record it as `D<n>` in the Close doc with the one-line reason.

A deletion is reversible when one of these proves it, with the proof pasted in `D<n>`:

- **Worktree:** follow `CLAUDE.core.md` §Shell Discipline for ownership and removal authority. Verify the resolved full path with the session's worktree-ownership helper (`session_created_worktree`). Check the worktree and each submodule for peer work; uncertain ownership or peer work parks removal. Preserve delivered commits and wanted files in repositories or artifacts outside the directory being removed, including submodule commits held only in its local object store. Paste the ownership proof, resolved path and peer-work check in `D<n>`. Remove it with `git worktree remove <path>`. Unpushed commits alone are not a blocker when preserved.
- **Remote branch:** this session's push output or `D<n>` row records the push, `git fetch` succeeds, `gh pr list --head <branch> --state open` is empty, and `git cherry origin/main origin/<branch>` exits 0 with no `+` row. A cloud session still working on the branch goes undetected; its commits are already in `origin/main`.

**Park** everything else: irreversible actions no owner prompt authorizes (a delete neither form above proves, force-push, publishing another branch, external publish); an owner-domain fork, meaning taste or appetite or an approval its command reserves to the owner, even when reversible; scope changes; forks with no confident recommendation; and any step whose rerun reproduces the same failure with no new diagnostic information (`Park: failed-twice`). An `irreversible` Park names the owner prompts checked for authorization. An environment kill (low memory, timeout, dropped connection) is not a step failure: rerun it to separate it from a real failure before counting it. Record it as one parseable `Q<n>` row, then do everything that does not depend on it:

`**Q<n> — <fork>. Options: <options>. Park: <irreversible|out-of-scope|owner-domain|no-recommendation|failed-twice> — <evidence>. Recommendation: <option|none>. Default: <what holds while it stays unanswered>.`

A merge parks only on a failing Park test its evidence names (a red gate, a peer on the same files, an owner-domain fork); the word "merge" is not one.

The optional `Reversibility: <clean|residue|none>` segment (the recommended option's class) goes between Recommendation and Default when `CLAUDE.core.md` §Preferences' recovery-consequence condition holds. Default is required independently and is the state the run leaves while the owner has not answered, such as "nothing merged; the branch stays".

**A `Q` row whose Park evidence does not hold is a Decide row that was not applied.** Before writing a `Q` row, re-check every open `Q` row against the Park tests and apply each one that fails none.

**No-regret list.** When every remaining goal step waits on a `Q` row or on a running job, work from items whose value holds whatever each open `Q` row's answer or each job's result is: evidence that settles a parked fork, verification of delivered work (the candidate review of committed work the job cannot change, `/session_audit --files <paths> --task <id> --plan <path>`, included), and in-scope steps no answer or result changes. Work whose value depends on an answer or result waits for it.

`Known issue` is never an unattended verdict: a red test is fixed (Decide) or parked (Q) out of the shared tree (`CLAUDE.core.md` §Core Principles), never waved through.

## Step 3 — Run

- Use Bash `run_in_background` for one terminal notification; use `Monitor` for repeated events, including failure states. Follow the live tool's lifetime contract. A running sidecar is watched by `python3 .claude/tools/watch_records.py --session --wake` in the background (`reference/sidecar_dispatch.md` §Procedure), never by its launch's own shell, so no sidecar goes unwatched when the turn ends. Detach separately only when required and supported; idle alone is not cancellation evidence.
- Files the goal must change hold uncommitted work of a session that is not done (`CLAUDE.core.md` §Core Principles) → work in a worktree (`git worktree add -b <branch> .claude/worktrees/<slug> main`); once that work lands, rebase the branch onto `main`, then fast-forward `main` to it. Never wait idle on a peer's commit.
- After compaction, run `--status` and read this session's Close doc before acting.
- A step whose rerun reproduces the same failure with no new diagnostic information → park it (Step 2) and move on. Never loop on a nudge or a denial.

## Step 4 — Close

Close runs after every goal step, including a `/session_end` the goal names: a named closeout never replaces a runnable step. Before Close, list each remaining step of the goal's plan with its dependency, citing the plan's dependency table when it has one; a step whose dependencies have landed runs now, and only a step a `Q` row blocks may stay undone. First close out dispatch ratings: `/orchestration_metrics` §Incremental rating, *Phase close*. A `Q` row is for an absent owner. Any owner message disarms the run; when it leaves the run going (a side request or status check), re-arm with the same goal, which the disarm line prints. Once the owner's prompt has ended the run, ask each open fork as a live question packet (`CLAUDE.core.md` §Preferences), and a Close doc written after that records only what was delivered.

Close doc: `DevProjects/{{PROJECT_NAME}}/Claude/TODO/Overnight/<YYYY-MM-DD>-<session-id>.md` (vault; direct `Write`). Use the full session id from `--status`; inspect an existing target first and never overwrite another session's report. Sections: **Outcome**, optional **Decisions made**, **Decisions for you**, **Left undone**. Omit empty Decisions made; write the other three even when empty.

**Close-doc contract.** This is an action index, not a session record. Outcome: one row per deliverable with one proof, plus `Resume: /session_digest <full-session-id>` and `Session digest: logs/session_digest_<sid8>.json`. When the doc holds a `Q` row, Outcome also holds `Next session: <currency> · <model>`: the `transport` that `python3 .claude/tools/provider_bands.py pick orchestrator --json` returns, and the orchestrator ladder row that `python3 .claude/tools/model_registry.py for-role orchestrator` lists on that transport. Decisions made: every applied `D<n>` with its one-line reason. Each `Q`: the Step 2 row. Left undone: `Q`-blocked work only, one row each, `- <work> — blocked by Q<n> — worklog: <title>`; first `/worklog add` that item with `When: after <sid8> Q<n>`, so `/overnight_review` unblocks it after successful application of the answer. A `Resolved: <date> — <answer|obsolete> — <evidence>` line directly under a `Q` row closes it; `/overnight_review` writes it. One sentence per item; no other sections. Delete chronology, discovery/root-cause stories, fixed mistakes, audit coverage, raw logs, full test or commit lists and duplicated evidence; link the owner instead. Delete any row that neither changes the next action nor proves delivery.

Then validate it; on nonzero, fix the doc and rerun. Rerun after any later edit to the doc. The marker stays armed; the owner's next prompt disarms it (`prompt_dispatch` runs the guard on UserPromptSubmit).

```bash
python3 .claude/hooks/overnight_ask_guard.py --validate-close <close-doc-path>
```

Final message = the Close doc path, its Outcome and `Q` list as text, opening with the outcome, and `/overnight_review` as the route for answering the `Q` list. Every headline number carries its coverage — *of N pins run; never run: …* — and its spread when reps exist; a table without a coverage column is a claim the reader cannot weigh.

## No-op

Goal already complete when invoked → write the Close doc with an empty `Q` list, validate it, stop.
