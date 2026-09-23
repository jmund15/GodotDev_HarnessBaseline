## Godot Principles
- **Godot-designer-intuitive authoring** ranks with ideal architecture at EVERY stage (design, plan, execute, review). `game_vision` owns game direction.
- Runtime logs are `JmoLogger` output, read via `/analyze_godot_logs` after a user playtest.
- Planning litmus for `.tscn`/`.tres` configuration surfaces: `rules/scene_authoring.md` §Scene anatomy.

## Godot Build & Test
- **Godot/C# tests:** load `testing`; use `--filter` and `--settings .runsettings`, never `--no-build`. Bash timeout is `600000` for tests and Godot-install walks.
- **C# commits:** `/regression_gate` is mandatory, once at drive close. Mid-drive verification uses `scripts/verify.ps1 -Scope <domains>`; `change_control` owns cadence. Pure documentation is exempt from the C# gate.
- After substantial scene/resource wiring, check the MCP Godot version against `reference/project_stack.md`, run the game and inspect debug output. Engine invocations can rewrite assets; inspect their diffs.

## Godot Docs and C# Navigation
- Godot class docs come from `.claude/cache/godot-docs/doc/classes/<Class>.xml`, not the website. `reference/source_trust_godot.md` owns the Godot, GdUnit4 and .NET sources.
- Semantic search does not index `.cs`.
- **C# navigation:** anchor the declaration, then use LSP; do not mix declarations and mentions with a bare identifier grep. Literal values, regex alternatives, comments, attributes and known unique anchors remain valid Grep uses. If LSP is unavailable, use the supported search fallback and name its coverage limit.

## Godot Conventions
PascalCase for C# files/types/directories; snake_case for Godot assets. Invoke the engine as `bash .claude/scripts/godot_bin.sh <args>`, never `"$GODOT_BIN"`.

## Godot Rationalizations to Refuse

| Rationalization | Rule to cite |
|---|---|
| "grep is faster / LSP is slow / it's just one symbol" | §Godot Docs and C# Navigation, C# navigation |
| "it's a cosmetic change / just a rename, skip the gate" | §Godot Build & Test: `/regression_gate` for every `.cs` commit; `change_control` owns cadence |
