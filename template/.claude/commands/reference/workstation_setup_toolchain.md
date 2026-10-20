---
disable-model-invocation: true
stamp-inputs:
  - ".claude/commands/reference/workstation_setup_toolchain.md"
  - "global.json"
  - "nuget.config"
---

# `/workstation_setup` — Engine toolchain phases

Read at `/workstation_setup` Phase 8 when the godot layer is installed; Phases T1–T6 run in order, and T7 runs inside Phase 9. Entrypoint: [`../workstation_setup.md`](../workstation_setup.md). The command's report format and statuses apply to every row here.

`workstation_profile.py stamp` re-runs this layer's cheap checks through `tools/workstation_profile_godot.py`: a .NET SDK on PATH, `GODOT_BIN` resolving to an existing file, and each `nuget.config` local feed folder existing. It refuses on any failure.

## T1 — Toolchain prerequisites

| Check | Command | Requirement |
|---|---|---|
| .NET SDK | `dotnet --list-sdks` | the SDK `global.json` pins (roll-forward per its `rollForward`). csharp-ls ≥0.21 also needs a .NET 10 SDK |
| Godot | the mono editor binary for the pinned version (`.claude/reference/project_stack.md`) | engine per pin; ask the user for the path when absent |

A missing required item → BLOCKED-ON-USER row with its install link; continue with the other phases.

## T2 — Three Godot pins: one version, three homes

The engine version lives in three independent places; all three must match the project-stack pin:

1. **`.runsettings` `<GODOT_BIN>`** (gitignored, per machine). The SessionStart hook generates it from
   `.runsettings.template` only when it is missing. A wrong value → delete the file and start a new
   session after pin 3 is set. Never copy it from another machine or edit the template.
2. **`~/.claude.json` `mcpServers.godot.env.GODOT_PATH`** (user-owned). Missing → §T6 registers it.
   Wrong → BLOCKED-ON-USER naming the exact expected path.
3. **User env `GODOT_BIN`** — read back `[Environment]::GetEnvironmentVariable('GODOT_BIN','User')`.
   Absent → set it to the pinned mono exe with `SetEnvironmentVariable(..., 'User')`; new terminals
   see it. Without it, every `[RequireGodotRuntime]` test passes without running.

## T3 — Engine runtimeconfig pin: machine-local, re-applied per engine upgrade

Godot resolves its own .NET runtime, ignoring `global.json`: the shipped
`GodotPlugins.runtimeconfig.json` uses `rollForward: LatestMajor` and loads the newest installed
major. Apply only while no Godot process is live:

- In both `<GodotInstall>/GodotSharp/Api/{Debug,Release}/GodotPlugins.runtimeconfig.json`, set
  `tfm`/`framework.version` to the csproj TargetFramework and `rollForward` to `LatestMinor`.
  Back up each original as `*.orig`. An existing `*.orig` beside a matching file means done.
- Verify the loaded runtime, not green tests:
  `COREHOST_TRACE=1 COREHOST_TRACEFILE=<path> bash .claude/scripts/godot_bin.sh --headless --quit`,
  then `grep "Chose FX version" <path>` names the TargetFramework major.

## T4 — csharp-ls + LSP wiring

Local only; cloud sessions disable it:

1. User env `ENABLE_LSP_TOOL=1`, or Claude Code never connects to the server.
2. The official `csharp-lsp` plugin ships only a README: hand-write its `plugin.json` with a
   `--solution` arg naming the canonical `.sln`, launched through `.claude/tools/csharp-ls-adapter.js`
   (fixes csharp-ls `workspace/configuration` and `file://` URIs on Windows).
3. Omit `gitCommitSha` from its `installed_plugins.json` entry; with it, plugin auto-update
   overwrites the hand-written `plugin.json`.
4. `bash .claude/tools/setup-csharp-ls.sh` installs the csharp-ls version `.config/dotnet-tools.json` pins, and the adapter; to upgrade, bump that pin first. A new adapter
   needs a full Claude Code restart.

## T5 — NuGet feeds

1. For each `<packageSource>` in `nuget.config` that maps a local folder, verify the folder holds
   every package its `<packageSourceMapping>` names. Never restore a mapped package from nuget.org:
   the gdUnit4 fork carries a pipe-salt patch that nuget.org's package lacks.
2. A missing `.nupkg` → BLOCKED-ON-USER: copy it from an existing workstation or rebuild the fork,
   naming the exact feed path.
3. `dotnet build {{PROJECT_NAME}}.csproj -consoleLoggerParameters:ErrorsOnly` succeeds; run it in the
   background (Bash timeout 600000) beside the phases that do not need it. The smoke gate reuses it.

## T6 — Godot MCP and docs cache

1. `mcp__godot__get_godot_version` returns the project-stack version. A response alone proves
   nothing: the server launches its own `GODOT_PATH` engine, and a stale pin silently downgrades
   `project.godot` and the csproj SDK on contact.
2. Absent: godot-mcp is a separate Node project. Clone it, run `npm install && npm run build`, and
   register it at user scope: `"godot": {"command": "node", "args": ["<clone>/build/index.js"],
   "env": {"GODOT_PATH": "<mono exe>"}}` under `mcpServers` in `~/.claude.json`. Restart Claude Code.
   The repo `.mcp.json` stays empty.
3. `bash .claude/scripts/godot_docs_cache.sh` builds the gitignored `.claude/cache/godot-docs/`.
   Done when SessionStart no longer reports the cache stale.

## T7 — Smoke checks (inside the command's Phase 9)

1. The T5 build is green.
2. One small Logic suite via `testing` rules (`--filter`, `--settings .runsettings`, in the
   background) reports real pass counts, not zero-match. Runs take this checkout's test lock plus a
   runtime slot: while a suite on this checkout is live, or `LOCK_TIMEOUT` shows the slots full,
   mark the row DEFERRED-PEER-CONTENTION with the exact command.
3. Godot MCP boots the project: `mcp__godot__run_project`, then `get_debug_output` shows no startup
   errors, then `stop_project`.

## Tool updates — report only

Rows for the command's Tool updates table, in the same `UPDATE-AVAILABLE <installed> → <latest>` form:

| Tool | Installed | Latest | Upgrade owner |
|---|---|---|---|
| csharp-ls | `dotnet tool list -g` | `dotnet tool search csharp-ls --take 1` | §T4 step 4 |
| Godot engine | the `reference/project_stack.md` pin | the engine's release feed | the stack-update owner `reference/project_stack.md` names |
