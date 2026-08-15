---
description: Autonomously provision a new workstation for this harness — verify prereqs, wire MCP servers, sidecar, search index, and run smoke checks
---

# /workstation_setup

Run after cloning the repo (plus Jmodot submodule) onto a fresh machine. The agent executes
every step it can autonomously, verifies each with a concrete check, and ends with a
PROVISIONED / BLOCKED-ON-USER table. Secrets are NEVER created by the agent — only verified
present and reported missing.

**Idempotency:** every phase starts with its verification check; if it passes, skip the phase.
Re-running on a provisioned machine should produce an all-green table and no writes.

## Phase 0 — Repo integrity

1. `git -C <repo> submodule update --init` — Jmodot populated (`Jmodot/` non-empty).
2. `git -C <repo> status` clean; `main` present. If `.claude/baseline.lock.json` exists, note
   that `/sync_baseline pull` is available but do NOT run it unprompted.

## Phase 1 — Toolchain prerequisites (verify, report versions)

| Check | Command | Requirement |
|---|---|---|
| .NET SDK | `dotnet --version` | SDK per `global.json` — **9.0.304**, `rollForward: latestFeature`. csharp-ls ≥0.21 additionally needs the **.NET 10 SDK** (`archive_csharp_lsp_setup_gotchas`; dated — re-verify the pairing on the machine via `dotnet tool list -g` + `dotnet --list-sdks`) |
| claude CLI | `claude --version` | any current version (the DeepSeek sidecar phase assumes it) |
| Godot | locate the mono editor binary for the pinned engine version (`.claude/reference/project_stack.md`; ask user for path if not on PATH) | engine per pin |
| Python | `python3 --version` (hooks) | 3.10+ |
| git + gh | `git --version`, `gh auth status` | gh authenticated |
| Node | `node --version` (workflow scripts) | any LTS |

Missing toolchain items → BLOCKED-ON-USER rows (install links in the report), continue with
remaining phases.

### Three Godot pins — one version, three homes

Godot's version is pinned in **three independent, drift-prone places**; all three must agree on
the project-stack version (`archive_godot_install_location`). Enumerate and verify all three:

1. **`.runsettings` `<GODOT_BIN>`** (gitignored, per-machine) — REGENERATE it from
   `.runsettings.template` with the `{{GODOT_BIN}}` placeholder filled to the pinned engine path.
   Never copy `.runsettings` from another machine and never hand-edit the committed
   `.runsettings.template`.
2. **`~/.claude.json` `mcpServers.godot.env.GODOT_PATH`** (global, user-owned) — VERIFY-ONLY. It
   must resolve to a `Godot_v4.7.1-stable_mono_win64` binary; a wrong value is a BLOCKED-ON-USER
   row naming the exact expected path.
3. **shell `$GODOT_BIN`** (set via `setx GODOT_BIN`) — verify via `$env:GODOT_BIN` read-back,
   REPORT-ONLY.

### Engine runtimeconfig pin — machine-local, re-applied per engine upgrade

Godot resolves its **own** .NET runtime independently of `global.json`: the engine's
`GodotPlugins.runtimeconfig.json` ships `rollForward: LatestMajor` and grabs the newest installed
major (`gotcha_godot_clr_host_rollforward_latestmajor`). Pinning the project therefore takes a
second, machine-local edit. Apply it **only when no Godot process is live**:

- In **both** `<GodotInstall>/GodotSharp/Api/Debug/GodotPlugins.runtimeconfig.json` and
  `.../Api/Release/GodotPlugins.runtimeconfig.json`, set `tfm`/`framework.version` to the
  project's TargetFramework (`net9.0`) and `rollForward` to `LatestMinor`. Back up each as `*.orig`.
- **Verify positively — don't infer from green tests:**
  ```
  COREHOST_TRACE=1 COREHOST_TRACEFILE=<path> <godot.exe> --headless --quit
  grep "Chose FX version" <path>
  ```
  → `Chose FX version [...\Microsoft.NETCore.App\9.0.8]`. A passing suite proves nothing about
  the loaded runtime version.
