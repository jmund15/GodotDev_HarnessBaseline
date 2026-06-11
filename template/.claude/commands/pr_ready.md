---
description: Pre-PR release-readiness battery — fan out parity, API-consumption, worklog/roadmap, doc-coverage lenses over the frozen diff (excludes test runs).
allowed-tools: Bash(git diff:*), Bash(git log:*), Bash(git status:*), Bash(git show:*), Bash(git grep:*), Bash(git ls-files:*), Read, Workflow
---

# /pr_ready — Release-Readiness Battery

Consolidates the "done but not actually done" invariants into one gate at the highest-stakes moment — pre-commit (after `/regression_gate` is green) and pre-PR/pre-merge. Each invariant has shipped as a real regression because it relied on recalling a different memory at the same moment; this turns that tacit discipline into a deterministic checkpoint.

**Does NOT run tests.** `/regression_gate` stays the separate single-flight serial gate. This battery is static read-only analysis over a frozen diff snapshot. Run it AFTER the gate is green.

## When to use
- Pre-commit after `/regression_gate` passes, before `/commit_push`.
- Pre-PR before `/create_pr`, or pre-merge before `/merge_pr`.
- Skip for meta-only commits (`.claude/`, docs) — the parity/API lenses have nothing to chew on.

## Step 0: Assemble the frozen snapshot (Claude-side — push-don't-pull)

The fanned agents must NOT discover files themselves (intermittent `Grep`/`Glob` empties read as false-absence under fan-out — see `gotcha_workflow_fanout_search_false_absence.md`). Claude assembles the snapshot via `git` (reliable) and pushes it into every agent prompt. Keep what each agent receives lean — pushing verbatim code into many nested agent prompts is the `gotcha_workflow_args_generation_fidelity.md` death case (see the generation-fidelity guard in Step 1).

Scope: `$ARGUMENTS` may give a range; else default to `main...HEAD` (three-dot — diff from the merge-base, i.e. what this branch *added*; two-dot `main..HEAD` diffs endpoint-to-endpoint and mis-scopes a stale branch) plus uncommitted working-tree changes. Malformed range → fall back to the default and say so.

```bash
ROOT="$(git rev-parse --show-toplevel)"
git -C "$ROOT" diff --stat main...HEAD
git -C "$ROOT" diff main...HEAD            # full hunks
git -C "$ROOT" diff                        # uncommitted
git -C "$ROOT" status --porcelain
```

**No-op gate:** if `diff --stat main...HEAD` AND `status --porcelain` are both empty, there is nothing to review — report "clean, nothing to gate" and exit without dispatching agents.

For the **parity** lens, also capture the OLD version of each changed method/file so the agent can diff behavior, not just read the new code:
```bash
git -C "$ROOT" show main:<path>            # per changed .cs file
```

Run the three **cheap BLOCKER greps Claude-side** (do NOT spend an agent on them) and fold results into the snapshot as pre-computed BLOCKER candidates:
```bash
git -C "$ROOT" grep -nE "\[DIAG-|GD\.Print" -- $(changed .cs paths)        # leftover diagnostics
git -C "$ROOT" grep -nE "{{PROJECT_NAME}}\." -- 'Jmodot/**/*.cs'               # framework-boundary leak
git -C "$ROOT" status --porcelain -- .claude/scratch                        # orphaned scratch
```

Also read into the snapshot: `.claude/worklog-titles.md` (worklog state) and the relevant topic-folder `roadmap.md` Parts table (roadmap state) for the reconcile lens.

## Step 1: Dispatch the battery

**Generation-fidelity guard.** The heaviest lens — `parity` — carries verbatim OLD+NEW `.cs` (escape-dense, nested), the exact payload that makes a single nested `Workflow` `args` blob die at generation (`gotcha_workflow_args_generation_fidelity.md`: 4ms / 0-agent / 0-byte, the throw is `review_fanout.js`'s own `JSON.parse(args)`). So run `parity` as a **standalone `Agent()` call** (one flat prompt — the low-infidelity shape) and the three lighter lenses through `review_fanout`. Triage any 4ms/0-agent/0-byte death as **malformed args, not a broken tool** — re-dispatch that lens as an `Agent()` call.

### Step 1a: parity lens — standalone `Agent()`

```
Agent({
  description: "Refactor parity diff",
  subagent_type: "general-purpose",
  prompt: "<parity mandate + OLD (from git show main:…) + NEW per changed .cs, verbatim with file:line>"
})
```

Mandate (`parity`): diff OLD vs NEW per changed method. Flag every dropped/weakened branch, stub, `TODO`, `// deferred`, removed validation, or silently-changed default as a **BLOCKER** (`critical:true`). Quote old vs new verbatim with file:line. Return a JSON findings array (`{agent, action, category, critical, file, description, old, new, rationale}`). This is audit-shape line-precision work — read OLD AND NEW; treat critical findings as candidates Claude verifies, not final truth.

### Step 1b: the other three lenses — `review_fanout`

Keep each `prompt` lean: push the shared diff stat + changed-file list + the 3 pre-computed BLOCKER grep results via `contextPrefix` (NOT repeated per agent).

| key | lens | mandate (findings: action FIX/ASK/PLAN, category bug/rule/improvement, `critical?`, file:line, description, rationale) |
|---|---|---|
| `api-consume` | consume-new-APIs | For each new public type/method in the diff: are the motivating call sites migrated, or do old paths still bypass it (the spawn-behaviors-bypass-crafted-pipeline class)? Report unmigrated call sites. |
| `worklog-roadmap` | state reconcile | Against the pushed worklog + roadmap: shipped item still `## Active`? completed Part still plan-pending/in-progress? orphaned `.claude/scratch` from this work? |
| `doc-coverage` | diff doc-coverage | Changed `[Export]` missing `<summary>`? changed subsystem missing a skill/roadmap touch? new public Logic API undocumented? |

```
Workflow({
  scriptPath: ".claude/workflows/review_fanout.js",
  args: {
    contextPrefix: "<diff --stat + changed-file list + the 3 pre-computed BLOCKER grep results>",
    agents: [
      { key: "api-consume",     prompt: "<mandate + new-public-API list + call-site content>" },
      { key: "worklog-roadmap", prompt: "<mandate + worklog-titles + roadmap Parts table>" },
      { key: "doc-coverage",    prompt: "<mandate + changed-export/subsystem content>" }
    ]
  }
})
```

`api-consume` is line-precision work — demand verbatim file:line. Treat highest-stakes (critical) findings as candidates Claude verifies, not final truth.

## Step 2: Present BLOCKER / WARN / INFO

`review_fanout` returns deduped+sorted findings; merge in the standalone `parity` Agent()'s findings and the three Claude-side grep BLOCKER candidates. Present as:
- **BLOCKERS** (critical / parity-dropped-branch / framework-leak / leftover `[DIAG-]`) — must resolve before PR.
- **WARN** (unmigrated call site, state drift, missing doc-coverage).
- **INFO** (minor).

Then run the user-gated walkthrough per `agents/orchestrator_action_protocol.md` (Step 1.5 verify → FIX/ASK/PLAN per finding). Do NOT auto-run tests or auto-commit.
