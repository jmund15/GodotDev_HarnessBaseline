# Per-slice pins

Read before pinning a slice's row and effort, to decide whether its test intent is clearly defined or ambiguous (`orchestration` §5 points here).

Record that reading on the call itself — `pick` (`cell|room|owner|default`) and `spec` (`tight|judged`) on a job, or `-Y <pick>,<spec>` on a lone sidecar launch — because the reason is gone once the session compacts; both are optional, and a value the enums do not name is refused at launch while an omitted one is recorded as unrecorded rather than guessed.
- **Clearly defined:** the slice's test intent names a check that fails before the work and passes only when the slice's unknown is resolved correctly: a failing test, a count against a hand-labeled sample (labels read by a person, never produced by the tool being tuned), a diff against a golden file. An unknown that check scores over a bounded set of moves, such as adjusting a lookup table until a labeled-sample test passes, is iteration inside a clearly defined slice. It takes the converged or tight-spec cell.
- The executor can fit a sample it sees: the parent scores a held-out sample it never sees before accepting a tuned table.
- **Ambiguous:** the test intent is `subjective`, or its check is one the seed or a first guess already passes, so the executor must judge its response unscored. Examples: an unknown root cause, runtime behaviour no one could confirm beforehand, tuning with no labeled sample, a search whose target cannot be named, an approach picked against acceptance criteria. It takes the ladder's ambiguous-execution cell. A fact a read of the current tree settles is not one: the planner settles it (`orchestration` *Settle each question*).
- Owner decisions and undecided seams are never an executor's ambiguity: they halt (halt rules H1, H3).
- A plan's or brief's class label is an input: re-apply this test to each slice's test intent.
- **A job takes the pin of its least-scored slice:** an unscored slice that can gain a check (a hand-labeled sample) gets that step first, and one that cannot becomes its own job.
