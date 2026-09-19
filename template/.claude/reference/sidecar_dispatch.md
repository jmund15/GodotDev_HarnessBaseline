# Sidecar Dispatch — one recipe, every transport

A sidecar is a separate `claude` child on a transport the calling session is not running on (GPT, opencode, deepseek — and Anthropic itself, reached from a provider session via `anthropic_sidecar.sh`). It is the ONLY way to cross transports: `Workflow`/`Agent` pins never do. Within one transport, sibling models need no sidecar — pin them directly. Every launcher shares one flag surface (`scripts/lib/sidecar_common.sh`), so the recipe below is identical for all of them. Injected on any Bash call to `*_sidecar.sh` by `hooks/sidecar_dispatch_context.py`.

## Procedure

1. **Roster + launcher:** `python3 .claude/tools/model_registry.py available` — prints each alias, its roles, effort rungs, price, and which transport it belongs to, `anthropic` included. Launcher = that transport's `launcher` field in `reference/external_models.json`. Excluded models are out; re-select under the ladder (`reference/model_ladder_evidence.md`) — **a stale route name in a command is not a reason to fall back to the session's own transport.**
2. **Band:** read the latest `[budget-posture]` line in context. `<launcher> --check -m <alias>` runs every refusal gate a dispatch runs (7 availability, 5 band floor, 8 provider ceiling) and `hooks/sidecar_dispatch_context.py` runs it for you before any launch — a refusal DENIES the Bash call, never surfaces minutes later in a background task's output. Add `-A` only when the spend is a deliberate choice, and say so in the dispatch line.
3. **Prompt to a file** (never argv — Windows argv caps at ~8K). Schema to a file when you need structured output.
4. **Dispatch** — one call, backgrounded or not, with `-R`; every field below is required except the bracketed ones:

```bash
bash .claude/scripts/<launcher> -m <alias> -e <effort> -D <bare|pointer|full> -G <survey|review|author|any> -f <prompt.md> [-S <schema.json>] -R <record.json> -l "<label>" -d "<repo-root>" > <capture>.json
```

The launcher itself writes `<record>.out` and `<record>.exit` on **every** exit path — a clean finish, a gate refusal, a kill — so the result lands there even if the harness task that carried the call dies. Backgrounding is a routine call shape, not a special one.

**Detached form, for a run that must outlive this session:** add `-X -P <progress.jsonl>` and run the call in the foreground. `-X` must be its own argument (never a cluster like `-AX`) and needs `-R`. The launcher starts a detached job and returns within seconds, printing `DETACHED pid=`, `OUT=`, `ERR=` and `EXIT=`. Arm a Monitor on `EXIT` and the progress file; inspect the process and record before any kill. Each `-R` path takes one launch: a path whose `.out`, `.err`, `.exit` or `.pid` exists refuses with exit 1.

A backgrounded launch is never denied; `hooks/sidecar_dispatch_context.py` adds a one-line pointer at `<record>.exit` instead.

5. **Consume:** result JSON is on **stdout** (`-o` is a FORMAT, never a path). `-R` record holds `costUSD` (truth; `total_cost_usd` in the payload is inflated), `servedModel`, and attestation. Rate the dispatch's effort fit when you read it.

A fan-out is `tools/sidecar_fanout.py`, which detaches every child with `-X` so killing the fan-out's own process stops none of them; or N backgrounded calls, each with its own `-R`, relying on the same default durable files. Capture bytes do not prove a run ended: a retry waits for that run's `EXIT` file, or for its process to be proven gone, and takes a new `-R` path.

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

