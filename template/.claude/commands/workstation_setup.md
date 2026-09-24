---
description: Provision a new workstation for this harness — toolchain, pins, MCP, sidecars, index, smoke checks
---

# /workstation_setup

Run after cloning the repo onto a fresh machine, or re-run to audit one. The agent executes every
step it can, verifies each with a concrete check, and ends with the report table below. Secrets are
never created by the agent — only verified present and reported missing. `environment_bootstrap`
holds this machine's ground truth and troubleshooting; this command owns provisioning.

**Idempotency:** every phase starts with its verification check; a passing check skips the phase.
A provisioned machine produces an all-green table and no writes.

## Phase 0 — Repo integrity

1. `git -C <repo> submodule update --init --recursive`; `git -C <repo> submodule status` shows no
   `-` (uninitialized) prefix.
2. `git -C <repo> status` clean; `main` present.
3. Harness baseline: `python3 .claude/tools/baseline_sync.py check --strict`. Exit 0 → PROVISIONED.
   A non-zero exit is reported with its states; `/sync_baseline` owns the follow-up. Do not pull
   unprompted.

## Phase 1 — Toolchain prerequisites (verify, report versions)

| Check | Command | Requirement |
|---|---|---|
| .NET SDK | `dotnet --list-sdks` | the SDK `global.json` pins (roll-forward per its `rollForward`). csharp-ls ≥0.21 also needs a .NET 10 SDK (`archive_csharp_lsp_setup_gotchas`) |
| claude CLI | `claude --version` | current; the sidecars and the MCP idle-timeout knob (Phase 3) depend on it |
| Godot | the mono editor binary for the pinned version (`.claude/reference/project_stack.md`) | engine per pin; ask the user for the path when absent |
| Python | `python3 --version` from the Bash tool, not a WSL shell | 3.10+; UTF-8 mode comes from `settings.json` `env.PYTHONUTF8` |
| git + gh | `git --version`, `gh auth status` | gh authenticated (baseline publish and PRs) |
| Node | `node --version` | any LTS (workflow scripts, Node MCP servers) |
| WSL | `wsl -l -q` lists a distro with `python3` | optional: only `baseline_linux_preview.py` needs it → DEGRADED when absent |

A missing required item → BLOCKED-ON-USER row with its install link; continue with the other phases.

### Three Godot pins — one version, three homes

The engine version lives in three independent places; all three must match the project-stack pin
(`archive_godot_install_location`):

1. **`.runsettings` `<GODOT_BIN>`** (gitignored, per machine). The SessionStart hook regenerates it
   from `.runsettings.template` when `GODOT_BIN` resolves. Verify its value; never copy it from
   another machine or edit the template.
2. **`~/.claude.json` `mcpServers.godot.env.GODOT_PATH`** (user-owned) — VERIFY-ONLY. A wrong or
   missing value → BLOCKED-ON-USER naming the exact expected path.
3. **User env `GODOT_BIN`** — read back `[Environment]::GetEnvironmentVariable('GODOT_BIN','User')`.
   Absent → set it to the pinned mono exe with `SetEnvironmentVariable(..., 'User')`; new terminals
   see it. Without it, every `[RequireGodotRuntime]` test passes without running.

### Engine runtimeconfig pin — machine-local, re-applied per engine upgrade

Godot resolves its own .NET runtime, ignoring `global.json`: the shipped
`GodotPlugins.runtimeconfig.json` uses `rollForward: LatestMajor` and loads the newest installed
major (`gotcha_godot_clr_host_rollforward_latestmajor`). Apply only while no Godot process is live:

- In both `<GodotInstall>/GodotSharp/Api/{Debug,Release}/GodotPlugins.runtimeconfig.json`, set
  `tfm`/`framework.version` to the csproj TargetFramework and `rollForward` to `LatestMinor`.
  Back up each original as `*.orig`. An existing `*.orig` beside a matching file means done.
- Verify the loaded runtime, not green tests:
  `COREHOST_TRACE=1 COREHOST_TRACEFILE=<path> bash .claude/scripts/godot_bin.sh --headless --quit`,
  then `grep "Chose FX version" <path>` names the TargetFramework major.

