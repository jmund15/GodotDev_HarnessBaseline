---
description: Delegate scoped ad hoc jobs with explicit model/effort pins
argument-hint: <task> | --jobs <path>
allowed-tools: Bash(python3 .claude/tools/lens_briefs.py:*), Bash(python3 .claude/tools/sidecar_fanout.py:*), Bash(python3 .claude/tools/dispatch_size.py:*), Bash(python3 .claude/tools/orchestration_metrics.py:*), Bash(python3 .claude/tools/dispatch_result.py:*), Workflow
---

# Delegate

Use this for ordinary ad hoc jobs. Commands with their own coverage contract, such as the coding layer's exploration and plan-review commands, keep it and use the same engines.

## Arguments

- `<task>` creates one `single` job; put the brief in a scratch file.
- `--jobs <path>` reads `{route, jobs|chains, contextPath?, spillDir?}`. Routes: `single|parallel|chain|review`.
- For missing/ambiguous arguments, unreadable files or malformed input, the parent returns `HOLD` with the exact problem before dispatch.

A job requires `label`, `promptPath`, `role`, `model`, `effort`, `transport`, `currency`, `agentType`; a `review` route job also requires `mustRead` for its subject files, the `{"path", "lines"}` entries that `python3 .claude/tools/lens_briefs.py must-read <list file>` prints. Optional: `shape`, `disclosure`, `contextFiles`, `schemaFile`, `resident` (`true` for a job that keeps one subject resident, such as a design, a plan or chained research). The sidecar route compacts a resident job at the resident window, and a `review` shape already does. `shape` is `any|survey|review|author`; it selects delegate rails, never an executor. `route` selects the engine.

## Converged single job

One job with a check costs the parent four requests (`usage_report.py turns` counts them):

1. Write the brief to scratch: its prose, pointers to the spec it executes, and one fenced `task` block holding one JSON object: `check` (required, non-empty: shell commands, or the literal `claims`; a writing job adds `python3 .claude/tools/dispatch_result.py check <spillDir>/<label>.md --block choices=unit,why`) and `writeSet` (omit for a read-only job). The brief names `<spillDir>/<label>.md` as the job's report file (`orchestration` reference `writer_returns.md`).
2. Write `{"jobs": [<job>], "spillDir": "<scratch dir>"}` to a file; the job carries the fields §Arguments requires plus its `tier`. Run `python3 .claude/tools/dispatch_size.py <file>` first: its line gives the reading each job carries and whether it fits the row's window, which the Pre-Dispatch Checklist weighs before launch. Then run `python3 .claude/tools/sidecar_fanout.py <file> --detach`; it pays the job's row from the plan with the most room. When none of your watchers is armed, issue the arm in the same message with `run_in_background` and `timeout` 7200000: `python3 .claude/tools/watch_records.py --session --wake --await-launch` (`reference/sidecar_dispatch.md` step 6); both calls share one request.
3. On the completion wake, run `python3 .claude/tools/dispatch_result.py consume <run dir or fan-out out dir> --label <label>`. It prints the digest, each `CHECK <n>` verdict with its tail, the changed paths, the diff and the manifest path. `HELD` means the brief was written after the run started, so nothing ran. Exit codes and refusals are in its docstring.

A check runs the edited units' proof files, never the bound suite (`rules/harness_tooling.md`), each to `--check-timeout`. A floor-guarded check is refused, and the parent runs that check in its own Bash call. A known follow-on's steps go in the same brief, each with its check in order, never the shared-checkout commit or the push.

Then the parent reviews the diff, chooses what lands, commits by pathspec and rates it (`/orchestration_metrics` §Incremental rating) in the same Bash call.

## 1. Normalize and preflight

Check needed model fields through `model_registry.py resolve <model>`, locate rows with `for-role <role>`, and take the effort from the role ladder's cell; `for-role` picks none. Use `available` when selecting a model, not as a repeated full-catalog ritual. Preserve explicit owner overrides as overrides, not capability promotions; availability and permission boundaries still bind.

