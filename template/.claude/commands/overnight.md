---
description: Run a goal UNATTENDED to completion — no questions, confident calls made and logged, the rest parked in a dated decisions doc for the user's return.
---

# /overnight — Unattended run to completion

The user is leaving for hours. Every question you would normally ask has no one to answer it, and a session that ends on one has delivered nothing. This command turns the goal into an unattended run: decide what you can defend, park what you cannot, and finish everything that does not depend on a parked item.

## Arguments

`/overnight <goal>` — the goal text, verbatim. No argument → run `python3 .claude/hooks/overnight_ask_guard.py --status`; resume its goal only when `armed` is true. Missing or invalid `CLAUDE_CODE_SESSION_ID` → stop with the error, never claim another session's goal. Legacy anonymous `active.json` is not owned state.

Pair it with `/goal <same text>` so the Stop hook blocks an early exit. This command owns the conduct; `/goal` owns the persistence. **The goal is met by an artifact, never by self-report**: a condition phrased as confidence ("when you are fully confident in X") is met only when the mechanical check that owns X is green and its output line is pasted in the Close doc (the instrument's own gate for a benchmark, `/regression_gate` for code, `harness_tests.py` for the harness); a red line or a missing one means the goal stays unmet and the run continues or parks.

## Step 1 — Arm

```bash
python3 .claude/hooks/overnight_ask_guard.py --arm "<goal>"
```

Writes `.claude/scratch/overnight/active-<session-id>.json` atomically. The hook checks the payload's session identity and the marker's goal and timestamp; only that session's valid marker under 16 h old denies questions. Done: `--status` reports `armed: true` and the exact goal. Other sessions' markers and anonymous legacy state stay untouched.

## Step 2 — Decide or park (the one rule)

Every fork that would have been a question routes here — gate FAIL adjudication (`/regression_gate`), pre-merge `[ ]` items (`/merge_pr`), worklog confirm-prompts, plan-file forks, "A or B?" design calls.

**Decide** when all three hold: you would have marked the option "(Recommended)"; the action is reversible from the branch (a commit, a file edit, a push to a feature branch); it stays inside the goal's stated scope. Record it as `D<n>` in the decisions doc with the one-line reason.

**Park** everything else — irreversible actions (merge, push to main, delete, force-push, external publish), scope changes, forks with no confident recommendation, and any step that fails the same way twice. Record it as `Q<n>`: the fork, the options, your recommendation if you have one, and what you finished around it. Then do everything that does not depend on it.

**A row that carries "I recommend O1" is a Decide row that was not applied**, unless the one sentence beside it names the Park test it fails (irreversible, out of scope, no recommendation, failed twice). The doc holds at most 5 `Q` rows: writing a sixth means re-reading the five and applying every one that fails no Park test — overflow is the signal the rule is not being run, never a reason for a longer doc.

`Known issue` is never an unattended verdict: a red test is fixed (Decide) or parked (Q), never waved through.

## Step 3 — Run

- Use Bash `run_in_background` for one terminal notification; use `Monitor` for repeated events, including failure states. Follow the live tool's lifetime contract. Detach separately only when required and supported; idle alone is not cancellation evidence.
- After compaction, run `--status` and read this session's decisions doc before acting; never resume a peer's goal.
- Same failure twice on one step → park it (Step 2) and move on. Never loop on a nudge or a denial.

## Step 4 — Close

Decisions doc: `DevProjects/{{PROJECT_NAME}}/Claude/TODO/Overnight/<YYYY-MM-DD>-<session-id>.md` (vault; direct `Write`, findings-shaped). Use the full session id from `--status`; inspect an existing target before updating and never overwrite another session's report. Sections in order: **Outcome** (what is done, verified how), **Decisions made** (`D1…`), **Decisions for you** (`Q1…`, each with options + recommendation), **Left undone** (blocked on which `Q`). Write it even when `Q` is empty — the user reads it before anything else.

Then disarm:

```bash
python3 .claude/hooks/overnight_ask_guard.py --disarm
```

Final message = the decisions doc's Outcome and `Q` list, opening with the outcome. Every headline number carries its coverage — *of N pins run; never run: …* — and its spread when reps exist; a table without a coverage column is a claim the reader cannot weigh. A run with open `Q` items is complete when every non-dependent step is done and the doc names each blocker.

## No-op

Goal already complete when invoked → write the Close doc with an empty `Q` list, disarm, stop.
