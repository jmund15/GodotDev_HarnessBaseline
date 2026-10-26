# Sidecar Dispatch — one recipe, every transport

A sidecar is a separate client process on a chosen transport. `orchestration` §0 owns route selection. Claude-client launchers use `scripts/lib/sidecar_common.sh` for flags, disclosure and recovery. A project's native OpenCode launcher, where it ships one, runs `opencode run` with file attachments and its own permission configuration. Use its flag validation rather than assume Claude-client disclosure, hooks or resume support. The disclosure, tool-profile and permission sections below, and `reference/sidecar_runs.md`'s resume section, describe Claude-client launchers.

## Procedure

1. **Model ladder:** the read-before-pin rule is `orchestration` §5; `hooks/sidecar_dispatch_context.py` denies a launch while the session has not read the ladder since compaction, naming the load route.
2. **Roster + launcher:** `python3 .claude/tools/model_registry.py available` — prints available aliases, roles, effort evidence and paying-plan launchers. Effort displays measured effort levels or an evidence label. Dollar input rates appear only for priced non-plan-quota rows. Launcher = the `launcher` of the plan you chose to pay; `for-role` prints it. A plan may share another transport's models (`planOf`). The session's own plan runs native; any other plan runs through its `launcher`. Excluded models are out; re-select under the ladder (`reference/model_ladder_evidence.md`) — **a stale route name in a command is not a reason to fall back to the session's own transport.**
3. **Dispatch facts + live capacity:** `<launcher> --check -m <alias> -e <effort> -G <profile> [-Q <tier>] -f <prompt.md>` prints the requested pin, brief bytes and estimated tokens, the brief plus its named files against the point the job compacts at (with how far over), the resolved tier (see Tool profile) and matching ladder row with its effort, and the latest shape rating for that profile/model with its note (or none), then reads the current seat's plan band and the target provider's normalized quota/balance before any model request. It distinguishes a dispatch floor from exhausted quota (both 10), insufficient balance (6), authentication (3) and unverifiable/malformed capacity or an unreadable target band (8). A floor refusal names its binding window, used share, dispatch floor, mid-step runout basis and reset; allowance remains. A plan-quota window refuses at its dispatch floor, on exhaustion or on a band it cannot read, never on a pressure ceiling; which plan pays is your choice by the model ladder's selection line. `-A` overrides only the host-band floor. The dispatch hook runs this preflight and denies on any refusal. Before it, the hook runs the Workflow route's tier check (`hooks/_dispatch_checks.py`) on the launch and on each fan-out job: a job whose resolved tier (see Tool profile) the alias's row does not serve is denied. Each plan's quota left against its window left is the `[budget-posture]` line, and a row's owner probation mark prints on `model_registry.py for-role <tier>`; both are read before the pin.
4. **Prompt to a file** (never argv — Windows argv caps at ~8K). Schema to a file when you need structured output.
5. **Dispatch** — one call per job, backgrounded or not; every field below is required except the bracketed ones. `-R` and `-l` must resolve to literal tracking values. The hook expands known preceding shell assignments. Unresolved variables and loop-carried values are denied. Several jobs go in a jobs file for `tools/sidecar_fanout.py`.

```bash
bash .claude/scripts/<launcher> -m <alias> -e <effort> -D <bare|pointer|full> -G <survey|review|author|any|arm> [-Q <tier>] -f <prompt.md> [-S <schema.json>] -R <record.json> -l "<label>" -d "<repo-root>"
```

After `-R` is parsed and its directory remains writable, the launcher's EXIT trap writes `<record>.exit`. Captured output writes `<record>.out`. Detached stdout redirection can create that file empty before the child runs. File existence alone is not captured output. Errors before a valid `-R` may create neither file. A launch ending after its label or `-R` is parsed writes a spend-ledger row unless its resolved ledger path is empty (`-L ""` disables the row). The row uses the published record, or `outcome: ended-before-result` with its exit code and refusal line when no result exists (`finished-without-record` for a finished run without `-R`).

Run it as one command from the project root: no `cd`, `export`, env prefix, redirection or
chaining. A tracked `survey`, `review` or `any` launch with a passing preflight and fresh ladder
Read auto-approves only in the narrow `hooks/approving_gates.py` shape: `-R` names a new scratch file, and optional `-P` must do so when supplied. Optional `-L` may name an existing scratch ledger. No `-A`, `-W`, `-U`, `-X` or `-p` grants may be present. Other shapes face the
normal permission checks.

