---
description: Audit release readiness before a PR; the regression gate runs the tests
allowed-tools: Write, Bash(python3 .claude/tools/review_ledger.py:*), Bash(git diff:*), Bash(git log:*), Bash(git status:*), Bash(git show:*), Bash(git grep:*), Bash(git ls-files:*), Bash(python .claude/hooks/analyze_godot_logs.py:*), Read, Agent, Workflow, mcp__godot__get_godot_version, mcp__godot__run_project, mcp__godot__get_debug_output, mcp__godot__stop_project
---

# /pr_ready — Release-Readiness Battery

Consolidates the "done but not actually done" invariants into one gate at the highest-stakes moment — pre-commit (after the project's regression gate is green) and pre-PR/pre-merge. Each invariant has shipped as a real regression because it relied on recalling a different memory at the same moment; this turns that tacit discipline into a deterministic checkpoint.

**Does NOT run tests.** The project's regression gate (`change_control` §Gate cadence names it) stays the separate single-flight serial gate. Run this battery AFTER the gate is green. Every lens is static read-only analysis over a frozen diff snapshot, with one exception: a stack's Step 0b boot probe boots the project once and reads its own startup log — no test suite, no gameplay.

## When to use
- Pre-commit after the project's regression gate passes, before `/commit_push`.
- Pre-PR before `/create_pr`, or pre-merge before `/merge_pr`.
- Skip for meta-only commits (`.claude/`, docs) — the parity/API lenses have nothing to chew on.
- Which lenses run for a change follows [`execution_depth.md`](../skills/_brainstorm_shared/execution_depth.md) §Mandate admission.

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
git -C "$ROOT" show main:<path>            # per changed source file
```

Run these cheap critical checks Claude-side and add their results to the snapshot:
- grep changed source files for leftover `[DIAG-` instrumentation and the stack's raw debug-print call;
- read the subsystem registry, then grep every `framework_paths` source root for the project namespace;
- check `git status --porcelain -- .claude/scratch`;
- compare changed paths with every subsystem whose default `domain` is `gameplay`.

**Playtest-route critical (Gameplay/Mixed diffs).** If a changed behavior is Gameplay and subjective,
require either a changed scene under one of the registry's `playtest_scenario_paths` or a branch log at
`.claude/scratch/playtest_artifact/<slug>.log` carrying `[Lifecycle] DIRECT RUN`. A playtest-artifact
report that names every subject as already routed also clears the critical finding. Do not maintain a separate
subject-folder list here.

Also read into the snapshot: `.claude/worklog-titles.md` (worklog state) and the relevant topic-folder `roadmap.md` Parts table (roadmap state) for the reconcile lens.

The two **reachability** lenses are the one exception to push-don't-pull and search for themselves — the reacher can be any authored data file (scene, resource, config) in the project, so no snapshot can carry it. Their compensation is the stated-probe rule in Step 1b; capture nothing extra for them here.

## Step 0b: Runtime boot probe (stacks that ship one)

Where the project's stack ships a boot-log probe — the engine diagnoses its own wiring at boot and writes the diagnosis to its log — run it now, serially, after the project's regression gate (`change_control` §Gate cadence names it) is green and never while a test run is live. The stack's log-analysis command owns the probe procedure. It returns one verdict per distinct warning group: **CORRELATED** (the group names a token from this diff) → critical; **UNCORRELATED** → `minRigor` thorough; **NOT RUN** (pin mismatch, launch failure, empty log) → a coverage gap, open at every rigor; plus an advisory warning-budget delta. A stack with no probe skips this step.

## Step 1: Dispatch the battery

Each lens reads a different input, so a branch's first run runs one seat per mandate (`orchestration` §2 *Sizing the width*); a rerun after new commits is a later round, sized per `orchestration` §2 *Later rounds* from what the new commits changed. Write the seats to `.claude/scratch/pr_ready/roster_<branch>.json` (`{key, effort}`, the effort per `orchestration` §5; `review_ledger.py` fills each seat's mandates and tier from the lens registry) and run `python3 .claude/tools/review_ledger.py plan --stage pr-ready --subject session:<sid> --roster <roster.json> --files <changed files>`; dispatch only the mandates it returns. An audit scope the owner names ignores the ledger: pass `--ignore-ledger`. Write `{"files": [<changed files>]}` to `.claude/scratch/pr_ready/scope_<branch>.json`. The `review_fanout` call carries `args.ledger` `{stage: "pr-ready", subject: "session:<sid>", roster: <roster.json>, scopeJson: <that file>}`, so its post-check records those rows over the changed files; the `parity` seat's rows come from its own post-check (Step 1a). Its lens battery needs Workflow, which a delegate lacks, so a delegate never runs `/pr_ready` and the parent keeps the lenses (`orchestration` §0). Triage any 4ms/0-agent/0-byte death as **malformed args, not a broken tool** (`gotcha_workflow_args_generation_fidelity.md`).

### Step 1a: parity lens — `dispatch.js`, brief as a file

Write the brief (mandate + OLD from `git show main:…` + NEW per changed source file, verbatim with file:line) to `.claude/scratch/pr_ready/parity_<branch>.md` and pass that path, never the payload in `args` — `parity` is the one lens heavy enough to hit the generation-fidelity limit (`gotcha_workflow_args_generation_fidelity.md`) — then:

```
Workflow({
  scriptPath: ".claude/workflows/dispatch.js",
  args: { jobs: [ { label: "parity", promptPath: "<abs>/.claude/scratch/pr_ready/parity_<branch>.md", tier: "executor", model: "<executor>", effort: "<executor review rung>", agentType: "general-purpose", readOnly: true, ledger: { stage: "pr-ready", subject: "session:<sid>", roster: "<roster.json>", scopeJson: ".claude/scratch/pr_ready/scope_<branch>.json" } } ] }
})
```

The executor tier at its review rung (`reference/model_ladder_evidence.md` §Role guidance): a line-precision OLD-vs-NEW audit that gates the PR, where a missed dropped branch ships a bug. The parity job declares its ledger above, so the engine closes in review mode; consume the result per [protocol Step 1.5](agents/orchestrator_action_protocol.md). Run the job in the same message as Step 1b — two Workflow calls in flight together.

Mandate (`parity`): the brief carries the [Mandate section of the parity checklist](checklists/parity.md) verbatim, the one home of this mandate.

### Step 1b: the other five lenses — `review_fanout`

Keep each `prompt` lean: push the shared diff stat + changed-file list + the 3 pre-computed critical grep results via `contextPrefix` (NOT repeated per agent). Each seat's must-read list is the diff of the changed files its candidates come from (written to scratch); `worklog-roadmap`'s is the worklog-titles and roadmap files it reconciles. Pass it as the seat's `mustRead` (`review_fanout.js` §USAGE CONTRACT).

| key | lens | mandate (findings: action FIX/ASK/PLAN, category bug/rule/improvement, `critical?`, file:line, description, rationale) |
|---|---|---|
| `data-reach` | authored-data reachability | For each behavior-selecting default and each threshold the diff introduces or touches: name the authored data file that selects the non-default branch or crosses the threshold. Rubric below. |
| `wire-reach` | production-wiring reachability | For each new parameter on an existing signature and each new type the feature needs at runtime: name the PRODUCTION call site or scene that supplies it. Rubric below. |
| `api-consume` | consume-new-APIs | For each new public type/method in the diff: are the motivating call sites migrated, or do old paths still bypass it (the spawn-behaviors-bypass-crafted-pipeline class)? Report unmigrated call sites. |
| `worklog-roadmap` | state reconcile | Against the pushed worklog + roadmap: shipped item still `## Active`? completed Part still plan-pending/in-progress? orphaned `.claude/scratch` from this work? |
| `doc-coverage` | diff doc-coverage | Changed authored-data field (an engine export, a config key) missing its doc comment? changed subsystem missing a skill/roadmap touch? new public Logic API undocumented? |

#### Reachability rubric — the inert-feature hunt

**Named failure mode — the inert feature:** code that compiles, passes the full gate, and never runs, because no authored data and no production wiring ever selects the branch that enables it. Neither `parity` ("is this code right") nor `api-consume` ("is this symbol referenced") asks the question that finds one: the code IS right and the symbol IS referenced — only its *reacher* is missing. Ask "what authored value reaches this branch", never "is this code correct".

**Production corpus = tracked paths outside the test tree and any prototype/throwaway tree the project excludes from production.** A reacher inside either is not a reacher: a test call site that passes the new parameter, and a test fixture that instantiates the new component, are the two shapes that have already shipped inert features past a green gate.

Candidate shapes, all enumerated from the pushed diff:

| lens | candidate shape | the reacher it must name |
|---|---|---|
| `data-reach` | new or changed authored-data field whose default selects behavior — enum, bool, nullable reference, strategy slot | an authored data file setting it to a NON-default value |
| `data-reach` | new threshold, band, or gate — `MinX`/`MaxX`, any comparison against a tunable field | authored values on both sides, plus what fraction of the authored range reaches each branch |
| `wire-reach` | new parameter on an existing method signature | a production call site that supplies it explicitly (a defaulted parameter compiles at every un-migrated site) |
| `wire-reach` | new type, component, or node the feature needs at runtime | a production scene, resource or config referencing it by class name or ID |

One verdict per candidate:
- **UNREACHED** — the probe returned empty. `action: FIX`, `category: bug`, `critical: true`. Word it "no authored reacher found in \<the corpus the probe covered\>", never "dead code": a diff-scoped probe cannot prove global unreachability, and a reader who deletes live surface on this finding has been misled. Put in `new` the authored data that WOULD reach it — file to touch, field, value.
- **NARROW** — the threshold IS crossed, but only by a minority of the authored range (a gate at `MinEnergy = 7.0` against an authored max of `10`). `action: ASK`, `category: bug`, non-critical; give the fraction and both `file:line` values. Reachable-in-principle and invisible-in-play are different states, and this is the only verdict that separates them.
- **INDETERMINATE** — the selecting value is computed at runtime, produced by a generator, or otherwise not expressible as a literal probe. `action: ASK`, `category: bug`, non-critical; name the unknown and who settles it. Leave it INDETERMINATE rather than upgrading it to UNREACHED for a decisive-looking report.
- **REACHED** — no finding of its own; it goes in the coverage roll-up below with its reacher `file:line` and value.

**Every UNREACHED finding states its probe verbatim in `rationale`** — the exact `git grep` whose empty result IS the claim (e.g. `git grep -n "flavor = 1" -- '*.<data-ext>' ':!Tests/'`). Claude re-runs that command in Step 2; an unstated or unrunnable probe is a coverage gap, open at every rigor. This is what keeps the lens falsifiable, and it is the mitigation for `gotcha_workflow_fanout_search_false_absence` — a fanned agent's empty `Grep` is a candidate, never a verdict.

**Each reachability lens returns exactly one coverage roll-up finding** (`action: PLAN`, `category: improvement`, non-critical, `file: null`): candidates enumerated, and the REACHED ones listed with reacher `file:line`. A lens that enumerated zero candidates reports that in the roll-up, so "no findings" stays distinguishable from "nothing examined".

```
Workflow({
  scriptPath: ".claude/workflows/review_fanout.js",
  args: {
    contextPrefix: "<diff --stat + changed-file list + the 3 pre-computed critical grep results>",
    agents: [
      { key: "data-reach",      prompt: "<rubric + changed authored-field declarations with defaults + threshold comparisons, verbatim with file:line>", shape: "review", tools: { add: ["Bash"] }, tier: "<resolved tier>", model: "<resolved native id>", effort: "<resolved effort>", agentType: "general-purpose", mustRead: [<must-read entries>] },
      { key: "wire-reach",      prompt: "<rubric + OLD+NEW signature lines (from the Step 0 parity capture) + new type declarations, verbatim with file:line>", shape: "review", tools: { add: ["Bash"] }, tier: "<resolved tier>", model: "<resolved native id>", effort: "<resolved effort>", agentType: "general-purpose", mustRead: [<must-read entries>] },
      { key: "api-consume",     prompt: "<mandate + new-public-API list + call-site content>", shape: "review", tools: { add: ["Bash"] }, tier: "<resolved tier>", model: "<resolved native id>", effort: "<resolved effort>", agentType: "general-purpose", mustRead: [<must-read entries>] },
      { key: "worklog-roadmap", prompt: "<mandate + worklog-titles + roadmap Parts table>", shape: "review", tools: { add: ["Bash"] }, tier: "<resolved tier>", model: "<resolved native id>", effort: "<resolved effort>", agentType: "general-purpose", mustRead: [<must-read entries>] },
      { key: "doc-coverage",    prompt: "<mandate + changed-export/subsystem content>", shape: "review", tools: { add: ["Bash"] }, tier: "<resolved tier>", model: "<resolved native id>", effort: "<resolved effort>", agentType: "general-purpose", mustRead: [<must-read entries>] }
    ]
  }
})
```

Every lens runs `git diff`/`git grep` itself, so each declares `tools: { add: ["Bash"] }` on the `review` shape. Resolve all five lenses' pins through `orchestration` §5; each `<resolved effort>` is the chosen row's review rung (ladder §Role guidance). Send OLD+NEW *signature lines* only, never whole methods — `parity` already owns method bodies, and duplicating them here walks back into the generation-fidelity envelope. `api-consume` is line-precision work — demand verbatim file:line.

## Step 2: Present findings by weight

`review_fanout` returns deduped+sorted findings; merge in the standalone `parity` Agent()'s findings, the three Claude-side grep critical candidates, and any Step 0b boot-probe verdicts. Treat every critical finding as a candidate Claude verifies, not final truth.

**Re-run every UNREACHED probe before presenting it.** Take the `git grep` the finding quoted, run it Claude-side, and let the result set the tier: still empty → critical; returns a production hit → clear `critical` and set `minRigor` thorough, naming the reacher the lens missed; no probe stated, or it does not run → a coverage gap, open at every rigor, saying which. Never promote an UNREACHED on the lens's word alone — the whole finding rests on an absence, and an absence one intermittent `Grep` produced is not evidence.

Present as:
- **Critical** (`critical: true`, `minRigor` light; holds at every rigor, H4): parity-dropped branch, framework leak, leftover `[DIAG-]`, probe-confirmed UNREACHED, CORRELATED boot warning — must resolve before PR.
- **`minRigor` normal**: unmigrated call site, state drift, missing doc coverage, NARROW reachability. An INDETERMINATE reachability finding is an ASK and counts at every rigor.
- **`minRigor` thorough**: reachability roll-ups, UNCORRELATED boot groups, the warning-budget delta.

The tiers above are examples of each weight: the lens grades each finding it reports by the bar in `orchestration` reference `rigor.md`, and the orchestrator changes a tag only when it re-runs a probe.

**Coverage gaps** (open at every rigor): boot-log probe NOT RUN, unverified reachability. Findings below the run's bar (`orchestration` reference `rigor.md`) are listed `below bar: noted` and skip the walkthrough.

**Never average across lenses, and issue no roll-up verdict.** Each lens reports its own outcome on the protocol's verdict scale, set by its worst surviving finding — a clean `doc-coverage` does not soften a `parity` critical, and "5 of 6 lenses clean" is not a result. State each lens's worst finding separately.

Then run the user-gated walkthrough per `agents/orchestrator_action_protocol.md` Step 1.5 and Step 4. Do NOT auto-run tests or auto-commit.