`-C <file>` extra context (repeatable) · `-a <dir>` extra read grant (e.g. the vault) · `-t <csv>` allowed tools (default read-only `Read,Glob,Grep`; an author needs `Edit,Write,Bash`) · `-p <mode>` permission mode (default by transport, §Permission mode) · `-P <file>` live progress stream (then send stdout to `/dev/null`) · `-T <sec>` wall-clock wake (never a kill) · `-Z <sec>` stall watchdog on the `-P` stream (default 900; 0 off; a run silent that long is killed, recorded as `terminal_reason: stall`, exit 9, never auto-resumed) · `-r <id>` resume a persisted session · `-W` dispatch inside a peak pricing window on a `gate.peakPolicy: refuse` row. Pass `-W` only when the user explicitly authorized peak pricing in the current conversation; `-A` does not cover it.

## Permission mode

Provider-transport children launch with `bypassPermissions` by owner decision: on those seats the auto-mode classifier runs on the provider model through the proxy and denies ordinary scratch and test writes. Under bypass, project hooks (heredoc, kill, harness-edit, gate cadence, dispatch guards) and settings deny rules still decide. Evidence: vault `DevProjects/{{PROJECT_NAME}}/Claude/Meta/Benchmark/harness-guidance-2026-09/permission-mode/`. The anthropic transport keeps `auto`. Pass `-p` only to override a default for a stated reason; never pass it to retry an action a hook or a deny rule refused.

Every child also gets the **autonomy rail** (`sidecar_common.sh` `sc_build_disclosure`, all tiers, all models): it runs unattended, never ends on a question, decides and states the assumption, reports a blocker inside the deliverable. A run whose last message is a question is scored as delivered nothing — three Luna benchmark cells did exactly that before the rail existed.

Never: `-n` (a turn cap discards a paid run) · an unverified `CCP_BIN` override (on Windows, use a side-by-side binary whose hash matches its tested build when live proxies lock the old executable) · a pin from ANOTHER transport's vocabulary inside Workflow `agent()` — role names on a provider session, vendor ids on an Anthropic one. Pin the session transport's own ids; `hooks/workflow_provider_guard.py` denies the other and names the roster.

## Resume — the two endings a run can be rescued from

**Resume is not retry.** `--resume <sid>` continues the same session and keeps every turn already paid for; a fresh dispatch re-buys them. That is what puts this inside the standing rule against auto-retrying a billed call, not against it — the rule bans duplicating spend. No session id means nothing to resume, and the launcher must not re-issue the run.

`sc_resume_loop` (in `lib/sidecar_common.sh`, called by every launcher) classifies the finished stream with `tools/sidecar_resume_check.py` and re-invokes the same child with `-r` plus the brief on stdin, up to `SC_COMPACT_RESUMES` (default 3). **stream-json only** — `-o json` emits no `compact_boundary` and no per-event stream, so a `json` run that hits either ending dies silently; `-P` sets stream-json. When the budget is exhausted and the run is *still* resumable, the launcher says so loudly: a run that burned its resumes otherwise records exactly like one that finished.

| ending | detected by | why resuming is safe |
|---|---|---|
| **compaction** | `compact_boundary`, zero tool calls after it, result opens `<analysis>`/`<summary>` | the child answered the continuation summary instead of the task; the work is intact in the session |
| **transient** | `is_error` + a connection-level signature (`WebSocket stream error`, `Connection reset without closing handshake`, `os error 10054`, 502/503/504) | the upstream stream aborted mid-flight after a 200; nothing about the request was rejected |

Never resumed, because each loops or needs backoff the loop does not implement: unknown model, malformed request, 401/403, rate limit or quota. The detector checks these **first**, so a deterministic fault can never be read as transient.

A mid-stream abort logs upstream as `status=200` (the proxy records status at headers, not at stream end), so diagnose it from the child's `terminal_reason` and `traffic/<session_id>/`, never from `proxy.log`.

## Provider usage limit

A usage-limit rejection ends the run. The launcher detects the child's terminal usage-limit result, or `SIDECAR_USAGE_LIMIT_RETRIES` (default 10, the CLI's own `max_retries`) consecutive 429 `api_retry` lines with no work event between them. It kills the child, never resumes it, records `stopReason: provider-usage-limit` with `usageLimitResetsAt` and `rateLimitInfo` when the stream carries them, appends the ledger row and exits 10. Retry and other system lines are not progress for `-Z`.

