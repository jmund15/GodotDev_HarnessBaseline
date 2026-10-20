# Brief checks before launch

Read before launching a brief you wrote (`orchestration` Pre-Dispatch Checklist points here).

- The brief marks each premise observed, with its evidence (a path, or a command and its output), or suspected. A suspected cause goes to the delegate as a question to settle, never as the brief's premise.
- A brief for a task states its record's requirements and exclusions (`task_record.py show <id>`) as the acceptance test, above the work's inputs.
- A brief for an investigation or fix lane embeds the project debugging skill's Recurrence Law rules 1–3 as text, because its delegate may run on a client that loads no skills; a lane returning a fix without its discriminating evidence is rejected at review.
- The brief sets no limit its delegate stops at: no minutes, turn count or "stop after". A duration worry is the orchestrator's check-in, read from the job's progress (`reference/sidecar_runs.md` §Detached runs); a run that hits a cap discards every turn it paid for.
- The brief's paths resolve: `python3 .claude/tools/verify_doc_citations.py --kinds path --only missing <brief>` lists the misses, and each is fixed or confirmed as a non-path (a branch name) or a path the job creates.
