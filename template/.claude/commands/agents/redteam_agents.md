---
disable-model-invocation: true
---

# Red-Team Lens Templates

<!-- Single source of truth for red-team lens mandates. Referenced by: /architecture_brainstorm_redteam (Step 0.5, Step 1). -->

## Shared Classification Rule

Every lens judges pushed input only (read-only; no tests, no LSP) and classifies each finding:

- **Design-hole** → `action: FIX` (a concrete design correction) or `PLAN` (needs a redesign pass). `category: rule | bug | improvement`.
- **Taste-fork** → `action: ASK` with `options[]` (viable alternatives, each ending `wins when <condition>`; `options[0]` carries no recommendation). The critic presents, NEVER picks.

---

## Lens Templates

Each header's `interleave:` line names the `architecture_brainstorm` steps the lens can fire at in Mode B; the conditional lenses need Step 0.5 admission instead.

### rt-boundary (framework boundary / layering) — floor lens

mandates: rt-boundary
interleave: Step 2 (premise leak), 4, 5, 7

```
Does the design make Jmodot reference `{{PROJECT_NAME}}.*`, or leak game-specific logic into the framework? Cite the violated seam.
```

### rt-abstraction (missed existing abstraction) — floor lens

mandates: rt-abstraction
interleave: Step 4, 5, 7 (needs a proposed type)

```
Does it invent a parallel type where a verified existing owner already covers the concern? Name that owner from the pushed inventory (`feedback_inspect_existing_abstractions_first`).
```

### rt-failuremode (known-gotcha walk) — floor lens

mandates: rt-failuremode
interleave: Step 2 (tentative pick), 4, 5, 7

```
Does it walk into a memorialized failure mode — init-timing, disposal/lifecycle, pooling reset scope, magnitude-as-type-discriminator, autoload order, OnExit/OnEnter clobber? Cite the gotcha.
```

### rt-yagni-scope (YAGNI vs stated direction) — floor lens

mandates: rt-yagni-scope
interleave: Step 2 (speculative premise), 4, 5, 7

```
Is it over-engineered for an *imagined* need? **A stated evolution direction is NOT a YAGNI hole** (`CLAUDE.coding.md` *Modular when direction is known*) — only flag speculation the user did not state. Also flag scope where a touched subsystem serves another behavior or a seam lacks an owner.
```

### rt-testability (test-first feasibility) — floor lens

mandates: rt-testability
interleave: Step 4 (only when an approach already names its test surface), 5, 7 (needs commitments)

```
Can each commitment be driven test-first (Logic → concrete `[TestCase]` names; Gameplay → ISceneRunner or explicit subjective flag; a person's task on a rendered screen → a row of the project's agent playtest command, and subjective covers taste only)? Feeds the plan stage.
```

### rt-systemic (neighboring-design conflict) — triggered

mandates: rt-systemic

```
Does this design conflict with, duplicate, or silently reshape a NEIGHBORING committed design? Judge only the pushed adjacent-design excerpts; cite the conflicting section pair.
```

### rt-vision (product fit) — triggered

mandates: rt-vision

```
Does this serve the product per the pushed vision digest? Fit findings are usually TASTE-FORKS (`action: ASK`) — flag misalignment, never decide fit.
```
