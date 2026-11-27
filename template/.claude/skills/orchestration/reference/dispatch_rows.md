# A job's rows and window

Read before leaving a cheaper row out of a job's rows, setting its cache lifetime, or pinning a larger window (`orchestration` Pre-Dispatch Checklist points here).

## Which rows a job lists

A job's rows leave out a cheaper row that clears the ladder's floor only for a named reason: the `±` clause by which it fails the job's bar (outside lean, the result the job needs; under lean, doing the job reliably). A `±` risk bars the cheaper row only when that failure would cost this job more than the price gap: a final report the commits and diff can rebuild costs one read. A shape that cannot say why it beats the cheaper one takes the cheaper one. Rows come from `for-role`'s cells for this job's work, never from a model or effort a plan, an earlier jobs file, a roster or a proposal named, and never from a fixed model per lens, round or rigor: a copied pin carries another job's reasons, not this one's. A check-scored job records its `spec` and omits its effort (§5 *Effort*); judged work takes the effort its scope, complexity and depth put it at in its cell's range, and its effort fit is rated after the result.

A job on an Anthropic row that will wait more than about an hour in total on long runs (tests, builds, other jobs) sets `cacheTtl: "1h"`; every other job leaves it unset. The 1-hour write costs 0.75× the job's final context more than the 5-minute one. While it waits, each wait call keeps the cache warm with a 0.1× read: one per 270 s on 5m and one per 10 minutes on 1h (`guards/common.md`'s wait clamps), which is 0.022× against 0.010× of the context per minute, so the hour repays after 0.75 ÷ 0.012 ≈ 62 minutes of waiting.

## A larger window

Pin a larger window (`-M`) only when the work needs everything resident at once, and state its price: a larger window can bill at a long-context rate. `-M` raises the window only up to the login's compaction cap; `reference/model_ladder_evidence.md` *Compaction* owns the cap and the remedies for a subject past its point.