Namespace labels `<runKey>-<label>` and reject duplicates. State model, effort, profile and currency before dispatch. Keep unknown effective settings null.

The parent performs this preparation. The engines consume the prepared jobs. Return one state:
- `PROCEED`: scope, inputs, route, eligibility, pins and currency are established.
- `HOLD`: a required fact or owner decision is missing.
- `ABORT`: the route, authorization or availability forbids the job, or the task belongs to a command with its own coverage contract: exploration, plan drafting or plan review.

Do not silently change a pin, role, provider or currency to get past a failure.

## 2. Keep the declared run

**Jobs follow `orchestration` §0; a review lens is one mandate, and its seats follow §2.** Related checks may share a scoped lens when coverage stays explicit.

Same-task comparisons follow `orchestration` reference `model_evidence.md`.

Write the declared manifest seed before dispatch on a multi-job route; `consume` derives the seed for the converged single job. For native review, the evidence label is `review:<key>`; other routes retain their namespaced label. The seed count is the required result count.

## 3. Dispatch through existing owners

Use paths and short scalars in Workflow args. Every job has explicit `model`, `effort` and `agentType`, and a `tier` (its `role`). The guard's tier behavior is in `orchestration` §5.

One jobs file, `{jobs:[...],contextPath,spillDir}` or `{chains:[{name,jobs:[...]}],contextPath,spillDir}`, each job carrying the fields §Arguments requires plus its `tier`. `sidecar_fanout.py` runs the file (per-job `disclosure` and `schemaFile` allowed) and picks each job's paying plan; chains run as lanes in parallel, each lane's jobs in sequence. Native `Workflow` runs only an engine the fan-out lacks: `review_fanout.js`, which takes `agents:[{key,promptPath,model,effort,agentType,mustRead}]` in place of `jobs` and `contextPrefixPath` in place of `contextPath` (it never reads `contextPath`).

Run `python3 .claude/tools/dispatch_size.py <file>` on the jobs file, or on the Workflow args saved as JSON, before launching it. Native engines are under `.claude/workflows/`; sidecar CLI is `python3 .claude/tools/sidecar_fanout.py <jobs.json> --out-dir <record-dir> --detach`. `shape` supplies sidecar `-G`; never pass `route` as `-G` or derive one from the other. Split transports without changing job identity. Workflow/Agent do not cross endpoints; a bare Agent call cannot supply an exact effort pin.

Keep full evidence in a run-specific `.claude/scratch/` artifact (`orchestration` §4) and recover before repaying (`orchestration` §8). Waiting on completion events follows `CLAUDE.core.md` §Shell Discipline. Select a profile that can produce the promised artifact.

## 4. Consume and finalize

The converged single job above is consumed by `dispatch_result.py consume`, which performs steps 1 and 3 (artifact and coverage check, manifest join); the parent still rates (step 2). The steps below serve every other route.

1. Verify each artifact and required source/finding coverage (`orchestration` §8). Consume findings and claims per [`orchestrator_action_protocol.md`](agents/orchestrator_action_protocol.md) Step 1.5. Recheck relevant inputs that changed during a read-only job.
2. Rate the consumed labels before the manifest: `--rate` or `--rate-batch` as `/orchestration_metrics` §Incremental rating directs.
3. Build the existing manifest without appending the metrics archive:

```bash
python3 .claude/tools/orchestration_metrics.py --manifest-seed <seed.json> --manifest-out <manifest.json> --session <session-dir> --sidecar-record-dir <record-dir> --manifest-verdicts .claude/orchestration_verdicts.json
```

Verify one evidence join per declared label, requested/effective fields kept separate, distinct `workflow`/`sidecar` sources, status `completed` and all required outputs accounted for. Report result, verification, artifact and unmet scope briefly; do not equate tool quota with provider cost.