- Re-apply after every engine upgrade; there is no repo-level lever for the engine path.

### csharp-ls + LSP wiring

The C# LSP is **LOCAL-ONLY** (disabled on cloud via `settings.local.json`). Procedure per
`archive_csharp_lsp_setup_gotchas` (condensed — the archive is authoritative):

1. `ENABLE_LSP_TOOL=1` env var MUST be set, or Claude Code will not connect to the LSP server.
   First thing to verify when LSP appears unavailable.
2. The official `csharp-lsp` plugin ships only a README — hand-write `plugin.json` with a
   `--solution` arg pointing at the canonical `.sln` (multiple worktree `.sln`s confuse
   csharp-ls). The adapter `.claude/tools/csharp-ls-adapter.js` fixes `workspace/configuration`
   and `file://` URI issues specific to csharp-ls on Windows.
3. Omit `gitCommitSha` from the `installed_plugins.json` entry — plugin auto-update re-caches with
   that field present and overwrites the hand-written `plugin.json` with upstream's broken config.
4. `dotnet tool update -g csharp-ls` upgrades the binary; the adapter wrapper applies it (see
   `setup-csharp-ls.sh`). A fresh adapter version needs a full Claude Code restart to be picked up.

## Phase 2 — gdUnit4 patched fork (local NuGet)

The test stack uses a patched `gdUnit4.api` fork (pipe salt via `GDUNIT4_PIPE_SUFFIX` —
`project_gdunit4_fork_pipe_salt.md`). NEVER restore it from nuget.org.

1. Check `dotnet nuget list source` for the local feed the `.csproj`/`nuget.config` references.
2. If the feed or package is absent: the fork's built `.nupkg` must be copied from an existing
   workstation or rebuilt from the fork repo → BLOCKED-ON-USER with the exact feed path expected.
3. Verify: `dotnet build {{PROJECT_NAME}}.csproj` (Bash timeout 600000) succeeds.

## Phase 3 — ai-worker MCP

1. Verify the MCP responds: `ToolSearch("select:mcp__ai-worker__list_models")` → call it.
   Responding → phase done.
2. If absent: config lives at `~/.config/ai-worker/models.yaml` (hot-reloads); install/run
   details are in `ai_worker_model_guide.md` (Obsidian vault) — follow it, don't guess.
3. Credentials: verify `~/.env.ai-worker.cmd` EXISTS and names the DeepSeek + Kimi keys.
   Missing → BLOCKED-ON-USER (user supplies keys; agent never writes key values).
4. Local fallback: `ollama list` shows the qwen-local models; if Ollama absent, note as
   degraded-optional, not blocking.

## Phase 4 — DeepSeek CLI sidecar

1. Script ships with the repo: `.claude/scripts/deepseek_sidecar.sh` — verify present and
   `bash -n` clean.
2. Runtime contract (credential sourcing, uncapped defaults, subagent blocking) is documented
   in the script's own header — read it there. No install step: depends only on Phase 3's env
   file plus the `claude` CLI already present.
3. Smoke: dry-run the script's help/arg-parse path (no billable call).
   `bash .claude/scripts/deepseek_sidecar.sh --check` must print `OK (model=… endpoint=… key=***…)`
   and exit 0. It reports `UNAVAILABLE (model registry unusable: …)` when the registry below is
   missing or invalid — the registry is a hard dependency of every dispatch, so that is a genuine
   availability failure, not a warning.
4. **Model registry** — `.claude/reference/external_models.json` is the SSOT for external model
   ids, versions, prices, limits, role resolution and authorization gates. Verify it parses and
   satisfies its field contracts: `python3 .claude/tools/model_registry.py --check` exits 0.
   A `WARNING: … price.asOf is N days old` is non-fatal but means re-probing the vendor pricing
   page it names — DeepSeek has announced a rise, so a stale rate silently becomes an assumption.
