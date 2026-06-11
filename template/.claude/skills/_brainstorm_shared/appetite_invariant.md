# The Appetite Invariant — rigor-hole vs taste-fork

> Single source of truth for the rigor-hole-vs-taste-fork classification rule shared by [`/architecture_brainstorm_redteam`](../../commands/architecture_brainstorm_redteam.md) (its critic panel) and [`/plan_drive`](../../commands/plan_drive.md) (its convergence loop). Both reference this file via *"[Shared] See `_brainstorm_shared/appetite_invariant.md`."* and then add their own one-line application.
>
> This file is NOT a skill — no frontmatter, not directly invocable. It exists so the two surfaces cannot drift: a future edit to the tripwire list or the always-taste set lands once, here.

---

## The rule

A finding/question is a **TASTE-FORK** — human-only; batch it, **NEVER** auto-answer or auto-resolve — whenever its answer depends on a tunable **appetite**: how much coupling, future-proofing, scope, performance margin, risk, or feel is *acceptable* — **even if it is phrased analytically.**

An **analytical** (model-answerable) finding has a single correct answer *given a stated appetite*. If the appetite itself is unstated, the finding is taste.

- **Lexical tripwire:** the words *"acceptable / good enough / worth it / matters / tolerable / fine"* force taste classification. You may **quantify** a cost (analytical); you may **never** rule whether the cost is **worth paying** (taste).
- **Always taste:** abstraction-level sizing; boundary placement between viable options; *"is coupling X acceptable"*; over- vs under-engineering; anything that sets a **convention / value-set / default / naming precedent** a downstream Part will inherit.
- **Errs toward taste:** when unsure, classify as taste-fork and batch it. Misclassifying taste as analytical (and answering it) **fabricates the user's vision — the worst outcome.** Misclassifying the other way costs only one extra batched question.

## Why this is load-bearing

The whole point of the brainstorm → plan pipeline is that the *human* owns appetite and the *agent* owns rigor. An agent that silently resolves a taste-fork to reach convergence (or to "harden" a design) has invented a design decision the user never made — and downstream Parts inherit it as if it were chosen. Surfacing a taste-fork is never a failure state; a converged plan or a hardened design that still carries **N open, batched taste-forks is a SUCCESS state**, not an incomplete one.

> A stated evolution direction is NOT a taste-fork to re-litigate — it is a settled appetite (CLAUDE.md Core Principle 8, *Modular when direction is known*). Only flag speculation the user did **not** state.