A full review requesting exactly `-t +Bash` after `-G review` and before `-f` is **not**
read-only. Its consent shape requires `-m`, `-e`, `-D full`, `-G review`, `-t +Bash`,
`-f`, `-S`, `-R`, `-P`, `-l` and `-d` (optional `-C`). The hook validates the whole
command, tracked launcher, project paths, model preflight and ladder Read, then returns `ask`.
The project `permissions.ask` rule `Bash(bash .claude/scripts/*_sidecar.sh * -t +Bash *)` guarantees
a visible prompt for every launch it matches, including shapes the hook does not validate; whether
the hook's own `ask` surfaces under auto mode is unmeasured. Approve the **exact command once**, only if shell access for that provider and brief is intended; the rule prompts on every matching launch, so an approval covers that command only. If no prompt appears, stop and reload
settings or start a new session; do not treat a successful dispatch as proof consent was collected.
A wider tool grant or shell compound is not covered.

**Detached form, for a run that must outlive this session:** add `-X -P <progress.jsonl>` and run the call in the foreground. `-X` must be its own argument (never a cluster like `-AX`) and needs `-R`. The launcher starts a detached job and returns within seconds, printing `DETACHED pid=`, `OUT=`, `ERR=` and `EXIT=`; its own exit is not the run's. Watch it with step 6's command; inspect the process and record before any kill. Each `-R` path takes one launch: a path whose `.out`, `.err`, `.exit` or `.pid` exists refuses with exit 1.

A backgrounded launch is not denied merely for being backgrounded; ladder, capacity, availability, and pricing gates still apply.

6. **Watch — one route for every sidecar, Workflow, bench campaign and long Bash background job.** Arm one session watcher when none of yours is armed: run `python3 .claude/tools/watch_records.py --session --wake` with Bash `run_in_background` and `timeout` 7200000 (the default 30-minute limit ends it before its check-in). Issue a launch and its arm in one message with a bare `--await-launch` on the arm: it waits for the launch entry the launch hook writes instead of exiting `NONE`, and both calls share one request. A re-arm after a wake takes no flag. A backgrounded arm beside a live watcher exits `COVERED` at once, and that exit is a wasted wake. It re-reads this session's pending sidecars, running Workflow runs, live bench rosters and running Bash background tasks (a task with no end notice counts as live work, so its check-in lands inside the cache window) each poll and wakes you when one
   needs a decision (`reference/sidecar_runs.md` §Detached runs, *What wakes you*). On a wake, act on its output without a tail or cat call, then re-arm: it carries every job that ended since the last wake (a sidecar, a fan-out job, a Workflow run or a background shell) with its exit, result head, report path and verification counts, then an `UNRATED` line. Decide dispatch units at launch time: jobs
   whose results gate a later job are separate units, and you start the next when you wake, while
   independent jobs share one fan-out that wakes you once, because every wake is a turn. The Stop hook blocks while a running sidecar or a live bench roster this session started has no live watcher unless the payload already sets `stop_hook_active`. When a stopped notice arrives for a backgrounded launch with `-R`, read `<record>.exit` first.

7. **Consume:** the wake's result head opens the answer; for the whole of it, wait for `<record>.exit` (or prove the process gone), then read
`<record>.claims.json`: its `result` holds the answer and its `claims` carry applied verdicts.
The host writes it and `<record>.post_check.json` before publishing a direct launch's record or
completion, on ordinary and detached paths. Read its `verification` under
`commands/agents/orchestrator_action_protocol.md` Step 1.5; an `unavailable` report is UNCHECKED. The Stop audit reads the same
checked file. `<record>.out` remains unchanged captured stdout for diagnosis, not consumption;
`-o` names its format, not a path. For a long job, include `-P <progress.jsonl>` at launch:
it selects stream-json for compaction/transient recovery. A gate refusal with no captured
output means no answer, not a successful empty review. The `-R` record holds `costUSD`
(list-price tokens; `costBasis` says whether it is spend or a plan-quota comparison figure; never `total_cost_usd`: that payload is inflated and no divisor corrects it), `servedModel` and attestation; `tools/run_cost.py` prices any record or result the same way. Rating follows `/orchestration_metrics` §Incremental rating.
If a completed review's fold failed, run `sidecar_fanout.py <jobs.json> --out-dir <finished-run> --refold` with its `--fold-*` and `--ledger-*` flags: it dispatches no seat and keeps prior artifacts (tool usage owns attempt paths).

A fan-out is `tools/sidecar_fanout.py`, which detaches every child with `-X` so killing the fan-out's own process stops none of them. Launch it with `--detach` in a foreground call: it validates, hands the run to a detached driver and returns, and the step-6 watcher wakes you. A job whose named plan has no room, or whose route has no plan with room, is refused in that call, by the dispatch hook from cached capacity and by the planning pass; a stale or unreadable reading decides nothing, and a queued job meets its launcher's capacity gate when it starts. A driver held in a backgrounded Bash call ends at the tool's background limit, stranding its queued jobs and its `<label>.md` writes. The alternative is N backgrounded launcher calls, each with its own `-R`, relying on the same default durable files. Capture bytes do not prove a run ended: a retry waits for that run's `EXIT` file, or for its process to be proven gone, and takes a new `-R` path. The fan-out's jobs file may be a `dispatch.js` args file (its docstring owns the job spec). A job's final message writes `<out-dir>/<label>.md` only when a final message exists and the write succeeds. The parent treats an absent required artifact as uncovered. A claims fan-out skips child launcher checks, verifies once with `--root <subject-root>`, and publishes `post_check_claims.json` and `claims.json`; consume `claims.json` after the driver finishes, not the raw spills or event stream. For jobs without claims, consume `<label>.md`. Several jobs run at once receive `dispatch.js`'s single-flight guard as a `-C` file.