5. **Shell integration** — the `claude-primary`/`claude-secondary` and the three DeepSeek
   functions (`claude-deepseek-pro`, `claude-deepseek-flash`, and bare `claude-deepseek`, which
   drives **pro**) live in `.claude/scripts/claude_profile_functions.ps1` (version-controlled).
   The user's `$PROFILE` must **dot-source** that path, never carry copies of the bodies. Verify:
   `pwsh -NoLogo -NonInteractive -Command "Get-Command claude-deepseek-pro, claude-deepseek-flash, claude-deepseek"`
   resolves all three. If it does not, append the dot-source line to `$PROFILE` (create the file
   if absent) and re-verify. A `$PROFILE` containing inline function bodies is drift — replace
   them with the dot-source. Each function prints a launch banner naming the driving model, its
   version, the unpinned-subagent default, and all three per-1M rates, sourced from the registry;
   a banner showing blank rates means Phase 4's `--check` was skipped.

## Phase 5 — Semantic-search plugin

1. Verify the plugin tool responds: `ToolSearch("select:mcp__plugin_semantic-search_semantic-search__search")`.
   If the plugin isn't installed, it's a Claude Code plugin (DreB) — install via the plugin
   manager, then restart the session (BLOCKED-ON-USER if a marketplace step is interactive).
2. **Windows Node-plugin manual bootstrap** — Node-based plugins that self-bootstrap via
   `execFileSync('npm', ['install'])` FAIL on Windows with `spawnSync npm ENOENT` (`npm` resolves
   only as `npm.cmd`; the spawn won't append `.cmd`), surfacing as a `Status: failed` MCP card
   (`archive_node_mcp_plugin_windows_bootstrap_gotcha`). Pre-populate `node_modules/` and `dist/`
   from a Bash shell where `npm` is callable, so the bootstrap branches become no-ops:
   ```
   npm install --prefix <plugin-cache-dir>
   npm run build --prefix <plugin-cache-dir>
   ```
   where `<plugin-cache-dir>` is `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`.
   Verify with `timeout 3 node <plugin-cache>/bin/server.js` — exit 0 (from timeout) means clean
   stdio wait.
3. The index (`.search-index/search.db`) is gitignored — always absent on a fresh clone.
   Run `/reindex_search` to build it. Verify with one query (e.g. "spell spawn pipeline")
   returning results.

## Phase 6 — Obsidian vault

1. Verify the vault paths resolve:
   `{{VAULT_ROOT}}\DevProjects\{{PROJECT_NAME}}\` and `...\Jmodot\`.
2. If the machine uses a different vault location, report the delta — path references live in
   `.claude/CLAUDE.md` §3 and must be updated by the user (or via an approved edit), since the
   harness hard-codes them.
3. OneDrive sync lag is a known hazard for freshly-synced vaults: spot-read `Worklog.md` to
   confirm content is hydrated, not placeholder stubs.

## Phase 7 — Smoke gate

1. `dotnet build` green (done in Phase 2).
2. One filtered test run per Testing skill rules (`--filter` + `--settings .runsettings`,
   timeout 600000) — pick a small Logic suite; confirm real pass counts (not zero-match).
   **DEFERRED-PEER-CONTENTION:** only run when no peer suite is live — the GdUnit4 named pipe is
   machine-wide single-flight; if a concurrent session holds it, record the exact command and mark
   the row DEFERRED-PEER-CONTENTION for the orchestrator to run later.
3. Godot MCP: `mcp__godot__get_godot_version` returns the project-stack engine version (4.7.1 per
   `.claude/reference/project_stack.md`) — **responsiveness alone verifies nothing.** The MCP
   launches its OWN `GODOT_PATH`-pinned engine from global `~/.claude.json`; a stale pin silently
   downgrades `project.godot` + the csproj SDK pin on contact, and MCP server config changes need
   a Claude Code restart to take effect (`gotcha_godot_mcp_wrong_engine_binary`).

## Report format

End with one table: `| Component | Status (PROVISIONED / DEGRADED / BLOCKED-ON-USER / DEFERRED-PEER-CONTENTION) | Evidence / next action |`
— one row per phase item. BLOCKED-ON-USER rows must state the exact artifact the user supplies
(key file, install, path decision), never "see docs". DEFERRED-PEER-CONTENTION rows must carry
the exact command/path the orchestrator runs later.
