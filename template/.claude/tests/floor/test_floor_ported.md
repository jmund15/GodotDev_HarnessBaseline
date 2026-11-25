# Loss-floor port record

Where each v1 floor case went, and every v1 case not carried as written. The rows are v2's (S5, S5b; sources
the game project's `origin/main` `37128158e` (G) and MaddManager `main` `abd59061` (MM)), carried onto the v1 harness
(b2): `hooks/pre_bash.py` is the one fail-closed entry, it runs `hooks/pre_bash_dispatch.py`, and the floor members
there are `dangerous_shell_guard.py` (every git and delete loss check, with `_delete_rule.py`, `_own_worktree.py`
and `_session_lineage.py`) and `kill_guard.py`. Every shell call is read by `_shell_lex.py`. A row decides through
`case.decide`, which is `pre_bash_dispatch.decide`. Every v1 file named in the first column is deleted once its cases
are rows.

## Ported rows

| v1 file | G | MM | floor file |
|---|---|---|---|
| `test_dangerous_shell_guard_rm_scope.py` | 66 | 66 | `test_floor_delete.py` (with S4's rows, the D-TT and mktemp rows and the session-lineage rows) |
| `test_dangerous_shell_guard_absent_target.py` | 36 | 36 | `test_floor_delete.py` |
| `test_dangerous_shell_guard_cache_cleanup.py` | 80 | 80 | `test_floor_delete.py` |
| `test_dangerous_shell_guard_owned_temp_cleanup.py` | 22 | 22 | `test_floor_delete.py` |
| `test_dangerous_shell_guard_session_dir.py` | 13 | 13 | `test_floor_delete.py` |
| `test_regenerable_clone.py` (3 parts), the J8 conformance proof's two nested-submodule retirement cases | whole | whole | `test_floor_retire.py` |
| `test_git_guardrails_advice.py` (sections 0-3) | 95 | 95 + 11 stash-held | `test_floor_git_discard.py` (with the one-row-per-case table check) |
| `test_git_guardrails_amend.py` | 6 | 6 | `test_floor_peer_loss.py` |
| `test_git_guardrails_revert_lane.py` | 8 | 8 | `test_floor_peer_loss.py` |
| `test_peer_loss.py` | 11 | — | `test_floor_peer_loss.py` |
| `test_git_guardrails_owner_branch_delete.py` | 12 | 12 | `test_floor_git_owner.py` |
| `test_git_guardrails_owner_discard.py` | 17 | 17 + 3 deleted-file | `test_floor_git_owner.py` |
| `test_git_guardrails_scratch_branch_delete.py` | 10 | 10 | `test_floor_git_owner.py` |
| `test_git_guardrails_checkout_own_worktree.py`, `test_pattern_enforcer_own_worktree*.py` (4 parts) | 118 | 118, launcher and loose-file rows differ (D-LB, D-LS) | `test_floor_own_worktree.py` (the earlier-transcript row is now the lineage rows) |
| `test_git_guardrails_index_fold.py` (fold and index-write rows) | 34 | — | `test_floor_index_fold.py` |
| `test_git_guardrails_harness_commit.py` (sections W, G1, G2, G3, S, M) | 44 | 44 + `-C $UNSET` | `test_floor_commit_index.py` (with the D-IW rows) |
| `test_git_commit_msg_hook.py` | 16 | 16 | `test_floor_commit_msg.py` (plus the CLI row) |
| `test_kill_guard.py` | 73 | 73 | `test_floor_kill.py` (with the TaskStop-on-the-lineage rows and the J6 conformance proof's TaskStop cases) |

`test_floor_entry.py` holds the entry cases: one spawned `pre_bash.py` call per decision kind, a broken import of
each floor member (the member set and the case set must be equal), of the shared lexer and of the dispatcher's
bootstrap, an unparseable dispatcher, a raising `decide_text`, an unreadable payload, the registration (one
`pre_bash.py` entry for `Bash|PowerShell|Monitor|TaskStop`, no floor member registered on its own), a floor
member whose git reads outrun `pre_bash_dispatch.BUDGET_S` and a member outside the floor that keeps its own
budget, the dispatcher's fault posture and precedence rules, and the kept Bash helpers judged through the one entry.

S4's rows are in `test_floor_delete.py` `test_s4_*`: a plugin download under `~/.claude/plugins/cache/` holding no
repository is a new allow (OF-guards); the cache root, anything above it and a folder holding a repository stay
denied; a temp tree allows whether or not it holds repositories (D-TT).

## The owner's rulings on the drop list (2026-11-23)

| id | v1 case | ruling | rows |
|---|---|---|---|
| D-IW | the index-write deny: a commit that `git add`/`rm`/`reset`/`stash`/`apply --cached` precedes in the same command (`SAME_COMMAND_WRITE`) | Narrowed to what git's commit-msg hook re-checks with every check the chain ran: the hook runs `pre_bash_dispatch.commit_time`, which re-runs the stage-record and peer-revert checks and every `COMMIT_JUDGES` member on the index git writes. The deny stays for a publication other than `git commit` (git runs commit-msg for commit and merge only, and the hook passes a merge), a call that skips hooks (`-n`, `--no-verify`, a bundled `n`), a command that overrides git config (`core.hooksPath`, `GIT_CONFIG_*`), a repository whose `core.hooksPath` is not `.claude/githooks` or whose hook, dispatcher or commit judges differ from this checkout's, an amend (the amend check read HEAD before the command moved it), and the commit that closes a merge. | `test_floor_commit_index.py` `test_an_index_write_before_a_hooked_commit_allows`, `..._that_skips_the_hook_denies`, `..._whose_hook_is_not_this_harnesss_denies`, `..._a_continue_git_runs_no_commit_msg_hook_for_denies`, `test_a_head_move_before_an_amend_denies_even_when_hooked`, `test_an_index_write_before_the_commit_that_closes_a_merge_denies_even_when_hooked`, `test_a_hooks_path_without_the_commit_msg_hook_denies`; `test_floor_commit_msg.py` `test_the_hook_refuses_what_each_commit_judge_refuses`, `test_an_add_then_commit_the_floor_allows_is_refused_when_git_writes_the_commit` |
| D-TT | a recursive delete of a temp tree holding git repositories (S4's `v2s4_s4b`) | Allowed: the core doctrine exempts temp dirs. The protected checkout, a worktree registered to it, and any path strictly inside either (wherever the temp dir sits) keep their rules. | `test_floor_delete.py` `test_s4_a_temp_tree_holding_git_repositories_allows`, its project-root deny, `test_s4_a_folder_inside_a_registered_worktree_under_temp_stays_denied` and `test_s4_a_temp_folder_beside_a_registered_worktree_still_goes` |
| D-LB | G's allow of an own-worktree discard inside a shell launcher (`bash -c`, `sh -c`, `eval`, `env … bash -c`) | Kept MM's deny: a launcher-body call borrows no worktree ownership (`git_verdict`); stored-bytes evidence still allows inside a launcher. | `test_floor_own_worktree.py` launcher rows |
| D-LS | G's allow of a loose file directly in a worktree's or scratch clone's `.claude/scratch/` | Kept MM's deny: a loose scratch file is evidence, not a job's output folder. | `test_floor_own_worktree.py` and `test_floor_retire.py` loose-file rows |
| K-XA | `kill_guard.py`'s docstring refused "an `xargs kill` fed by anything but pgrep", but the bare `<producer> \| xargs kill` passed | Denied, with every kill of pids the call does not name (`$VAR`, `$(...)` or backtick operands of `kill`, `taskkill /PID`, `Stop-Process -Id`); `$!`, a variable every assignment of which is `$!` (`PID=$!; kill $PID`, v1's allow), `kill -0` and a `pgrep`/`pidof` substitution stay named; a quoted mention is data. `reap.py` stays the one stop route. | `test_floor_r1_branches.py` `test_a_kill_of_pids_the_call_does_not_name_denies` and its allow rows |

## Outcome changes beyond the rulings

- Kept from v1 (v2's T1 had made each an ask): a worktree this session created goes with its ignored files and its
  stored edits (`test_a_worktree_this_session_created_takes_its_ignored_files_and_stored_edits`; an edit no blob
  holds still denies), and a submodule whose admin directory is gone is judged by its files there; an untracked
  Godot `.import` file and an ignored Godot import temp file are regenerated output (the `imports` and `import_tmp`
  rows); and a saved copy under `.claude/scratch` the rule verifies stands in for a push (`_saved_copy_holds`: a
  bundle holding every unpushed commit, a tar or patch holding every file the status would lose), for a retired
  worktree's HEAD, a scratch clone and a nested copy in a worktree's scratch (`test_floor_retire.py`
  `..._a_bundle_holds_...`, `..._a_tar_holds_...` rows).
- A submodule whose path holds a space (D18): v1 split `submodule status` on whitespace and skipped it unchecked; the
  rule now judges it, so a dirty one denies and a clean one retires (`..._submodule_path_holds_a_space_...` rows).
- Two new allows of the command's own `NAME=$(mktemp -d)` directories (temp trees, D-TT's reason): a multi-line
  script and two mktemp variables in one `rm` (`test_floor_delete.py` `OWNED_PASS`).
- New allows (b8 S6, the recorded call F7811): a non-forced `git switch` or `git checkout --detach <commit>`
  discards nothing, so it passes in any repository, an unproved `-C $W` included (v1 denied the unproved
  form). A forced, merging, orphan or force-create switch and a checkout that may name paths keep their deny,
  and the switch still counts as an index write before a later commit (`test_floor_git_discard.py` S6 rows).
- PowerShell here-strings (review N-F14): in a PowerShell call, or a body read as PowerShell, `@'...'@` and
  `@"..."@` are one word of data, so a kill or delete spelled inside one passes (new allows); a here-string
  a shell or `Invoke-Expression` runs is code, and an `@"..."@` subexpression runs, so those deny (two new
  denies: piped to `powershell -` and named by `Invoke-Expression`, both allowed before). A Bash call reads
  the same text as Bash does (`test_floor_kill.py` `test_a_powershell_here_string`, `test_floor_delete.py`
  `test_n_f14_*`).
- A commit pathspec the shell expands (`$(git diff --name-only)`, a backquote, `$FILES`) passes as in v1; its
  text naming a path outside the repository denies, as v1's 26a did (`test_floor_commit_index.py` V15 rows).
- New denies, v1 allowed each (review N-F3, N-F5): a git command word in any case, with `.exe`, a path, quotes or
  escapes (`GIT reset --hard`, `g\it`, `g"i"t`) is git to every git check, and `git push --repo=<remote>
  +<ref>` is a force-push (`test_floor_git_discard.py` N-F3 rows); `HARNESS_ALLOW_KILL` counts only as a prefix
  of a command the call runs, so a heredoc body or a comment spelling it unlocks nothing (`test_floor_kill.py`
  N-F5 rows).
- New allows (review N-F8): a path that only looks like a cmd switch run (`rmdir /tmp/sub`) is no `/s`, and a
  delete word among the arguments of a program that reads them as data (`rg del /src/foo`, `git log --pretty
  format C:/foo`, `echo rm -rf /x`) deletes nothing; every runner position keeps its deny
  (`test_floor_delete.py` `ARGUMENT_POSITION_PASS` and `ARGUMENT_POSITION_BLOCK`).
- A TaskStop reads the session's transcript lineage, so a task launched before a `/clear` or a compaction in the same
  process can be stopped.
- The bypasses (`936577a`: each spelling v1 parsed past) and the six new deny classes (`602be4b`) deny.

## Branch read (plan R1)

Method (v2, not re-recorded by the port): the table rows ran under a line recorder over `dangerous_shell_guard.py`
and `kill_guard.py`; each unexecuted line was read for a deny or allow branch. `test_floor_r1_branches.py` adds a row
for each v1 branch no v1 test reached that a row can reach in process: the `--force-with-lease=`/`--force-if-includes=`
and bundled `-f` push spellings, `branch -D` naming no branch, the absent-target rewrite to `true` (and its
present-target deny), an invalid `killall -r` pattern, `Stop-Process -InputObject`, an unconditioned
`wmic process delete`, `xargs <opts> kill` fed by a non-pgrep producer, and a Win32_Process Terminate with no
readable filter. Each asserts v1's own answer, except K-XA's rows, which assert the ruling.

Left without an in-process row: fail-closed returns on a git read that fails (`_stash_changes_held`,
`stage_record_loss`), message variants of a branch whose deny a row already asserts (`HEAD cannot be read` on amend,
the `.git`-file reason), and `kill_guard`'s live CIM and `ps` PID lookups, which every row plants.

The harness push close (`harness_push_verdict`, v1's floor guard) is carried: the push-land proof's guard cases are
`test_floor_commit_index.py::test_the_harness_push_close`; its push-range and proved-push cases stay in the
push-land proof.

## Not carried as decision rows

- `git_guardrails.py`'s process advisories and their rows: proof-required and unproven proofs, excluded proofs,
  skill verdict, re-attach cap (`test_git_guardrails_reattach_cap.py`), markdown density
  (`test_git_guardrails_md_density.py`), layer closure, pull proof, `test_git_guardrails_advice.py`
  sections 4-5, the advisory sections of `test_git_guardrails_harness_commit.py`, and the merge-drop advisory.
- Rows that assert no allow or deny: v1's call-count asserts (absent-target `ls-files` read counts; the one kept,
  a launcher body walked once, runs the git check alone as v1 did), the kill test helper's self-check, the
  index-fold file's script-route meta rows (10), v1's per-file "imports and runs" spawn (one spawned entry case
  now), the deletion rule's `--retire-check` CLI (now `python3 .claude/hooks/_delete_rule.py [--created] <path>...`)
  and its timeout environment variable.
- A v1 fixture that is gone: the recovered `rm_denials.json` replay (gitignored scratch, absent in both repos).

## The stop tool and the kill check

`tools/reap.py` keeps every v1 verb and reason class. It carries one fix from v2 (`9091bba`): a recorded parent,
Windows or MSYS, is a parent only if it started no later than the child (`parents`, read by `descendants`,
`ancestors`, the never gate and the session-host walk), and a stop takes the verified tree member by member,
deepest first, never `taskkill /T`, which walks reused OS parent pids. v1's reap proof holds the rows.

`kill_guard.py` keeps v1's eight protected patterns (the sidecar fan-out and a launcher's child, the harness proof
run, a gate or test run, a claude session, the proxy, a benchmark grid); a project's `adaptation.json`
`pre_bash.protected` rows add to them. TaskStop's paid-work ask reads v1's launchers
(`sidecar_argv.launcher_invocations`).

## Suite cost

`conftest.py` prints each file's seconds and git and other spawns after every run. The floor's time is git
spawns: each real-repository row builds its fixture and the decision reads git several times; in-process rows
without git take milliseconds.

## Owed

- The rows run under pytest, and `scripts/harness_tests.py` runs them when a floor module changes: `catalog.FLOOR_MODULES` binds every row to each module the floor judges, and a row to itself.
- The chain runner keeps v1's dispatcher (`main()` members, per-member fault posture) until its redesign.
- `tools/sidecar_argv.py` keeps its own command segmenter; the one-lexer test scans `hooks/` only.