### csharp-ls + LSP wiring

Local only; cloud sessions disable it. `archive_csharp_lsp_setup_gotchas` is authoritative:

1. User env `ENABLE_LSP_TOOL=1`, or Claude Code never connects to the server.
2. The official `csharp-lsp` plugin ships only a README: hand-write its `plugin.json` with a
   `--solution` arg naming the canonical `.sln`, launched through `.claude/tools/csharp-ls-adapter.js`
   (fixes csharp-ls `workspace/configuration` and `file://` URIs on Windows).
3. Omit `gitCommitSha` from its `installed_plugins.json` entry; with it, plugin auto-update
   overwrites the hand-written `plugin.json`.
4. `bash .claude/tools/setup-csharp-ls.sh` installs or upgrades the binary and adapter. A new adapter
   needs a full Claude Code restart.

## Phase 2 — NuGet feeds

1. For each `<packageSource>` in `nuget.config` that maps a local folder, verify the folder holds
   every package its `<packageSourceMapping>` names. Never restore a mapped package from nuget.org:
   the gdUnit4 fork carries a pipe-salt patch (`project_gdunit4_fork_pipe_salt`).
2. A missing `.nupkg` → BLOCKED-ON-USER: copy it from an existing workstation or rebuild the fork,
   naming the exact feed path.
3. `dotnet build {{PROJECT_NAME}}.csproj -consoleLoggerParameters:ErrorsOnly` succeeds (Bash timeout 600000).

## Phase 3 — ai-worker MCP

1. `ToolSearch("select:mcp__ai-worker__list_models")`, then call it. A response → go to step 3.
2. Absent: config lives at `~/.config/ai-worker/models.yaml` (hot-reloads); install per
   `ai_worker_model_guide.md` in the vault.
3. **Classify the box before installing any local arm.** Read total VRAM (`nvidia-smi
   --query-gpu=memory.total --format=csv,noheader`): **≥16 GB** install the measured roster;
   **8–16 GB** only the small arms, leaving 27B-backed roles unset; **<8 GB or no GPU** no local arms.
4. **An armless ai-worker is a valid end state** → DEGRADED, never BLOCKED-ON-USER. Bulk reads use
   the CLAUDE.md §Tool Routing offline fallback.
5. **Single-GPU concurrency env — report, don't set.** Concurrent sessions share one GPU and
   `server.py` has no queue. `OLLAMA_NUM_PARALLEL=1` bounds requests per loaded model;
   `OLLAMA_MAX_LOADED_MODELS` defaults to 3× the GPU count, so also set it to `1`
   (`gotcha_local_llm_measurement_discipline`). A missing value is a report row; the change is the user's.
6. **`OLLAMA_KV_CACHE_TYPE` must be `q4_0`** — BLOCKED-ON-USER otherwise. The pinned `qwen-local`
   build degenerates under `q8_0`; higher precision is not safer here (evidence: the `qwen-local`
   block in `models.yaml`). The value takes effect only after an Ollama restart, so confirm it
   against the ledger's `resident_bytes`, not the declared value.
7. **MCP idle timeout vs call budget.** Claude Code aborts a stdio tool call after 30 min without a
   response or progress notification; ai-worker sends neither. Read `request_timeout_local` and
   `request_timeout_cloud` from `list_models`: each must sit below the client's bound so a failure
   is legible server-side, else report the collision. The client knob is
   `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT` (ms), not `MCP_TIMEOUT`; it needs CLI ≥ v2.1.203.
8. Credentials: `~/.env.ai-worker.cmd` supplies cloud keys. Only providers
   `reference/external_models.json` marks available need one. The agent never writes key values.

## Phase 4 — Model registry and sidecars

1. `python3 .claude/tools/model_registry.py --check` exits 0. It is a hard dependency of every
   sidecar. A `price.asOf is N days old` warning means re-probing the vendor page it names.
2. For each `.claude/scripts/*_sidecar.sh` with a `--check` mode, run `bash <script> --check` (no
   billable call): `OK (…)` → PROVISIONED. `UNAVAILABLE` for a provider the user does not use →
   DEGRADED with the reason printed; for one they do use → BLOCKED-ON-USER naming the missing key
   or install.
