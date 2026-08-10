# Model Delegation — core doctrine

Canonical home of the delegation decision framework. Project CLAUDE.md files
reference this file and add a project-weight table (which tier does what HERE);
they do not restate the framework. This replaces any cross-repo runtime read of
another project's CLAUDE.md — a tracked file cannot rot when a sibling project
renames a heading.

## Selection order

Delegate selection: **intelligence > taste > cost > speed.** Session-model
selection: delegation ability dominates — the orchestrator's job is judgment
and dispatch, so its own tier matters more than its typing speed.

## The ladder (roles, not brands — map to current model names per tier)

| tier | role |
|---|---|
| frontier (Fable/Opus-tier) | judgment calls, structure decisions, genuine ambiguity, brand/voice taste |
| mid (Sonnet-tier) | fan-out workers: review lenses, verification passes, scoped research |
| budget sidecar (`scripts/deepseek_sidecar.sh` or equivalent) | budget-gated bulk I/O once dispatch volume justifies it |
| small (Haiku-tier) | validation of cheap-to-reject answers; offline fallback for simple synthesis |

## Pins

Pin model+effort explicitly on any dispatch — never inherit session defaults.
The `Agent` tool pins `model` but carries effort as prompt text only (soft pin);
the Workflow engine's `agent()` has a real `effort` option — when a hard effort
pin matters, dispatch via Workflow. Full fan-out selection doctrine (tier
ladder, carve-outs, engine floors): `skills/parallel_agents/SKILL.md` §5.
