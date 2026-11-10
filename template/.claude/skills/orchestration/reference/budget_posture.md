# Budget posture

Read when the posture line reports `5h used faster than its time` or a Surplus band, and §Paying plans before a job's `plan` override, a pay-per-token route or a credit-plan row (`orchestration` §5b points here).

## Paying plans

The fan-out picks each job's paying plan by the ladder's selection line step 2 (`reference/model_ladder_evidence.md`) across its listed rows' plans, counting its live jobs and keeping plans whose 5h window lasts until the job ends, then runs the cheapest listed row on it (`orchestration` §0 routes it); a job's `plan` field overrides it only for an owner's named plan or a measurement. Pay per token (`costModel: marginal-usd`) only when no fit plan can serve the job: each is exhausted, unreadable, refused by the launcher's `--check`, or left with less than the job needs. A plan spending faster than its window is not exhausted: pick cheaper rows on it, never dollars. List price is a weight, never the spend; a free route still has eligibility, privacy and service limits. On a credit plan (a registry `credit` block) list price is the spend: weigh the job's expected list cost against the dollars its posture clause leaves, since the grant lapses at cycle end. An unreadable window is unknown, never assumed available: a plan whose reader states why it cannot read carries that reason instead of `warming`, while one whose reading merely expired shows its last band with its age and is re-read.

A plan whose only shortfall is a short window's reset is waited for, not bypassed. When the fitting plan is spending its long window faster than time (`room` below 1.0, the even pace `provider_bands` defines) and another plan's long window has more room, hold the job for that reset: the wait is the short window's remaining time, minutes, against the days of long-window quota the fit would spend. A plan blocked by its longest window is not a wait, and neither is an unreadable one.

## Posture bands

When the posture line says `5h used faster than its time` (the narrow threshold in `quota_bands.FIVE_HOUR_NARROW_PRESSURE`), the session runs one native job at a time; the fan-out keeps one native job and places the rest on another plan, and the guard denies a second and names the reset and the cross-transport rows that serve its tier. An owner statement lifts it for the session when the transcript verifies it: an owner turn holding the literal `--<transport>` token outside a pasted block (`workflow_provider_guard.owner_lift_found`); no answered question or other free text lifts it. The words in `args.currencyReason: "owner: <words>"` are display only.

A plan in the Surplus band has more quota left than window left. Usage for the rest of the window is unknown, so that never by itself justifies new work. At the close of the current task, offer the owner backlog jobs whose result is a report or a review, never a change to the tree: `/worklog triage`, the project's audit commands, a plan check on an unchecked plan, or the candidate review a commit owes. Route their dispatches by the ladder's selection line, whose plan step takes a plan with quota going unused at reset first. Never interrupt work in flight to spend it.

The `free pool: N/M ready` clause counts the free pool's members not cooled after a failed call; `0/M` means no free member can serve a pin, and the count never by itself justifies new work.
