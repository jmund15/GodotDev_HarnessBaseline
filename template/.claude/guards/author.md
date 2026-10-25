# Guard: author — delegates that write production code

Read ONLY the section your dispatch names; if none is named, read `## detailed`. `any.md`'s section of the same tier binds you too and arrives with this file. Every line cites the home that owns it; the home is authoritative if this summary and it ever disagree.

## detailed

- Run only the targeted tests of the items you change, plus the repo-scan lints your layer's rails name. A scoped suite run and the project's regression gate are your dispatcher's, once at close: report them owed. A wide run your brief itself names (a crash that reproduces only in a batch) is the one exception, for that run alone. Any other wide run, in your command or in a script you run, is refused. [change_control §Gate cadence]
- In the shared main checkout you never commit or branch: return every changed path as `changedPaths`, and your dispatcher commits them. In a worktree on its own branch, commit there. [orchestration reference writer_returns.md]
- Comments: default NONE. Add one only where deleting it would lead a maintainer to a wrong decision. [CLAUDE.core.md §Core Code Conventions]
- Do NOT reduce planned scope mid-execution. If the scope looks wrong, stop and report it — the cut is not yours to make. [feedback_dont_unilaterally_reduce_planned_scope.md]
- End your turn only after every command you started has finished. A returned child cannot be resumed, so a report promised for when a background run finishes is never written. Run the edited unit's proof file in the foreground (RED-first), not the bound suite; `rules/harness_tooling.md` moves that suite to close, and a brief that orders it is a rail conflict to report. [rules/harness_tooling.md; orchestration §4]
- Write each finished unit of your deliverable (a section, a Part, a file) to its path as it settles, never held for one write at the end: after a compaction your re-injected brief resumes from what is on disk, and reasoning held only in context is lost. [orchestration §4]
- Your report, digest or findings file is a judgment-dense artifact you author directly with `Write`. Size copyable worker inputs against the configured total prompt, context window and output room. There is no fixed per-file byte cap. [CLAUDE.core.md §9; ~/.config/ai-worker/server.py `_plan_window`]

## condensed

- Run only your changed items' targeted tests, plus the repo-scan lints your layer's rails name; a scoped suite run and the regression gate are the dispatcher's at close (`change_control` §Gate cadence): report them owed. A wide run your brief itself names is the one exception; any other is refused.
- In the shared main checkout never commit or branch; return `changedPaths` for the dispatcher. A worktree on its own branch commits there (orchestration reference writer_returns.md).
- Comments default to none (CLAUDE.core.md §Core Code Conventions). Planned scope is not yours to cut (`feedback_dont_unilaterally_reduce_planned_scope.md`).
- End your turn only after every command you started has finished: a returned child cannot be resumed. Run the edited unit's proof file in the foreground (RED-first), not the bound suite, and report a brief that orders it as a rail conflict (`rules/harness_tooling.md`; orchestration §4).
- Write each finished unit of your deliverable to its path as it settles, never one write at the end: a compaction resumes only from what is on disk (orchestration §4).

## minimal

Read this file's `## condensed` section.
