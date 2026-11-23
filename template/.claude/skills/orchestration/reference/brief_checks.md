# Brief shapes

Every brief takes one of two shapes, chosen by the work. A job whose result is a judgment (exploring, designing, planning, debugging, reviewing) gets an **open** brief. A job that carries out a settled spec gets an **execute** brief. The launcher or engine adds the delegate rails (`guards/rails.md`) to every brief, so a brief never copies them.

## Open brief

It holds:

- **The owner's words**, verbatim, each with its source: the owner-decisions block of the state file (`.claude/work/<slug>.md`) or the session transcript `.jsonl`.
- **Raw source paths** under `inputs:`: the files, logs and measurements as they stand, never a summary, an excerpt or a list you filtered first.
- **Owner-tagged constraints**: their picks, locked parts and hard limits, each with the owner's words.
- **The deliverable path** under `targets:`.

It holds no conclusion of yours: no cause, fix, design or ranking. A brief that hands over the briefer's conclusion gets it confirmed back, which is the dispatch-leading defect both repos recorded. The owner's words show what is wrong and what they want; the job finds the cause and the best answer, which can differ from what the words name. Point at a principle's home instead of restating it, because a restatement drops the example's terms. Several seats on one question each get the whole question: a lens names where a seat starts, never its boundary.

## Execute brief

It holds:

- **The settled spec**: the owner's pick, the plan slice or the golden file, stating every behaviour the job must build.
- **The target files** under `targets:`, the deliverable included. A read-only job's only target is its deliverable.
- **The unit's own fast test**, as the done-condition. A run sized by the product (the gate, a whole suite, a render of every state) is never in an execute brief. The parent starts it in the background once at merge or close and acts on its one wake, because a delegate waiting on it spends turns on nothing.

## Both shapes

- `inputs:` and `targets:` are each a header line followed by `- <path>` lines. Every path the job must read goes under `inputs:`. The launch check refuses a brief whose input, deliverable directory or brief path does not resolve against the launching checkout, and a read-only job whose `targets:` names more than its deliverable.
- Mark each premise observed, with its evidence (a path, or a command and its output), or suspected. A suspected cause goes to the job as a question to settle, never as a premise.
- When the work belongs to an initiative, quote its state file's owner decisions above the inputs as the acceptance test.
- A brief whose result feeds an owner question asks for each fact as a `{file, quote}` or `{command, output}` item, so `tools/quote_check.py` confirms it before the ask.
- A debugging brief for a recurrence carries the Recurrence Law's three points (`skills/debugging/SKILL.md`).
- Set no limit the job stops at (minutes, turns, "stop after"), because a run cut at a cap discards every turn it paid for. A duration worry is the parent's check-in on the job's progress. Order no wait on a long job: the rails' wait checks govern it.
