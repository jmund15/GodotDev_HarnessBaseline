# Guard overlay: author — godot layer

`tools/guard_text.py` appends each section below to `author.md`'s section of the same tier; `author.md`'s header rules bind this file too.

## detailed

- Logic Domain (the Logic subsystems CLAUDE.md §Hybrid TDD names) is strict TDD: NO production code without a failing test. RED (`[TestSuite]` in the Logic suites it names) → VERIFY the specific failure → GREEN minimum → REFACTOR. Includes `.tres` changes that affect Logic behavior. [CLAUDE.md §Hybrid TDD]
- "The logic is obvious, implement first and test after" is a named rationalization to refuse, not a carve-out. [CLAUDE.md §Rationalizations to Refuse]
- Gameplay Domain (the Gameplay subsystems CLAUDE.md §Hybrid TDD names): automate the deterministic via ISceneRunner; leave subjective feel to manual playtest. [CLAUDE.md §Hybrid TDD]
- `.cs` changes: the regression gate is `/regression_gate`, no carve-outs. [CLAUDE.godot.md §Godot Build & Test]
- If you do run tests: never omit `--filter`/`--settings .runsettings`, never pass `--no-build`, always give the Bash call `timeout=600000`. [CLAUDE.godot.md §Godot Build & Test]
- A `///` is authoritative about its own member only — write obligations (what a caller must honour), never observations about other code; TODO/future notes go in `//`, never `///`. [rules/csharp_patterns.md §Core Conventions]

## condensed

- Logic-domain strict TDD per CLAUDE.md §Hybrid TDD; `.cs` changes take `/regression_gate` before commit (CLAUDE.godot.md §Godot Build & Test).
- A `///` is authoritative about its own member only — write obligations (what a caller must honour), never observations about other code; TODO/future notes go in `//`, never `///` (`rules/csharp_patterns.md` §Core Conventions).

## minimal

Read this file's `## condensed` section.
