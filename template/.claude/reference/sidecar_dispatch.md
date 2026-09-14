# Sidecar Dispatch — one recipe, every transport

A sidecar is a separate `claude` child on a transport the calling session is not running on (GPT, opencode, deepseek — and Anthropic itself, reached from a provider session via `anthropic_sidecar.sh`). It is the ONLY way to cross transports: `Workflow`/`Agent` pins never do. Within one transport, sibling models need no sidecar — pin them directly. Every launcher shares one flag surface (`scripts/lib/sidecar_common.sh`), so the recipe below is identical for all of them. Injected on any Bash call to `*_sidecar.sh` by `hooks/sidecar_dispatch_context.py`.

## Procedure

1. **Roster + launcher:** `python3 .claude/tools/model_registry.py available` — prints each alias, its roles, effort rungs, price, and which transport it belongs to, `anthropic` included. Launcher = that transport's `launcher` field in `reference/external_models.json`. Excluded models are out; re-select under the ladder (`reference/model_ladder_evidence.md`) — **a stale route name in a command is not a reason to fall back to the session's own transport.**
2. **Band:** read the latest `[budget-posture]` line in context. `<launcher> --check -m <alias>` runs every refusal gate a dispatch runs (7 availability, 5 band floor, 8 provider ceiling) and `hooks/sidecar_dispatch_context.py` runs it for you before any launch — a refusal DENIES the Bash call, never surfaces minutes later in a background task's output. Add `-A` only when the spend is a deliberate choice, and say so in the dispatch line.
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

**`-D` is window-relative.** Turn-1 context: `bare` 22k, `pointer` 25k, `full` 37k on Luna 258k (Anthropic tokenizer counts the same harness at 34k). Every run records `turn1ContextTokens`/`turn1ContextPct` in `-R` and prints them. Under 400k: lenses and scouts `pointer`; `full` only for a child that must apply doctrine. The brief is the bigger lever — hand paths and Grep targets, never the input.

## Optional flags

`-C <file>` extra context (repeatable) · `-a <dir>` extra read grant (e.g. the vault) · `-t <csv>` allowed tools (default read-only `Read,Glob,Grep`; an author needs `Edit,Write,Bash`) · `-p <mode>` permission mode (default `auto`) · `-P <file>` live progress stream (then send stdout to `/dev/null`) · `-T <sec>` wall-clock wake (never a kill) · `-Z <sec>` stall watchdog on the `-P` stream (default 900; 0 off; a run silent that long is killed, recorded as `terminal_reason: stall`, exit 9, never auto-resumed) · `-r <id>` resume a persisted session.

Every child also gets the **autonomy rail** (`sidecar_common.sh` `sc_build_disclosure`, all tiers, all models): it runs unattended, never ends on a question, decides and states the assumption, reports a blocker inside the deliverable. A run whose last message is a question is scored as delivered nothing — three Luna benchmark cells did exactly that before the rail existed.

Never: `-n` (a turn cap discards a paid run) · `CCP_BIN` (the proxy resolves from its install path; set it only when `--check` says it is missing) · a pin from ANOTHER transport's vocabulary inside Workflow `agent()` — role names on a provider session, vendor ids on an Anthropic one. Pin the session transport's own ids; `hooks/workflow_provider_guard.py` denies the other and names the roster.

## Resume — the two endings a run can be rescued from

**Resume is not retry.** `--resume <sid>` continues the same session and keeps every turn already paid for; a fresh dispatch re-buys them. That is what puts this inside the standing rule against auto-retrying a billed call, not against it — the rule bans duplicating spend. No session id means nothing to resume, and the launcher must not re-issue the run.

`sc_resume_loop` (in `lib/sidecar_common.sh`, called by every launcher) classifies the finished stream with `tools/sidecar_resume_check.py` and re-invokes the same child with `-r` plus the brief on stdin, up to `SC_COMPACT_RESUMES` (default 3). **stream-json only** — `-o json` emits no `compact_boundary` and no per-event stream, so a `json` run that hits either ending dies silently; `-P` sets stream-json. When the budget is exhausted and the run is *still* resumable, the launcher says so loudly: a run that burned its resumes otherwise records exactly like one that finished.

| ending | detected by | why resuming is safe |
|---|---|---|
| **compaction** | `compact_boundary`, zero tool calls after it, result opens `<analysis>`/`<summary>` | the child answered the continuation summary instead of the task; the work is intact in the session |
| **transient** | `is_error` + a connection-level signature (`WebSocket stream error`, `Connection reset without closing handshake`, `os error 10054`, 502/503/504) | the upstream stream aborted mid-flight after a 200; nothing about the request was rejected |

Never resumed, because each loops or needs backoff the loop does not implement: unknown model, malformed request, 401/403, rate limit or quota. The detector checks these **first**, so a deterministic fault can never be read as transient.

A mid-stream abort logs upstream as `status=200` (the proxy records status at headers, not at stream end), so diagnose it from the child's `terminal_reason` and `traffic/<session_id>/`, never from `proxy.log`.

### Compaction specifics

After auto-compaction the child receives the summary as a user message and, unanchored, answers it and ends. The launcher exports the `-f` path as `CLAUDE_CODE_SIDECAR_PROMPT_FILE` and registers `hooks/sidecar_recompact_reprompt.py` (SessionStart, matcher `compact`) via `--settings`, which re-injects the brief after every compaction.

- Registration rides with the LAUNCHER: a worktree checked out before the hook existed gets no re-prompt.
- Pass the brief path absolute and native — the hook opens it from the child's cwd, which is a worktree or a temp dir.
- Briefs name their on-disk outputs and ask for partial output early — that is the resume path.
- What ends a run is the thrash breaker, not a compaction count: context refilled to the limit within 3 turns of a compaction, 3 times running (`terminal_reason: rapid_refill_breaker`). Keep single tool results small: `-D pointer`, `Read` with `limit`, never a whole `.tres` or patch.
- Force a compaction with `SIDECAR_CONTEXT_TOKENS_OVERRIDE=<tokens>`; under ~80k the harness alone overflows.

Evidence: `auto-memory/archive/gotcha_sidecar_reprompt_rides_with_launcher.md`.

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
