# Guard: author — delegates that write production code

Read ONLY the section matching your model tier: `strict` = sonnet / haiku / deepseek · `terse` = opus · fable receives none. Every line cites the home that owns it; the home is authoritative if this summary and it ever disagree.

## strict

- `.claude/guards/any.md` §strict applies to you as well — read it too.
- Logic Domain (SpellArchitecture, Synergies, Jmodot.Core, Inventory, Math/Parsing, Data Structures) is strict TDD: NO production code without a failing test. RED (`[TestSuite]` in `Tests/Logic/`) → VERIFY the specific failure → GREEN minimum → REFACTOR. Includes `.tres` changes that affect Logic behavior. [CLAUDE.md §Hybrid TDD]
- "The logic is obvious, implement first and test after" is a named rationalization to refuse, not a carve-out. [CLAUDE.md §Rationalizations to Refuse]
- Gameplay Domain (Wizard, enemy BT, spell lifecycle, VFX, UI, physics feel): automate the deterministic via ISceneRunner; leave subjective feel to manual playtest. [CLAUDE.md §Hybrid TDD]
- `.cs` changes require `/regression_gate` before commit, no carve-outs. Under the concurrency guard you must NOT run it — finish the work and report that the gate is owed. [CLAUDE.md §Build & Test]
- If you do run tests: never omit `--filter`/`--settings .runsettings`, never pass `--no-build`, always give the Bash call `timeout=600000`. [CLAUDE.md §Build & Test]
- Before ANY new named configuration surface (type, `[Export]`, parameter, behavior-selecting bool/enum, helper): name the family that already owns the concern, or record "none exists". A behavior-selecting bool sitting beside a `*Strategy` sibling is a strategy slot in disguise; a literal `null` into a strategy slot is a neutered seam. [rules/design_litmus.md #1]
- Orthogonal axis → a composable Resource/config slot on the base, not a subclass rung. One knob, one axis. One home per authored value — second surfaces derive, never re-author. Every visible export is read in every context an author can reach it. [rules/design_litmus.md #2–#5]
- Comments: default NONE. Add one only where deleting it would lead a maintainer to a wrong decision. [feedback_comment_discipline.md]
- Do NOT reduce planned scope mid-execution. If the scope looks wrong, stop and report it — the cut is not yours to make. [feedback_dont_unilaterally_reduce_planned_scope.md]

## terse

- `any.md` §terse applies; Logic-domain strict TDD and the `/regression_gate`-before-commit rule per CLAUDE.md §Hybrid TDD + §Build & Test (do not run the gate under the concurrency guard — report it owed).
- Seven-question design litmus before any new configuration surface: `rules/design_litmus.md`.
- Comments default to none, and a `///` is authoritative about its own member only — write obligations (what a caller must honour), never observations about other code; TODO/future notes go in `//`, never `///` (`rules/csharp_patterns.md` §Core Conventions). Planned scope is not yours to cut (`feedback_dont_unilaterally_reduce_planned_scope.md`).
