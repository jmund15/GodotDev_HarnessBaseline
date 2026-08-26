---
description: Pre-PR release-readiness battery — fan out parity, reachability, API-consumption, worklog/roadmap, doc-coverage lenses over the frozen diff (excludes test runs).
allowed-tools: Bash(git diff:*), Bash(git log:*), Bash(git status:*), Bash(git show:*), Bash(git grep:*), Bash(git ls-files:*), Bash(python .claude/hooks/analyze_godot_logs.py:*), Read, Agent, Workflow, mcp__godot__get_godot_version, mcp__godot__run_project, mcp__godot__get_debug_output, mcp__godot__stop_project
---

# /pr_ready — Release-Readiness Battery

Consolidates the "done but not actually done" invariants into one gate at the highest-stakes moment — pre-commit (after `/regression_gate` is green) and pre-PR/pre-merge. Each invariant has shipped as a real regression because it relied on recalling a different memory at the same moment; this turns that tacit discipline into a deterministic checkpoint.

**Does NOT run tests.** `/regression_gate` stays the separate single-flight serial gate. Run this battery AFTER the gate is green. Every lens is static read-only analysis over a frozen diff snapshot, with one exception: Step 0b boots the project once and reads its own startup log — no test suite, no gameplay.

## When to use
- Pre-commit after `/regression_gate` passes, before `/commit_push`.
- Pre-PR before `/create_pr`, or pre-merge before `/merge_pr` — its Step 6b Pre-Merge Checklist carries a BLOCKER row for this battery, so the batch path (`/pr_pipeline` Phase 3 → `/merge_pr`) reaches it too.
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

The two **reachability** lenses are the one exception to push-don't-pull and search for themselves — the reacher can be any authored `.tres`/`.tscn` in the project, so no snapshot can carry it. Their compensation is the stated-probe rule in Step 1b; capture nothing extra for them here.

## Step 0b: Boot-log probe (serial, Claude-side)

The engine diagnoses its own wiring at boot and writes the diagnosis to `godot.log`. Nothing in the PR flow read it, so self-announced defects shipped. This step reads it.

**Scope — boot and first scene load, nothing else.** Launch, let the main scene finish `_Ready`, quit. That exercises static wiring, `[Export]` validation, and singleton/`Instance` registration, which is where self-diagnosing warnings fire. It exercises no gameplay, satisfies no playtest checklist, and replaces no human play.

**Verify the engine pin before launching.** `mcp__godot__get_godot_version` — the MCP runs its OWN binary, and an older one silently downgrades `project.godot` `config/features` and the csproj SDK pin on open (`.claude/rules/godot_files.md`). Version ≠ the project's → do not launch. Report `NOT RUN — engine pin mismatch (MCP <x> vs project <y>)` as a WARN and offer the human path: the user launches the project, then this step resumes at instruction 3 against `--log latest`.

**The engine is machine-wide single-flight.** Run this only after `/regression_gate` is green, never while a test run is live.

1. `mcp__godot__run_project`. Launch truncates `godot.log`, so the log this step reads is exactly this run.
2. `mcp__godot__get_debug_output` until the main scene reports ready (cap at 60s), then `mcp__godot__stop_project`. A failed launch or a boot exception is itself a BLOCKER — report it and skip the rest of the step.
3. `python .claude/hooks/analyze_godot_logs.py --json --mode summary --log latest` for distinct warning/error GROUPS with counts; drill a group with `--mode timeline --target <term> --level warning --json`. Compose with that tool — never hand-parse the log.

### Diff correlation — what makes a boot warning a finding

A pre-existing warning in a subsystem this PR never touched is noise for this PR; a warning naming something the PR changed is a finding. Build the correlation token set Claude-side from the frozen Step 0 diff:

- changed `.cs` basenames plus the type names declared in them
- changed `.tres`/`.tscn` basenames plus their `script_class` values
- the `[Subsystem]` log prefixes emitted by changed files: `git -C "$ROOT" grep -hoE "\[[A-Za-z]+\]" -- <changed .cs paths>`

| verdict | condition | tier |
|---|---|---|
| CORRELATED | a distinct group's text contains any correlation token | **BLOCKER** (`critical: true`) — quote the group verbatim with its count and name the token that matched |
| UNCORRELATED | no token matches | INFO, in the roll-up only |
| NOT RUN | pin mismatch, launch failure, or empty log | WARN, naming which |

A CORRELATED group blocks even when the warning predates the PR: this is the last moment anyone will look at that subsystem with its code in hand.

### Warning budget — advisory

Distinct GROUPS are the health metric, not raw count — repeated-identical warnings collapse to one-with-a-count, which `--mode summary` already does. Compare this run's group signatures against `.claude/state/boot_warning_baseline.json` (`{"recorded_on": "<sha>", "groups": ["<signature>", ...]}`; create it on first run). Report the count delta and list the signatures new since the baseline. No baseline yet → record one from this run and report the raw count only. A run on `main` refreshes the baseline; a run on a branch never writes it.

