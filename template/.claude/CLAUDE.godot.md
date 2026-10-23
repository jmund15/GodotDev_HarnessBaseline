## Godot Principles
- **Godot-designer-intuitive authoring** ranks with ideal architecture at EVERY stage (design, plan, execute, review). `game_vision` owns game direction.
- Runtime logs are `JmoLogger` output, read via `/analyze_godot_logs` after a user playtest.

## Godot Build & Test
- **C# commits:** `/regression_gate` is mandatory, once at drive close. Mid-drive verification follows `change_control` §Gate cadence. Pure documentation is exempt from the C# gate.
- A Godot/C# test run is a long job: start it with `run_in_background` (`CLAUDE.core.md` §Shell Discipline).
- Run the game and inspect debug output after substantial scene/resource wiring and before accepting a delegate diff that could clip scene methods or resource references. Every engine invocation, headless import included, may write; resaves can drop failed resources and array references, so inspect the diffs.

## Godot Docs and C# Navigation
- Godot class docs come from `.claude/cache/godot-docs/doc/classes/<Class>.xml`. `reference/source_trust_godot.md` owns the Godot, GdUnit4 and .NET sources.
- Semantic search covers `.cs` only through the idle shim's C# hook (`reference/semantic_search.md` §Indexed); a session on the stock plugin or an older shim gets no C# chunks.
- **C# navigation:** anchor the declaration, then use LSP; do not mix declarations and mentions with a bare identifier grep. Literal values, regex alternatives, comments, attributes and known unique anchors remain valid Grep uses. If LSP is unavailable, use the supported search fallback and name its coverage limit.

## Godot Conventions
PascalCase for C# files/types/directories; snake_case for Godot assets. Invoke the engine as `bash .claude/scripts/godot_bin.sh <args>`, never `"$GODOT_BIN"`.

## Godot Rationalizations to Refuse

| Rationalization | Rule to cite |
|---|---|
| "grep is faster / LSP is slow / it's just one symbol" | §Godot Docs and C# Navigation, C# navigation |
| "it's just a rename, the build will catch it" | §Godot Build & Test: a rename can compile clean and still break serialized `.tres`/`.tscn` references, so `.cs` work is gated, once at drive close; `change_control` owns cadence |
