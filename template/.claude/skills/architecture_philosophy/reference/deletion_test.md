# The Deletion Test — signs, heuristic and use

Read from SKILL.md §The Deletion Test when judging whether a new class, Component, State, Resource subclass or `*Helper` earns its place. Moved verbatim from `SKILL.md`; the rule itself stays there.

- **Deep module** — deleting it forces every caller to re-implement substantial logic, *and* each re-implementation needs its own design decisions (which the module centralises). Keep it.
- **Shallow module** — deleting it changes nothing meaningful; each caller absorbs a one-liner. A redirection layer. Inline it.

**Heuristic:** if your interface signature lists every parameter the implementation needs, the module is shallow. Deep modules narrow the interface and absorb decisions internally.

{{PROJECT_NAME}}-specific signal: a `*Helper` / `*Utils` / `*Service` / `*Manager` class with static methods that merely forward most of the caller's inputs without owning a decision is almost always shallow. Either:

1. **Inline at the call site** (delete the indirection), OR
2. **Deepen the module** — move more decisions inside, narrow the parameter list, take an `IBlackboard` / context object instead of 6 individual parameters.

**Where this complements existing rules:** `structure_rules.md` R9 ("no single-file folders") and R10 ("no mixed concerns in a flat folder") detect shallow patterns at the *folder* level; the Deletion Test extends the diagnostic to *interface signatures* — a folder of one-liner classes can pass R9 while still being shallow. *Resource Strategy Hierarchies* and *Composable Configuration Resources* are the *positive* form; this is the *negative* form.

**When applied during diagnosis:** if the `debugging` skill's Phase 5 surfaces a "no correct seam exists" finding, the seam-less area is usually a chain of shallow modules each pushing responsibility downstream until the seam dissolved. Use this vocabulary in the Worklog `arch | <description>` entry to scope the future architectural work.
