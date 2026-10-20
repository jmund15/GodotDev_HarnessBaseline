# Context checkpointing (long-horizon drives)

Read when a drive keeps task state across phases, interruptions or compactions (`orchestration` §10 points here).

Keep a small task state: requirements/exclusions, decisions, active jobs, accepted artifacts, verification and next action, and its review-ledger rows (`review_ledger.py rows --task`), in `tools/task_record.py`: its triage fields (`task_record.py triage`), one job row per dispatch (`task_record.py job`) and one slice row per plan slice (`task_record.py slice <task> --id <S> --state pending|in_progress|completed`). Write each row at a phase boundary or consumed result, and update a slice row on entry and on green. A `TaskCreate` board only mirrors it. The compaction hook re-injects the record. Its `--files` names the docs the task reads (design doc, plan, worklog item), so a resumed session need not search for them. A result later work needs that exists only in tool output (a classification table, a tally) goes to a scratch file when it is produced, and the summary's Artifacts section names it. After interruption, load the task record and the active procedure, not completed phase bodies. After resume, recheck required inputs and decisions before trusting receipts.

Session cost is context size times turn count, and every tool call and wake-up is a turn. Take a large window only for the turn that needs it, then compact in session at a useful boundary; dispatch the next long job first, since in-flight dispatches survive compaction. Read `context_pct` from the cachestat file, never self-estimate.

## Summary fidelity

`hooks/compact_directive_ledger.py` sends these bullets to the compaction summarizer as written, and a hand-written handoff follows them. The summarizer cannot read a file, so each bullet is the whole instruction.
- Keep a line only if the successor would otherwise search the transcript for it. What a file, git or the re-injected owner words hold is cited by path or ID, never restated in any section (code, constants, test names, prompt or report text).
- Keep character for character every string the successor must re-issue or match: a command with its flags, a label (a tool's own classification label too), an id, a path, an exact value. Copy it from where it last appeared; never a placeholder, a paraphrase or "…" inside a quote.
- Every pending step whose command already ran once this session keeps that exact command, in order, in Pending Tasks. A recipe the session built (a launch, a worktree setup, a post-check) is its commands, not a description.
- State a guard's or gate's demand only as its denial text read it; a rule you cannot quote is left out, never hedged ("may require"). A file read it requires (the model-ladder read) is never recorded as done: compaction resets it.
- List each owner ID once; the ask's settled-ID line may carry several. When a lane closes, mark every directive scoped to that lane done.
- Drop resolved history: landed-commit sha lists, fixed errors, finished steps.
