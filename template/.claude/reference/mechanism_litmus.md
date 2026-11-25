# Mechanism litmus

A mechanism is any test, check, hook, tool, rule or stage that runs without a reader deciding each time. This file
applies to adding one and to keeping one, in any project: Godot tests, web tests and harness proofs included. Add or
keep a mechanism only when all four hold.

- **L1. Real.** The failure it targets has happened (a red, an incident, a deny that saved work), or it is a floor: an
  irreversible loss of peer work, owner authority, data, or a destructive operation's target. A spec test's RED-first
  run is a red that happened.
- **L2. Not removable.** Correcting or deleting the cause (`CLAUDE.core.md` §Core Principles, *Fit the work to its scope*)
  does not end the failure.
- **L3. Would escape.** Nothing cheaper already catches it. A silent failure escapes (a stale cached pass, a guard
  that fails open, a skipped step); a loud one (a crash, a red, a visible error) is caught by use. Cost counts the
  mechanism's code and test lines, which `tools/mechanism_census.py` reports by area, beside its run time and the
  context it loads. A change that adds or lengthens tests measures that run time at the change
  (`tools/proof_cost.py`), with load held level across what it compares: the base and the changed test file run
  alternately on one machine at one time, or the added tests' own times from one run where the runner reports
  them, because machine load moves a single run more than a few cases add. Its report gives the difference with
  its spread, and whoever judges the change weighs it
  against what the cases catch.
- **L4. Decidable.** It decides from facts. A deny meets `instruction_quality` §3's bar (wrong in every case its
  predicate matches); a check that does not block needs near-zero false alarms. A check that needs judgment is a line
  of text at the decision point, never a guard. A proof a valid edit can turn red fails L4 until it is cut to its
  decision (`rules/harness_tooling.md` §A proof asserts the decision); it is deleted only when that decision fails L1 or L2.

**Placement.** Block before only a floor loss (L1) or a change live in every session before any land. Where the
project has a main check, run every other check after the land, off every session's wait, with the same proofs and
gate. What makes a floor is the loss it guards, not a judgment that other work is easy to revert: no check is
skipped or thinned for being reversible.

**Exit.** A commit that adds a mechanism names its L1 failure and the observation that retires it, in its message.

## Two clauses

> **A fix re-asks the litmus.** Before fixing a mechanism, read its recent fixes:
> `git log --oneline --since=30.days --grep='^fix' -- <its files>`. When an earlier fix changed the same decision, or
> this fix would add one more case (a phrasing, a name, a path pattern, an event kind) to a list the mechanism decides
> by, fix the decision: make it decide on the property the cases share, observed as a fact. When no observed fact
> carries that property (the input is prose, free shell text, an owner's wording or model output), the decision fails
> L4. It becomes text at the decision point, or reported facts, unless the mechanism guards the floor.
>
> A fix to a file the census lists as most-fixed is planned as a redesign of that file. The plan states whether the
> redesign or the patch is the smaller total, and the plan check's smallest-system question rules on it, so a patch
> passes when the redesign is the larger change. Two cases land first and their redesign plan follows: a fix that
> restores a broken floor (a red main, a guard denying every call), and a fix to a guard that wrongly denies justified
> work. A fix to a file whose redesign a roadmap Part plan already owns joins that plan. The commit that lands a
> redesign carries the trailers `Redesigns: <path>` and `Plan: .claude/plans/<file>.md`; the census restarts the
> path's fix count at that commit only when the plan file exists there. The Read of a listed file names its facts
> once per window (`hooks/post_read_dispatch.py`).

> **Holds where it runs.** A mechanism, new or fixed, names the values its author's runs held fixed that vary in use,
> and holds across each. Examples: another session or worktree acting at once, the OS and shell, machine load and
> timing, a cold cache or the state an earlier run left, and the size of its input, the harness's own size included,
> with what its cost grows with. The landing observation (*Planted cases*) runs a varied value when one can be run.

The examples are open: a closed list would grow one incident at a time, as the per-variable sections did. A floor
guard that parses free shell text (`kill_guard.py`, `dangerous_shell_guard.py`) keeps its phrasing list as the price of
the floor; the census rules on each such guard's fix chain.

A reviewer asks the same question of every decision; `commands/agents/orchestrator_action_protocol.md` §Reporting Filter
owns that duty.
