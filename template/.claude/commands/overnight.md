---
description: Run a goal UNATTENDED to completion — no questions, confident calls made and logged, the rest parked in a dated Close doc for the user's return.
---

# /overnight — Unattended run to completion

The user is leaving for hours. Every question you would normally ask has no one to answer it, and a session that ends on one has delivered nothing. This command turns the goal into an unattended run: decide what you can defend, park what you cannot, and finish everything that does not depend on a parked item.

## Arguments

`/overnight <goal>` — the goal text, verbatim. No argument → run `python3 .claude/hooks/overnight_ask_guard.py --status`; resume its goal only when `armed` is true. Missing or invalid `CLAUDE_CODE_SESSION_ID` → stop with the error, never claim another session's goal.

Pair it with `/goal <same text>` so the Stop hook blocks an early exit. This command owns the conduct; the host built-in `/goal` owns the persistence — never search the repository for a goal file. Treat host-goal state as evidence only when the owner invoked or queried `/goal`; otherwise record `HOST_GOAL_UNVERIFIED` and claim no cross-turn persistence. **The goal is met by an artifact, never by self-report**: a condition phrased as confidence ("when you are fully confident in X") is met only when the mechanical check that owns X is green and its output line is pasted in the Close doc (`bench.py gate` for the benchmark, the project's regression gate for code as `change_control` §Gate cadence names it, `harness_tests.py` for the harness); a red line or a missing one means the goal stays unmet and the run continues or parks.

## Step 1 — Arm

```bash
python3 .claude/hooks/overnight_ask_guard.py --arm "<goal>"
```

Writes `.claude/scratch/overnight/active-<session-id>.json` atomically. The hook checks the payload's session identity and the marker's goal and timestamp; only that session's valid marker under 16 h old denies questions. Done: `--status` reports `armed: true` and the exact goal. Other sessions' markers and anonymous legacy state stay untouched.

## Step 2 — Decide or park (the one rule)

Every fork that would have been a question routes here — gate FAIL adjudication (the project's regression gate), pre-merge `[ ]` items (the PR merge checklist), worklog confirm-prompts, plan-file forks, "A or B?" design calls.

**Decide** when all three hold: you would have marked the option "(Recommended)"; the action is reversible from the branch (a commit, a file edit, a fast-forward push of the active branch, including `main`, after fresh evidence and ownership checks), or an owner prompt in this session already authorized that action class ("publish when green", "the sync is finished by session end") and its gate is green; it stays inside the goal's scope. Scope is the `/overnight` text plus every standing directive and requirement the owner stated earlier in the session. A fix that brings delivered work up to a stated requirement is in scope. Record it as `D<n>` in the Close doc with the one-line reason.

**Park** everything else — irreversible actions no owner prompt authorizes (merge, delete, force-push, publishing another branch, external publish), scope changes, forks with no confident recommendation, and any step that fails the same way twice. An `irreversible` Park names the owner prompts checked for authorization. An environment kill (low memory, timeout, dropped connection) is not a step failure: rerun it once before counting it. Record it as one parseable `Q<n>` row, then do everything that does not depend on it:

`**Q<n> — <fork>. Options: <options>. Park: <irreversible|out-of-scope|no-recommendation|failed-twice> — <evidence>. Recommendation: <option|none>.`

**A `Q` row whose Park evidence does not hold is a Decide row that was not applied.** The doc holds at most 5 `Q` rows: writing a sixth means re-reading the five and applying every one that fails no Park test.

`Known issue` is never an unattended verdict: a red test is fixed (Decide) or parked (Q), never waved through.

## Step 3 — Run

- Use Bash `run_in_background` for one terminal notification; use `Monitor` for repeated events, including failure states. Follow the live tool's lifetime contract. Detach separately only when required and supported; idle alone is not cancellation evidence.
- After compaction, run `--status` and read this session's Close doc before acting.
- Same failure twice on one step → park it (Step 2) and move on. Never loop on a nudge or a denial.

## Step 4 — Close

Close doc: `DevProjects/{{PROJECT_NAME}}/Claude/TODO/Overnight/<YYYY-MM-DD>-<session-id>.md` (vault; direct `Write`). Use the full session id from `--status`; inspect an existing target first and never overwrite another session's report. Sections: **Outcome**, optional **Decisions made**, **Decisions for you**, **Left undone**. Omit empty Decisions made; write the other three even when empty.

**Close-doc contract.** This is an action index, not a session record. Outcome: one row per deliverable with one proof, plus `Resume: /session_digest <full-session-id>` and `Session digest: logs/session_digest_<sid8>.json`. Decisions made: every applied `D<n>` with its one-line reason. Each `Q`: the Step 2 row. Left undone: `Q`-blocked work only. One sentence per item; no other sections. Delete chronology, discovery/root-cause stories, fixed mistakes, audit coverage, raw logs, full test or commit lists and duplicated evidence; link the owner instead. Delete any row that neither changes the next action nor proves delivery.

Then validate and disarm; a nonzero validate leaves the marker armed, so fix the doc and rerun:

```bash
python3 .claude/hooks/overnight_ask_guard.py --validate-close <close-doc-path>
python3 .claude/hooks/overnight_ask_guard.py --disarm
```

Final message = the Close doc path, its Outcome and `Q` list, opening with the outcome. Every headline number carries its coverage — *of N pins run; never run: …* — and its spread when reps exist; a table without a coverage column is a claim the reader cannot weigh. A run with open `Q` items is complete when every non-dependent step is done and the doc names each blocker.

## No-op

Goal already complete when invoked → write the Close doc with an empty `Q` list, disarm, stop.
