# Brief checks before launch

Read before building a brief (`orchestration` §0 Governing skills and Pre-Dispatch Checklist point here).

## Governing skills

Before building a brief, load each skill whose workflow the job uses in this session's current context; repeat after compaction unless the client reattached the whole skill and that copy still matches the skill's `SKILL.md` on disk. Keep its source paths in the brief. The dispatch doctrine guard observes skill-directory references in prompts and listed context, plus external artifact ownership in the optional project owner map read by `hooks/skill_dispatch_gate.py` (`owners`: skill name → artifact path globs). An absent project file adds no external ownership. It checks the dispatching session's recorded loads, never a job's load claim. Weakness: sessions have skipped skill-load pointers and resumed from partial summaries. Remove this check when live dispatch evidence shows those skips no longer recur.

## Brief checks

- The brief marks each premise observed, with its evidence (a path, or a command and its output), or suspected. A suspected cause goes to the delegate as a question to settle, never as the brief's premise.
- A brief for a task states its record's requirements and exclusions (`task_record.py show <id>`) as the acceptance test, above the work's inputs.
- A brief for an investigation or fix lane embeds the project debugging skill's Recurrence Law rules 1–3 as text, because its delegate may run on a client that loads no skills; a lane returning a fix without its discriminating evidence is rejected at review.
- An executor's done-condition runs its dispatcher's checks of the returned work (its edited units' proof files, `dispatch_result.py check`, its live checks), so a defect is fixed inside the dispatch on the cheaper model. No brief hands it a wider run than the slice row of `change_control` §Gate cadence (bound proofs, `verify -Scope`, the gate, a whole spec file or a second test project): the dispatcher runs each once at its merge or close (`rules/harness_tooling.md`, `change_control` §Gate cadence).
- A brief whose result feeds an owner question asks for each fact and each pick's basis in a `claims` fence (`reference/evidence_contract.md`), so the run verifies them before the ask, per `CLAUDE.core.md` §Preferences *Asking the owner*. Decision rows with free-text facts return unchecked.
- The brief sets no limit its delegate stops at: no minutes, turn count or "stop after". A duration worry is the orchestrator's check-in, read from the job's progress (`reference/sidecar_runs.md` §Detached runs); a run that hits a cap discards every turn it paid for.
- The brief's paths resolve: `python3 .claude/tools/verify_doc_citations.py --kinds path --only missing <brief>` lists the misses, and each is fixed or confirmed as a non-path (a branch name) or a path the job creates.
