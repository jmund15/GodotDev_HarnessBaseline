---
description: Delegate a scoped job with explicit pins and one attributable result
argument-hint: <task> | --jobs <path>
---

# Delegate

Use this for ordinary ad hoc jobs. `/explore` and `/plan_check` keep their coverage contracts and use the same engines.

## Arguments

- `<task>` creates one `single` job; put the brief in a scratch file.
- `--jobs <path>` reads `{route, jobs|chains, contextPath?, spillDir?}`. Routes: `single|parallel|chain|review`.
- Missing/ambiguous arguments, unreadable files or malformed input return `HOLD` with the exact problem before dispatch.

A job requires `label`, `promptPath`, `role`, `model`, `effort`, `transport`, `currency`, `agentType`. Optional: `shape`, `disclosure`, `contextFiles`, `schemaFile`. `shape` is `any|survey|review|author`; it selects delegate rails, never an executor. `route` selects the engine.

## 1. Normalize and preflight

Check needed model fields through `model_registry.py resolve <model>`, locate rows with `for-role <role>`, and take the effort from the role ladder's cell; `for-role` picks none. Use `available` when selecting a model, not as a repeated full-catalog ritual. Preserve explicit owner overrides as overrides, not capability promotions; availability and permission boundaries still bind.

Namespace labels `<runKey>-<label>` and reject duplicates. State model, effort, profile and currency before dispatch. Keep unknown effective settings null.

Return one state:
- `PROCEED`: scope, inputs, route, eligibility, pins and currency are established.
- `HOLD`: a required fact or owner decision is missing.
- `ABORT`: the route, authorization or availability forbids the job, or the task belongs to `/explore`, a plan-draft command or `/plan_check`.

Do not silently change a pin, role, provider or currency to get past a failure.

## 2. Keep the declared run

**One suitable arm per independently needed job, including review lenses at their seat width: a review lens is one mandate; seats follow `orchestration` §2.** Adding an available provider does not add jobs. Keep independent required coverage; related checks may share a scoped lens when coverage stays explicit.

Same-task model comparisons belong to an explicit comparison request (`/pin_ab` or a declared comparison workflow), with named arms, frozen inputs and a finite budget. Do not turn ordinary delivery into calibration or infer comparison eligibility from a mixed-model run.

Write the declared manifest seed before dispatch. For native review, the evidence label is `review:<key>`; other routes retain their namespaced label. The seed count is the required result count.

## 3. Dispatch through existing owners

Use paths and short scalars in Workflow args. Every job has explicit `model`, `effort` and `agentType`.

| Route | Engine and payload |
|---|---|
| Native single/parallel | `dispatch.js`: `{jobs:[{label,promptPath,model,effort,agentType,shape}],contextPath,spillDir}` |
| Native chain | `dispatch_chains.js`: `{chains:[{name,jobs:[...]}],contextPath,spillDir}`; one transport per chain |
| Native review | `review_fanout.js`: `{agents:[{key,promptPath,model,effort,agentType}],contextPrefixPath,spillDir}` |
| Off-transport | `sidecar_fanout.py`: jobs `{label,alias:model,promptFile:promptPath,effort,disclosure,shape,contextFiles,schemaFile,transport}` |

Native engines are under `.claude/workflows/`; sidecar CLI is `python3 .claude/tools/sidecar_fanout.py <jobs.json> --out-dir <record-dir>`. On the off-transport route `shape` supplies sidecar `-G`; never pass `route` as `-G` or derive one from the other. Split transports without changing job identity. Workflow/Agent do not cross endpoints; a bare Agent call cannot supply an exact effort pin.

Keep full evidence in a run-specific `.claude/scratch/` artifact and return a bounded digest. Select a profile that can produce the promised artifact; read-only profiles use their supported recovery path. Wait for completion events, not repeated status polling. Recover existing output before any new model call; a whole-task retry is not transport recovery.

## 4. Consume and finalize

1. Verify each artifact and required source/finding coverage. Missing or malformed output is uncovered, not a clean zero. Recheck relevant inputs that changed during a read-only job.
2. Record each consumed label in `.claude/orchestration_verdicts.json`: `clean|defects|rework|discarded`. Keep task completion separate from process exit.
3. Build the existing manifest without appending the metrics archive:

```bash
python3 .claude/tools/orchestration_metrics.py --manifest-seed <seed.json> --manifest-out <manifest.json> --session <session-dir> --sidecar-record-dir <record-dir> --manifest-verdicts .claude/orchestration_verdicts.json
```

Verify one evidence join per declared label, requested/effective fields kept separate, distinct `workflow`/`sidecar` sources, status `completed` and all required outputs accounted for. Report result, verification, artifact and unmet scope briefly. Codex quota is not Claude Code's estimated Anthropic-dollar cost.