3. **Shell integration.** `.claude/scripts/claude_profile_functions.ps1` defines the `claude-*`
   launch functions. The user's `$PROFILE` must dot-source it, never carry copies of the bodies.
   Verify: `pwsh -NoLogo -NonInteractive -Command "Get-Command claude-primary, claude-deepseek, claude-gpt"`.
   If it fails, wire the dot-source and re-verify. Inline function bodies in `$PROFILE` are drift:
   replace them with the dot-source.
   `$PROFILE` is OneDrive-synced, so one file serves every workstation: append this machine's repo
   root to the profile's `$ClaudeRepoRoots` probe list, keeping other machines' entries. Edit
   `OneDrive\Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1`; the PS7 profile forwards
   to it. A launch banner with blank rates means step 1 failed.

## Phase 5 — Semantic-search plugin

1. `ToolSearch("select:mcp__plugin_semantic-search_semantic-search__search")`. Absent → install the
   DreB plugin through the plugin manager and restart the session (BLOCKED-ON-USER when a
   marketplace step is interactive).
2. **Windows Node-plugin bootstrap.** A plugin that self-installs through
   `execFileSync('npm', ['install'])` fails with `spawnSync npm ENOENT`, shown as a `Status: failed`
   MCP card (`archive_node_mcp_plugin_windows_bootstrap_gotcha`). Pre-build it from Bash:
   `npm install --prefix <plugin-cache-dir>` then `npm run build --prefix <plugin-cache-dir>`, where
   `<plugin-cache-dir>` is `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`. Verify:
   `timeout 3 node <plugin-cache-dir>/bin/server.js` exits 124 with empty stderr (a clean stdio
   wait; with stdin at EOF it exits 0 instead).
3. `.search-index/search.db` is gitignored, so a fresh clone lacks it: run `/reindex_search`, then
   one query naming a known subsystem returns results.

## Phase 6 — Godot MCP and docs cache

1. `mcp__godot__get_godot_version` returns the project-stack version. A response alone proves
   nothing: the server launches its own `GODOT_PATH` engine, and a stale pin silently downgrades
   `project.godot` and the csproj SDK on contact (`gotcha_godot_mcp_wrong_engine_binary`).
2. Absent: godot-mcp is a separate Node project. Clone it, run `npm install && npm run build`, and
   register it at user scope: `"godot": {"command": "node", "args": ["<clone>/build/index.js"],
   "env": {"GODOT_PATH": "<mono exe>"}}` under `mcpServers` in `~/.claude.json`. Restart Claude Code.
   The repo `.mcp.json` stays empty.
3. `bash .claude/scripts/godot_docs_cache.sh` builds the gitignored `.claude/cache/godot-docs/`.
   Done when SessionStart no longer reports the cache stale.

## Phase 7 — Obsidian vault

1. The vault paths CLAUDE.md §3 names resolve under `{{VAULT_ROOT}}\DevProjects\`.
2. A different vault location on this machine → report the delta. The harness hard-codes the root,
   so the user changes it, or approves the edit.
3. Spot-read `Worklog.md`: content, not OneDrive placeholder stubs.

## Phase 8 — Smoke gate

1. Build green (Phase 2).
2. One small Logic suite via `testing` rules (`--filter`, `--settings .runsettings`, timeout
   600000) reports real pass counts, not zero-match. The GdUnit4 pipe is machine-wide
   single-flight: while a peer suite is live, mark the row DEFERRED-PEER-CONTENTION with the exact
   command.
3. Godot MCP boots the project: `mcp__godot__run_project`, then `get_debug_output` shows no startup
   errors, then `stop_project`.

## Report format

End with one table: `| Component | Status | Evidence / next action |`. Status is one of
PROVISIONED, DEGRADED, BLOCKED-ON-USER or DEFERRED-PEER-CONTENTION; one row per phase item.
A BLOCKED-ON-USER row names the exact artifact the user supplies (key file, install, path
decision). A DEFERRED row carries the exact command to run later.
