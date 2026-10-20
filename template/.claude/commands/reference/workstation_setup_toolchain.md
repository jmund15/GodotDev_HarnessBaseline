---
disable-model-invocation: true
---

# `/workstation_setup` — Engine runtime pin and csharp-ls wiring

Read at `/workstation_setup` Phase 1 when the engine's runtimeconfig is unpinned after an engine upgrade, or when csharp-ls or the LSP tool is not wired. Entrypoint: [`../workstation_setup.md`](../workstation_setup.md).

## Engine runtimeconfig pin — machine-local, re-applied per engine upgrade

Godot resolves its own .NET runtime, ignoring `global.json`: the shipped
`GodotPlugins.runtimeconfig.json` uses `rollForward: LatestMajor` and loads the newest installed
major. Apply only while no Godot process is live:

- In both `<GodotInstall>/GodotSharp/Api/{Debug,Release}/GodotPlugins.runtimeconfig.json`, set
  `tfm`/`framework.version` to the csproj TargetFramework and `rollForward` to `LatestMinor`.
  Back up each original as `*.orig`. An existing `*.orig` beside a matching file means done.
- Verify the loaded runtime, not green tests:
  `COREHOST_TRACE=1 COREHOST_TRACEFILE=<path> bash .claude/scripts/godot_bin.sh --headless --quit`,
  then `grep "Chose FX version" <path>` names the TargetFramework major.

## csharp-ls + LSP wiring

1. User env `ENABLE_LSP_TOOL=1`, or Claude Code never connects to the server.
2. The official `csharp-lsp` plugin ships only a README: hand-write its `plugin.json` with a
   `--solution` arg naming the canonical `.sln`, launched through `.claude/tools/csharp-ls-adapter.js`
   (fixes csharp-ls `workspace/configuration` and `file://` URIs on Windows).
3. Omit `gitCommitSha` from its `installed_plugins.json` entry; with it, plugin auto-update
   overwrites the hand-written `plugin.json`.
4. `bash .claude/tools/setup-csharp-ls.sh` installs the csharp-ls version `.config/dotnet-tools.json` pins, and the adapter; to upgrade, bump that pin first. A new adapter
   needs a full Claude Code restart.
