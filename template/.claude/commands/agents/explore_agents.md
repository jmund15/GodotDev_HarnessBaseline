---
disable-model-invocation: true
---

# Exploration lens rules

Every `/explore` lens reads this file with its own mandate (`commands/agents/explore/<lens>.md`). `/explore` owns the roster and when each lens runs; `workflows/explore_fanout.js` owns the claims object's shape and the evidence kinds, and rejects a reply that does not fit them.

- **State, never advice.** A lens reports what is. Recommendations, designs and priorities are the parent's to derive from the claims; a mandate that starts proposing has left its scope.
- **The shared context orients and never caps.** It is a seed, and every fact in it is a claim to confirm first-party: a lens that trusts the brief inherits the parent's blind spots, which exploration exists to catch.
- **Absence needs git-aware proof.** A `polarity: "absent"` claim cites a git-aware enumeration, supplied in the context or run when the lens has Bash, as `{"command": "git grep -n <pattern> <branch> -- <path>", "output": ""}`. An empty Grep or Glob is not proof. With neither route, return `polarity: "unclear"` and name the missing enumeration in `gaps`.
- **Memory files** cited by bare name (`feedback_*`, `gotcha_*`, `arch_rule_*`) resolve under `.claude/auto-memory/` or `.claude/auto-memory/archive/`.
- **Order reads by recent churn** in the topic's folders, and state the history window and why it reaches the current change. Churn orders reads; it never proves absence.
- **Report only what changes a planner's next move:** a premise the code refutes, a family that already owns the concern, a rule the plan must respect, a consumer a change would break. Orienting facts take `bearing: "context"` and stay few. A precondition that turns out false returns zero claims with `stoppedAt: "trigger-not-met"`, a legitimate empty.
- Use the same `subject` string another lens would use for the same thing: the engine groups on it to find corroboration and contradiction.
- Never use TodoWrite. Never run builds, tests or the language server: they can be single-flight per machine, and several lenses run at once.
