# Staircase construction — the mechanics behind SKILL.md §1–§2

Detail layer for `benchmark_design` §1 (coupled corpus constraints) and §2 (ladder ordering, reading a ceiling failure). The verdicts stay in SKILL.md; this file carries the procedure and the evidence table.

### Corpus constraints are coupled — solve them in one search, and let the LADDER give

Each rung needing its own answer target *and* its own answer value is **one joint problem**: solved in
sequence, the last rung is stranded with a target whose only usable value is spoken for. Greedy
allocation fails under every ordering, scarcest-first included; use an exhaustive joint assignment.
Where that proves infeasible, **change a rung's parameters, not the search's strictness** — the
parameters are arbitrary, the distinctness is not.

**One predicate, one implementation.** A separate feasibility probe choosing the ladder will disagree
with the builder; two implementations of one predicate is the defect, not the discrepancy. Survey with
the code that has to build it.


### A monotone SCALAR does not make a monotone LADDER

Difficulty is a scalar only when every rung asks for MORE OF THE SAME STEP. A rung introducing a new
KIND of step can be harder than the rung above it for an arm lacking that one skill, so the builder's
rising-depth assertion passes while the ladder is unordered and no bound is reported.

- **Say which kind of step each rung adds**, and check the ladder is nested in KIND as well as count:
  every rung should require everything the rung below it required.
- **A rung that introduces a new skill is a new AXIS wearing a rung's clothes.** Give that skill its own
  axis, or ensure it appears first at the bottom and recurs upward.
- **Read a non-monotonic result as a dissociation before reading it as noise** — and check the cost
  signal. An arm failing a middle rung in a THIRD of the turns it spent on the rung below never
  attempted the step, which is a different and more useful finding.

### A ceiling failure has TWO causes, and only one of them kills the instrument

Same observation, opposite disposition. Do not decide it from the pass rate — **read the trace**.

| | the arm SHORTCUT it | the arm EARNED it |
|---|---|---|
| what happened | the answer was reachable without doing the work | the arm did every step the rung requires |
| what it says | the instrument is broken | the instrument is sound; your field is above its range |
| evidence | a rule decidable at a glance; a path shorter than the declared minimum | the trace walks the full declared chain, including its most expensive hops |
| disposition | rebuild or discard | collect it for the tier it does separate, and report a FLOOR for the tier it does not |

**A floor is a legitimate output.** An axis that bounds the bottom of a field and floors the top has
measured the bottom; say so. What is not honest is **extending a ladder until somebody finally fails**
— more rungs of the same kind buy accumulated slip rate, so the axis measures endurance while claiming
competence. To bound the top of the field, find a different failure mode, not a longer chain.
