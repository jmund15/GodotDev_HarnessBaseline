# Worktrees and runtime-test lanes

Read before running runtime-test lanes concurrently, before a job runs in another checkout (`isolation: "worktree"` or a sidecar `workdir`), or before retiring a worktree (`orchestration` §2 and §7 point here).

## Runtime-slot rule

Concurrent runtime-test lanes never exceed the runtime slot count their test harness reports. Read that harness's reported count or configured default before dispatch, not another checkout's capacity. Serialize excess lanes, or bar runtime tests for the chain dispatch through `sharedSingleFlight`; queued lanes spend wall time without running tests.

## Worktree caveat

`isolation: "worktree"` is for write-parallel work only; it costs setup time and disk per agent, and its submodule must be initialized before builds. Main owns broad verification; worktree-lane verification follows the runtime-slot rule above, and jobs sharing one checkout serialize their runs on its lock.

A job reads its harness rules (`.claude/`) from the checkout it runs in, so cut its branch from main, or bring main's `.claude/` onto the branch it is cut from before launch (merge main, or check out main's `.claude/` there when the app code would conflict); a branch whose `.claude/` lags main's hands the job rules main has replaced. Only a run that measures old text (a probe arm) starts from an older base on purpose.

A lane branch merges back through a commit that builds: `githooks/pre-merge-commit` (and `pre-commit` while `MERGE_HEAD` exists) builds a merge that changes C# or moves the Jmodot pointer and refuses its commit on a compile error, since branches that each compile can merge into a tree that does not. A checkout with a live editor or playtest, or a Jmodot checkout behind the moved pointer, is refused with its repair instead of built; `--no-verify` bypasses.

Follow `CLAUDE.core.md` §Shell Discipline for worktree ownership, removal and branch-deletion authority. Verify the resolved full path with the session's worktree-ownership helper (`session_created_worktree`). Preserve commits and files later steps need outside the target. For recursive retirement, fetch submodules, run `rm -rf .claude/worktrees/<name>` alone, then `git worktree prune`. `/worktree_prune` classifies other sessions' worktrees and removes none. Name throwaway branches `scratch/<name>`. Delete them with `git branch -D` when their use ends.