A review panel is `sidecar_fanout.py <jobs.json> --review` (jobs from `lens_briefs.py --target sidecar --plan <review_ledger.py plan output>`). It writes `review.json`: every seat's coverage, source F-ids, exact same-file same-claim M-id groups and quote verdicts. `--review-artifact <review.json> --select <F-or-M-id>` returns the one held finding it names, with its original contributors. Adding `--fold-alias <alias> --fold-effort <effort> --fold-subject <root>::<snapshot manifest>::<file>[,<file>...]` (repeatable) launches one author per subject that received findings, working in the subject's `root`, and follows `commands/agents/review_fold.md` (`--fold <template>` names another brief); `--ledger-stage`, `--ledger-subject` and `--ledger-roster` record the panel. Every `--review` run, folded or not, ends by running `tools/post_check.py review` in-process and writing `post_check.json`, in which `tools/fold_check.py` decides each folded subject. Consumption follows `commands/agents/orchestrator_action_protocol.md` Step 1.5.

## Agent type = `-D` × `-G`

| type | flags | child sees | use for |
|---|---|---|---|
| scout | `-D bare -G survey` | repo files + delegate guards, no project context | locate / enumerate / extract |
| lens | `-D pointer -G survey` | + `MEMORY.md` | surveys that must dodge known traps |
| reviewer | `-D full -G review` | full harness | red-team, plan check, verdict lenses |
| author | `-D full -G author` | full harness | code/doc authoring, TDD under a spec |
| judge | `-D full -G any` | full harness | judgment fitting no other row |

Pick the lowest tier the child's work needs, on every model. A bare or pointer child starts at about 21–25K tokens and a full one at about 37K; the child pays that load every turn, it takes window room, and on a row with a long-context price step it can push a request past the step. An omitted `-D` is `pointer` on `-G survey` and `full` otherwise.

Effort follows the ladder cell for the role and work shape. A `see-ladder` row names that home instead of an effort level. Treat an `unmeasured` row as a guess, not a defensible pin. Legal `-e` values come from `model_registry.py effort-values <alias>`: the row's declared values narrowed by the transport's vocabulary, or the transport's values when the row declares none.

**`-D` is window-relative.** A readable child transcript supplies `turn1ContextTokens`/`turn1ContextPct` in `-R`. Unavailable measurements are null and print no context line. Do not substitute an estimate for an observation. Size `-D` against the receiving model's observed first-turn context and usable window; choose `pointer` for a scout/lens unless its actual work requires full doctrine. Split or narrow inputs when they will not fit. The brief is the bigger lever — hand paths and Grep targets, never the input.

## Optional flags

`-C <file>` extra context (repeatable) · `-a <dir>` extra read grant (e.g. the vault) · `-t <csv>` tool profile delta or legacy replacement (table below) · `-B <csv>` an `arm` job's task built-ins · `-p <mode>` permission mode (default by transport, §Permission mode) · `-P <file>` live progress stream (do not redirect the canonical Bash command; redirection defeats its narrow approval) · `-T <sec>` wall-clock kill (default OFF; the run is killed with exit 124, so pass it only when you can name why) · `-Z <sec>` stall signal on the `-P` stream (set above the longest silence seen on healthy runs of that transport, and pass it when that silence exceeds the default `SIDECAR_STALL_SEC` in `scripts/lib/sidecar_common.sh`; 0 off; a run silent that long writes `<record>.stall` and keeps running) · `-r <id>` resume a persisted session · `-K <tokens>` the Anthropic child's compaction window, 100000..1000000 (`reference/sidecar_runs.md` §Compaction specifics) · `-M` long context past a row's long-context price step, refused on a row without one; it holds the max window for the whole run, so pass it only to resume (`-r`) a run already past the default window, never on a fresh job (`reference/codex_session_runbook.md` §Launch) · `-W` dispatch inside a peak pricing window on a `gate.peakPolicy: refuse` row. Pass `-W` only when the user explicitly authorized peak pricing in the current conversation; `-A` does not cover it.

