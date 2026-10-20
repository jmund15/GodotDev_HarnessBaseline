# Guard overlay: author — godot layer

`tools/guard_text.py` appends each section below to `author.md`'s section of the same tier; `author.md`'s header rules bind this file too.

## detailed

- Logic Domain (the Logic subsystems CLAUDE.md §Hybrid TDD names) is strict TDD: NO production code without a failing test. RED (`[TestSuite]` in the Logic suites it names) → VERIFY the specific failure → GREEN minimum → REFACTOR. Includes `.tres` changes that affect Logic behavior. [CLAUDE.md §Hybrid TDD]
- "The logic is obvious, implement first and test after" is a named rationalization to refuse, not a carve-out. [CLAUDE.md §Development Philosophy: Hybrid TDD]
- Gameplay Domain (the Gameplay subsystems CLAUDE.md §Hybrid TDD names): automate the deterministic via ISceneRunner; leave subjective feel to manual playtest. [CLAUDE.md §Hybrid TDD]
- `.cs` changes owe `/regression_gate`, no carve-outs; your dispatcher runs it at drive close (`author.md`). [CLAUDE.godot.md §Godot Build & Test]
- After a `.cs` change, run the repo-scan lints before you return: `-Filter "FullyQualifiedName~Tests.Logic.Lint"` (every class under `Tests/Logic/Lint/`). They scan the whole repo, so your slice tests cannot show what your edit trips there. [change_control §Gate cadence]
- `hooks/wide_test_guard.py` refuses a wide test run: a filter that is not a test class or `Class.Method`, `verify.ps1 -Scope`, the regression gate, or unfiltered `dotnet test`. It admits the run when your brief names its filter string, and always admits the `Tests.Logic.Lint` lints. Weakness it compensates: delegates ran domain-wide suites despite the line above. It retires when delegate transcripts show no wide run and no denial from it. [`author.md`]
- Godot/C# tests: load `testing` first. Never omit `--filter`/`--settings .runsettings` and never pass `--no-build`. [testing §Quick Start]
- A `///` is authoritative about its own member only: write obligations (what a caller must honour), never observations about other code; TODO/future notes go in `//`, never `///`. [rules/csharp_patterns.md §Core Conventions]

## condensed

- Logic-domain strict TDD per CLAUDE.md §Hybrid TDD; `.cs` changes owe `/regression_gate`, which your dispatcher runs at drive close (CLAUDE.godot.md §Godot Build & Test).
- After a `.cs` change, run `-Filter "FullyQualifiedName~Tests.Logic.Lint"` before you return.
- `hooks/wide_test_guard.py` refuses a wide test run (namespace, tier, `verify.ps1 -Scope`, the gate, unfiltered `dotnet test`) unless your brief names its filter (`Tests.Logic.Lint` always passes).
- A `///` is authoritative about its own member only: `rules/csharp_patterns.md` §Core Conventions.

## minimal

Read this file's `## condensed` section.
