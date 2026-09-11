---
description: Delegate one task or a job file through the correct pinned executor and emit one manifest
argument-hint: <task> | --jobs <path>
---

# Delegate

Canonical route for ordinary ad hoc delegation across the current transport and registered sidecars.
Use `/explore`, plan-draft commands, and `/plan_check` for their fixed panels; do not reproduce them here.

## Arguments

- `/delegate <task>` — turn the non-empty task into one `single` job. Put the full brief in a scratch `.md`; keep request fields small.
- `/delegate --jobs <path>` — read one JSON request with `route: single|parallel|chain|review`, optional `contextPath` and `spillDir`, plus `jobs` or `chains`.
- No argument, a missing/unreadable file, mixed inline and `--jobs`, unknown keys that affect dispatch, or malformed JSON → `HOLD` with the exact missing or invalid value. Do not infer it.

Each job needs `label`, `promptPath`, `role`, `model`, `effort`, `transport`, `currency`, and `agentType`. Optional fields are `shape`, `disclosure`, `contextFiles`, and `schemaFile`. `shape` is the delegate guard and must be `any|survey|review|author`; it never selects an executor. `route` selects only `single|parallel|chain|review`; never pass `route` as sidecar `-G` or derive it from `shape`.

## 1. Normalize and preflight

1. Run `python3 .claude/tools/model_registry.py available`. Resolve availability, transport ids, roles, launcher, effort rungs, prices, and context windows from that output only.
2. Namespace every bare job label as `<runKey>-<label>`. Reject duplicate effective labels. A chain also needs a non-empty name and jobs.
3. State the currency before dispatch: `plan-quota`, `marginal-usd`, or `local`. If the request needs paid work and the currency or current band cannot be established, return `HOLD`.
4. Return exactly one preflight state before work starts:
   - `PROCEED` — every field, pin, role, route, launcher, prompt path, schema path, and legal transport is confirmed.
   - `ABORT` — the band denies the spend; a role is unavailable; the request reserves an orchestrator decision; a route is invalid; the task belongs to `/explore`, a plan-draft command, or `/plan_check`; or a native chain crosses transports.
   - `HOLD` — a required value, file, band, or paid currency is unknown and only the caller can supply it.
5. Stop on `ABORT` or `HOLD`. Never downgrade a role, translate a provider id, swap currency, or silently drop an arm.

## 2. Expand the exact run

For `single`, `parallel`, and `chain`, keep one seed row per requested job.

For `review`, expand each logical lens before dispatch:

- Add one Anthropic arm from the available fan-out role.
- Add every other available roster model that claims the fan-out role.
- Keep each arm's native model id, transport, effort rung, currency, and launcher.
- Give every arm a unique namespaced label. The expanded seed job count is the required evidence count; zero or duplicate matches fail manifest generation.

Write the expanded manifest seed under `.claude/scratch/` before dispatch. Keep requested values separate from later effective evidence. Set unknown effective effort to `null`; never copy the requested effort into it.

## 3. Dispatch through existing owners

Split a mixed run by transport, but preserve one run key and one expanded seed.

- Current-transport `single` or `parallel` → `Workflow({scriptPath: ".claude/workflows/dispatch.js", args: {jobs: [{label, promptPath, model, effort, agentType, shape}], contextPath, spillDir}})`.
- Current-transport `chain` → `Workflow({scriptPath: ".claude/workflows/dispatch_chains.js", args: {chains: [{name, jobs: [{label, promptPath, model, effort, agentType, shape}]}], contextPath, spillDir}})`. Every chain stays on one transport.
- Current-transport `review` → `Workflow({scriptPath: ".claude/workflows/review_fanout.js", args: {agents: [{key, promptPath, model, effort}], contextPrefixPath, spillDir}})`. Map the effective label to `key`; do not pass a dispatch-job object to this engine.
- Every off-transport group → convert each job to `{label, alias: model, promptFile: promptPath, effort, disclosure, shape, contextFiles, schemaFile, transport}` and run `python3 .claude/tools/sidecar_fanout.py <jobs.json> --out-dir <record-dir>`. The tool resolves the launcher from `alias` and `transport`. `shape` alone maps to sidecar `-G`.

Do not use a bare Agent call for exact pins. Workflow and Agent stay on the current endpoint; neither can reach another transport. Use `dispatch.js`, `dispatch_chains.js`, and `review_fanout.js` as the native executor family, and `sidecar_fanout.py` as the cross-transport owner.

Large prompts and context live in files. Workflow `args` carries paths and short scalar values. Use a legal `.claude/scratch/` or `$TEMP/claude` spill directory. Paid sidecars never auto-retry; recover a null return from the declared spill file or transcript before considering another dispatch.

## 4. Consume and finalize

1. Read every returned digest, spill, or structured review result. Treat a missing required arm as uncovered, not clean.
2. Record each consumed label immediately in `.claude/orchestration_verdicts.json` as `clean`, `defects`, `rework`, or `discarded` before building the manifest.
3. Build the run record without appending the metrics archive:

```bash
python3 .claude/tools/orchestration_metrics.py --manifest-seed <seed.json> --manifest-out <manifest.json> --session <session-dir> --sidecar-record-dir <record-dir> --manifest-verdicts .claude/orchestration_verdicts.json
```

4. Confirm the manifest contains the expanded seed job count, one exact evidence join per label, distinct `workflow|sidecar` sources, requested and effective fields kept separate, and status `completed`. Missing or duplicate evidence is a failed run, not a partial success.
5. Report preflight state, route, exact pins, currency, manifest path, status, and any uncovered arm. Do not report actual Codex spend from Claude Code's `anthropic-rate` estimate.