`-C` is appended as **SYSTEM instructions**, not a passive data attachment. The tool-profile
scanner checks both `-f` and each `-C` file for tool-requiring commands. When a reviewer must
judge a plan or rubric as data, label it DATA and blockquote its lines; a bare `Run python3`
there is an instruction and needs Bash. Keep a command in the actual task brief only when the
child should execute it. Do not grant Bash merely to review a plan's shell examples.

### Tool profile

**Resolved tier.** A job's tier is the work's kind, not its tool grant: the job's written `tier` (`-Q <tier>` on a launch, `tier` on a fan-out job), else its `-G`/`shape` row's tier in `guards/tool_profiles.json`. The pin must serve that tier (`hooks/_dispatch_checks.py` `job_tier` and `tier_fit`); an unknown tier refuses with the known list, and a refusal names the tier and where it came from. The Workflow hook applies the same rule to a `dispatch.js` job's `tier`. Read-only architect work (a design or plan review, a codify derivation) writes `tier: architect` on whatever shape grants its tools.

`guards/tool_profiles.json` is the one table (a `shape` maps to its built-ins and its logical MCP servers); `tools/tool_profiles.py` is the one resolver every launcher, `tools/sidecar_fanout.py` and the Workflow hook read. Each launcher passes `--tools`, `--strict-mcp-config` and `--mcp-config` from that resolved profile, and `-R` records it as `toolProfile` (`shape`, `tier` (the resolved tier above, when there is one), `builtins`, `mcpServers`, `add`, `remove`, `overrideMode`, `enforcement: strict`, `mcpRegistrations`); `orchestration_metrics` archives the record's `toolProfile.tier` and `parentSessionId` as the row's role tier and session.

`-t` is a DELTA when every entry carries a sign (`+Bash,-Grep,+mcp:godot`); the unsigned
legacy absolute list applies only to this launcher flag, not to a Workflow job's `tools`
field. A Workflow job takes a signed string/list or a structured `tools.add`/`tools.remove`
delta; an unsigned or malformed value refuses rather than losing a requested grant.
Only for `-G arm`, the task row supplies the arm's built-ins. `-B <csv>` carries that row
when a cell also adds a delta: `-G arm -B Read,Glob,Grep -t +mcp:ai-worker` is legal,
while `-G arm -t +mcp:ai-worker` alone is not. An unregistered server, unknown shape,
invalid removal or mixed CSV refuses before spend.

**A dispatch is never granted `Workflow` or `Agent`**: orchestration stays with the calling session. The CLI could grant them, so this is policy: the resolver refuses both in any `-t`, delta or arm task row (`RESERVED_BUILTINS` in `tools/tool_profiles.py`). The one exception is the `bench-score` shape row, the score daemon's driver, whose whole job is one `Workflow(score_cell.js)` call.

## Permission mode

Provider-transport children launch with `bypassPermissions`: on those seats the auto-mode classifier runs on the provider model through the proxy and denies ordinary scratch and test writes. Under bypass, project hooks (heredoc, kill, harness-edit, gate cadence, dispatch guards) and settings deny rules still decide. Evidence: vault `DevProjects/{{PROJECT_NAME}}/Claude/Meta/Benchmark/harness-guidance-2026-09/permission-mode/`. The anthropic transport keeps `auto`. Pass `-p` only to override a default for a stated reason; never pass it to retry an action a hook or a deny rule refused.

Every child also gets the **autonomy rail** (`sidecar_common.sh` `sc_build_disclosure`, all tiers, all models): it runs unattended, never ends on a question, decides and states the assumption, reports a blocker inside the deliverable. At consumption, the parent rates a question-only final message as no delivery.

Never: `-n` (a turn cap discards a paid run) · an unverified `CCP_BIN` override (`environment_bootstrap` owns the machine detail) · a pin from ANOTHER transport's vocabulary inside Workflow `agent()` — role names on a provider session, vendor ids on an Anthropic one. Pin the session transport's own ids; `hooks/workflow_provider_guard.py` denies the other and names the roster.

## After launch

`reference/sidecar_runs.md` owns what happens once a run starts: resume, provider usage limits, compaction internals, detached-run wakes and exit codes. Read it when a run ends, refuses, stalls or wakes you. Its rules that shape the brief at launch:

- Pass the brief path absolute and native: the re-prompt hook opens it from the child's cwd, which is a worktree or a temp dir.
- A job whose `-d` is a linked worktree cannot Write, Edit or NotebookEdit into the main checkout: `hooks/sidecar_workdir_guard.py` denies it, except under the job's spill dir (the directory of its `-R` record). Name every file the job writes by a path inside its worktree or spill dir, and its report under the spill dir. A Bash command with a clear write there (a redirect, `tee`, a `cp`/`mv`/`install` destination, `sed -i`, a mutating `git -C <main>`, a python path literal beside a write call) is denied the same way; a target it cannot resolve statically is allowed.
- Name the brief's on-disk outputs and ask for partial output early: that is the resume path.