The first stop writes the exhausted marker `~/.claude/sidecar-exhausted/<transport>.json` (`SIDECAR_EXHAUSTED_DIR` moves it). It expires at the stream's reset time, or 30 minutes after the stop. While it is live, every launch and `--check` on that transport exits 10, and neither `-A` nor `-U` overrides it: route the work to another provider. Owner override: delete the marker, only on the owner's word.

`python3 .claude/tools/sidecar_fanout.py --status <out-dir>` prints one line per job from its latest attempt: `working`, `retrying`, `stalled`, `finished` or `usage-limit`, or `died` when a detached child's pid is gone with no `.exit`. The fan-out summary reports a usage-limit stop as `usage-limit` and names the stream to salvage from.

### Compaction specifics

After auto-compaction the child receives the summary as a user message and, unanchored, answers it and ends. The launcher exports the `-f` path as `CLAUDE_CODE_SIDECAR_PROMPT_FILE` and registers `hooks/sidecar_recompact_reprompt.py` (SessionStart, matcher `compact`) via `--settings`, which re-injects the brief after every compaction.

- Registration rides with the LAUNCHER: a worktree checked out before the hook existed gets no re-prompt.
- Pass the brief path absolute and native — the hook opens it from the child's cwd, which is a worktree or a temp dir.
- Briefs name their on-disk outputs and ask for partial output early — that is the resume path.
- What ends a run is the thrash breaker, not a compaction count: context refilled to the limit within 3 turns of a compaction, 3 times running (`terminal_reason: rapid_refill_breaker`). Keep single tool results small: `-D pointer`, `Read` with `limit`, never a whole `.tres` or patch.
- Force a compaction with `SIDECAR_CONTEXT_TOKENS_OVERRIDE=<tokens>`; under ~80k the harness alone overflows.

Evidence: `auto-memory/archive/gotcha_sidecar_reprompt_rides_with_launcher.md`.

## Detached runs

- **Live until proven gone.** Any run with `-R` and no `<record>.exit` file is still live — the launcher writes that file on every exit path, so its absence is the signal, not a background task's own status. A detached run also names its pid in `<record>.pid`: live until that pid is absent from the process list (`Get-CimInstance Win32_Process` on Windows, `kill -0` in the same MSYS or POSIX shell). Neither form deletes its files, so a relaunch takes a new `-R` path.
- **Harness notices.** A low-memory "stopped" notice on a backgrounded (non-detached) launch has so far killed only the harness's outer shell; the launcher kept running and wrote its own `.exit`/`.out` normally. Any kill or stop, that one included, needs process and record evidence before the run is called dead.
- **Killed Monitor.** A Monitor is a harness task too. Losing one loses only the wake-up: re-arm it on the same `.exit` file (or `EXIT`/progress files for a detached run).

Evidence: `auto-memory/archive/gotcha_harness_killed_notice_is_not_a_dead_sidecar.md`.

## Exit codes

| code | meaning | fix |
|---|---|---|
| 1 | `-X`: record path already used, or the snapshot could not be made | pass a new `-R` path; check `TEMP` |
| 2 | bad flag / unknown alias / registry unreadable; `-X` without `-R` or inside a flag cluster | `model_registry.py available` |
| 3 | credential missing | the launcher header names the file |
| 4 | child CLI or proxy missing | `<launcher> --check` |
| 5 | band below the model's floor | `-A` if deliberate |
| 6 | balance floor / probe failed | not overridable |
| 7 | model marked unavailable | re-select; `-U` only on the user's word |
| 8 | provider's own quota band over ceiling | `-A` if deliberate |
| 10 | provider usage limit, or its exhausted marker is live | another provider (§Provider usage limit) |
| 11 | peak pricing window refused (`gate.peakPolicy: refuse`) | wait for off-peak (`model_registry.py price-window <model>` prints the end); `-W` only on the user's word |

`--check` on any launcher: exit 0 = dispatchable now.