**Advisory, never blocking**, and a fixed threshold is deliberately absent — it would be a number nobody re-tunes, and a rising count can come from an untouched subsystem or engine churn, so blocking would gate an author on what they cannot fix. Every group that matters is already a BLOCKER by correlation. The budget's job is to give the noise floor an owner and a trend, so the log stays a readable instrument.

## Step 1: Dispatch the battery

**Generation-fidelity guard.** The heaviest lens — `parity` — carries verbatim OLD+NEW `.cs` (escape-dense, nested), the exact payload that makes a single nested `Workflow` `args` blob die at generation (`gotcha_workflow_args_generation_fidelity.md`: 4ms / 0-agent / 0-byte, the throw is `review_fanout.js`'s own `JSON.parse(args)`). So run `parity` as a **standalone `Agent()` call** (one flat prompt — the low-infidelity shape) and the five lighter lenses through `review_fanout`. Triage any 4ms/0-agent/0-byte death as **malformed args, not a broken tool** — re-dispatch that lens as an `Agent()` call.

### Step 1a: parity lens — standalone `Agent()`

```
Agent({
  description: "Refactor parity diff",
  subagent_type: "general-purpose",
  model: "opus", // executor role per the ladder (`reference/model_ladder_evidence.md` §Role guidance) — reasoning-heavy floor (`orchestration` §5): line-precision OLD-vs-NEW regression audit that gates the PR, where a missed dropped branch ships a bug. Standalone Agent() bypasses review_fanout.js's engine floor, so this MUST be set explicitly (else it inherits the session model). If the ladder's role→model mapping changes, this literal follows it.
  prompt: "<parity mandate + OLD (from git show main:…) + NEW per changed .cs, verbatim with file:line>"
})
```

Mandate (`parity`): diff OLD vs NEW per changed method. Flag every dropped/weakened branch, stub, `TODO`, `// deferred`, removed validation, or silently-changed default as a **BLOCKER** (`critical:true`). Quote old vs new verbatim with file:line. Return a JSON findings array (`{agent, action, category, critical, file, description, old, new, rationale}`). This is audit-shape line-precision work — read OLD AND NEW; treat critical findings as candidates Claude verifies, not final truth.

### Step 1b: the other five lenses — `review_fanout`

Keep each `prompt` lean: push the shared diff stat + changed-file list + the 3 pre-computed BLOCKER grep results via `contextPrefix` (NOT repeated per agent).

| key | lens | mandate (findings: action FIX/ASK/PLAN, category bug/rule/improvement, `critical?`, file:line, description, rationale) |
|---|---|---|
| `data-reach` | authored-data reachability | For each behavior-selecting default and each threshold the diff introduces or touches: name the authored `.tres`/`.tscn` that selects the non-default branch or crosses the threshold. Rubric below. |
| `wire-reach` | production-wiring reachability | For each new parameter on an existing signature and each new type the feature needs at runtime: name the PRODUCTION call site or scene that supplies it. Rubric below. |
| `api-consume` | consume-new-APIs | For each new public type/method in the diff: are the motivating call sites migrated, or do old paths still bypass it (the spawn-behaviors-bypass-crafted-pipeline class)? Report unmigrated call sites. |
| `worklog-roadmap` | state reconcile | Against the pushed worklog + roadmap: shipped item still `## Active`? completed Part still plan-pending/in-progress? orphaned `.claude/scratch` from this work? |
| `doc-coverage` | diff doc-coverage | Changed `[Export]` missing `<summary>`? changed subsystem missing a skill/roadmap touch? new public Logic API undocumented? |

#### Reachability rubric — the inert-feature hunt

**Named failure mode — the inert feature:** code that compiles, passes the full gate, and never runs, because no authored data and no production wiring ever selects the branch that enables it. Neither `parity` ("is this code right") nor `api-consume` ("is this symbol referenced") asks the question that finds one: the code IS right and the symbol IS referenced — only its *reacher* is missing. Ask "what authored value reaches this branch", never "is this code correct".

**Production corpus = tracked paths outside the test tree and any prototype/throwaway tree the project excludes from production.** A reacher inside either is not a reacher: a test call site that passes the new parameter, and a test fixture that instantiates the new component, are the two shapes that have already shipped inert features past a green gate.

Candidate shapes, all enumerated from the pushed diff:

| lens | candidate shape | the reacher it must name |
|---|---|---|
| `data-reach` | new or changed `[Export]`/field whose default selects behavior — enum, bool, nullable Resource, strategy slot | an authored `.tres`/`.tscn` setting it to a NON-default value |
| `data-reach` | new threshold, band, or gate — `MinX`/`MaxX`, any comparison against a tunable field | authored values on both sides, plus what fraction of the authored range reaches each branch |
| `wire-reach` | new parameter on an existing method signature | a production call site that supplies it explicitly (a defaulted parameter compiles at every un-migrated site) |
| `wire-reach` | new type, component, or node the feature needs at runtime | a production `.tscn`/`.tres` referencing it by class name or script UID |

One verdict per candidate:
- **UNREACHED** — the probe returned empty. `action: FIX`, `category: bug`, `critical: true`. Word it "no authored reacher found in \<the corpus the probe covered\>", never "dead code": a diff-scoped probe cannot prove global unreachability, and a reader who deletes live surface on this finding has been misled. Put in `new` the authored data that WOULD reach it — file to touch, field, value.
- **NARROW** — the threshold IS crossed, but only by a minority of the authored range (a gate at `MinEnergy = 7.0` against an authored max of `10`). `action: ASK`, `category: bug`, non-critical; give the fraction and both `file:line` values. Reachable-in-principle and invisible-in-play are different states, and this is the only verdict that separates them.
- **INDETERMINATE** — the selecting value is computed at runtime, produced by a generator, or otherwise not expressible as a literal probe. `action: ASK`, `category: bug`, non-critical; name the unknown and who settles it. Leave it INDETERMINATE rather than upgrading it to UNREACHED for a decisive-looking report.
- **REACHED** — no finding of its own; it goes in the coverage roll-up below with its reacher `file:line` and value.

**Every UNREACHED finding states its probe verbatim in `rationale`** — the exact `git grep` whose empty result IS the claim (e.g. `git grep -n "flavor = 1" -- '*.tres' ':!Tests/'`). Claude re-runs that command in Step 2; an unstated or unrunnable probe caps the finding at WARN. This is what keeps the lens falsifiable, and it is the mitigation for `gotcha_workflow_fanout_search_false_absence` — a fanned agent's empty `Grep` is a candidate, never a verdict.

**Each reachability lens returns exactly one coverage roll-up finding** (`action: PLAN`, `category: improvement`, non-critical, `file: null`): candidates enumerated, and the REACHED ones listed with reacher `file:line`. A lens that enumerated zero candidates reports that in the roll-up, so "no findings" stays distinguishable from "nothing examined".

```
Workflow({
  scriptPath: ".claude/workflows/review_fanout.js",
  args: {
    contextPrefix: "<diff --stat + changed-file list + the 3 pre-computed BLOCKER grep results>",
    agents: [
      { key: "data-reach",      prompt: "<rubric + changed [Export]/field declarations with defaults + threshold comparisons, verbatim with file:line>", model: "sonnet", effort: "medium" },
      { key: "wire-reach",      prompt: "<rubric + OLD+NEW signature lines (from the Step 0 parity capture) + new type declarations, verbatim with file:line>", model: "sonnet", effort: "medium" },
      { key: "api-consume",     prompt: "<mandate + new-public-API list + call-site content>", model: "sonnet" },
      { key: "worklog-roadmap", prompt: "<mandate + worklog-titles + roadmap Parts table>", model: "sonnet" },
      { key: "doc-coverage",    prompt: "<mandate + changed-export/subsystem content>", model: "sonnet" }
    ]
  }
})
```

Both reachability lenses pin `sonnet/medium` — the engine's defaults, restated in the args so the pin is visible at the dispatch site. They are read-heavy enumeration against a rubric that resolves every classification decision up front, which is the ladder's sonnet profile; residual ambiguity at dispatch is what buys effort, and there is none left to buy. Send OLD+NEW *signature lines* only, never whole methods — `parity` already owns method bodies, and duplicating them here walks back into the generation-fidelity envelope.

`api-consume` is line-precision work — demand verbatim file:line. Treat highest-stakes (critical) findings as candidates Claude verifies, not final truth.

## Step 2: Present BLOCKER / WARN / INFO

`review_fanout` returns deduped+sorted findings; merge in the standalone `parity` Agent()'s findings, the three Claude-side grep BLOCKER candidates, and the Step 0b boot-log verdicts.

**Re-run every UNREACHED probe before presenting it.** Take the `git grep` the finding quoted, run it Claude-side, and let the result set the tier: still empty → BLOCKER; returns a production hit → INFO, naming the reacher the lens missed; no probe stated, or it does not run → WARN, saying which. Never promote an UNREACHED on the lens's word alone — the whole finding rests on an absence, and an absence one intermittent `Grep` produced is not evidence.

Present as:
- **BLOCKERS** (critical / parity-dropped-branch / framework-leak / leftover `[DIAG-]` / probe-confirmed UNREACHED / CORRELATED boot warning) — must resolve before PR.
- **WARN** (unmigrated call site, state drift, missing doc-coverage, NARROW / INDETERMINATE / unverified reachability, boot-log probe NOT RUN).
- **INFO** (minor; reachability coverage roll-ups; UNCORRELATED boot groups and the warning-budget delta).

**Never average across lenses, and issue no roll-up verdict.** Each lens reports its own outcome, set by its worst surviving finding — a clean `doc-coverage` does not soften a `parity` BLOCKER, and "5 of 6 lenses clean" is not a result. State each lens's worst finding separately.

Then run the user-gated walkthrough per `agents/orchestrator_action_protocol.md` (Step 1.5 verify → FIX/ASK/PLAN per finding). Do NOT auto-run tests or auto-commit.
