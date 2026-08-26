# Sidecar Dispatch — one recipe, every transport

A sidecar is a separate `claude` child on an external endpoint (GPT, opencode, deepseek, …). It is the ONLY way an Anthropic session reaches a non-Anthropic model — `Workflow`/`Agent` pins never cross transports. Every launcher shares one flag surface (`scripts/lib/sidecar_common.sh`), so the recipe below is identical for all of them. Injected on any Bash call to `*_sidecar.sh` by `hooks/sidecar_dispatch_context.py`.

## Procedure

1. **Roster + launcher:** `python3 .claude/tools/model_registry.py available` — prints each alias, its roles, effort rungs, price, and which transport it belongs to. Launcher = that transport's `launcher` field in `reference/external_models.json`. Excluded models are out; re-select under the ladder (`reference/model_ladder_evidence.md`).
2. **Band:** read the latest `[budget-posture]` line in context. Exit **5** at dispatch = band below the model's floor; add `-A` only when the spend is a deliberate choice, and say so.
3. **Prompt to a file** (never argv — Windows argv caps at ~8K). Schema to a file when you need structured output.
4. **Dispatch** — the minimum well-formed call; every field is required except the bracketed ones:

```bash
bash .claude/scripts/<launcher> -m <alias> -e <effort> -D <bare|pointer|full> -G <survey|review|author|any> -f <prompt.md> [-S <schema.json>] -R <record.json> -l "<label>" -d "<repo-root>" > <capture>.json
```

5. **Consume:** result JSON is on **stdout** (`-o` is a FORMAT, never a path). `-R` record holds `costUSD` (truth; `total_cost_usd` in the payload is inflated), `servedModel`, and attestation. Rate the dispatch's effort fit when you read it.

A fan-out is N calls, each with its own `-R` + `-l`; run them `run_in_background` and wait for the notification — never poll, never re-dispatch without checking the capture has bytes.

## Agent type = `-D` × `-G`

| type | flags | child sees | use for |
|---|---|---|---|
| scout | `-D bare -G survey` | repo files only, no doctrine | locate / enumerate / extract |
| lens | `-D pointer -G survey` | + `MEMORY.md` | surveys that must dodge known traps |
| reviewer | `-D full -G review` | full harness | red-team, plan check, verdict lenses |
| author | `-D full -G author` | full harness | code/doc authoring, TDD under a spec |
| judge | `-D full -G any` | full harness | judgment fitting no other row |

Effort: the registry row's `effort` block (`converged` / `open` rungs); `unmeasured` rows are a guess, not a pin you can defend.

## Optional flags

`-C <file>` extra context (repeatable) · `-a <dir>` extra read grant (e.g. the vault) · `-t <csv>` allowed tools (default read-only `Read,Glob,Grep`; an author needs `Edit,Write,Bash`) · `-p <mode>` permission mode (default `auto`) · `-P <file>` live progress stream (then send stdout to `/dev/null`) · `-T <sec>` wall-clock wake (never a kill) · `-r <id>` resume a persisted session.

Never: `-n` (a turn cap discards a paid run) · `CCP_BIN` (the proxy resolves from its install path; set it only when `--check` says it is missing) · vendor ids inside Workflow `agent()` pins.

## Exit codes

| code | meaning | fix |
|---|---|---|
| 2 | bad flag / unknown alias / registry unreadable | `model_registry.py available` |
| 3 | credential missing | the launcher header names the file |
| 4 | child CLI or proxy missing | `<launcher> --check` |
| 5 | band below the model's floor | `-A` if deliberate |
| 6 | balance floor / probe failed | not overridable |
| 7 | model marked unavailable | re-select; `-U` only on the user's word |
| 8 | provider's own quota band over ceiling | `-A` if deliberate |

`--check` on any launcher: exit 0 = dispatchable now.
