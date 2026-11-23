# Loss-floor port record (S5, S5b)

Where each v1 floor case went, and every case v2 does not carry as written. Sources: PushinPotions `origin/main`
`37128158e` (PP) and MaddManager `main` `abd59061` (MM). The five `test_dangerous_shell_guard_*` files and
`test_kill_guard.py` are the same cases in both repos; MM's `test_git_guardrails_*` files are an older generation
with the extra rows marked MM below. Every v1 file named in the first column is deleted from the template once its
cases are rows.

## Ported rows

Counts are the v1 cases ported; the v2 file's pytest items group them (one parametrized row per case, or one test
holding a v1 case's several asserts).

| v1 file | PP | MM | v2 file (items) |
|---|---|---|---|
| `test_dangerous_shell_guard_rm_scope.py` | 66 | 66 | `test_floor_delete.py` (228, with S4's 7 rows and the D-TT and mktemp rows) |
| `test_dangerous_shell_guard_absent_target.py` | 36 | 36 | `test_floor_delete.py` |
| `test_dangerous_shell_guard_cache_cleanup.py` | 80 | 80 | `test_floor_delete.py` |
| `test_dangerous_shell_guard_owned_temp_cleanup.py` | 22 | 22 | `test_floor_delete.py` |
| `test_dangerous_shell_guard_session_dir.py` | 13 | 13 | `test_floor_delete.py` |
| `test_regenerable_clone.py` (3 parts), `test_conformance_j8.py`'s two nested-submodule retirement cases | whole | whole | `test_floor_retire.py` (166) |
| `test_git_guardrails_advice.py` (sections 0-3) | 95 | 95 + 11 stash-held | `test_floor_git_discard.py` (107, with the one-row-per-case table check) |
| `test_git_guardrails_amend.py` | 6 | 6 | `test_floor_peer_loss.py` (21) |
| `test_git_guardrails_revert_lane.py` | 8 | 8 | `test_floor_peer_loss.py` |
| `test_peer_loss.py` | 11 | — | `test_floor_peer_loss.py` |
| `test_git_guardrails_owner_branch_delete.py` | 12 | 12 | `test_floor_git_owner.py` (42) |
| `test_git_guardrails_owner_discard.py` | 17 | 17 + 3 deleted-file | `test_floor_git_owner.py` |
| `test_git_guardrails_scratch_branch_delete.py` | 10 | 10 | `test_floor_git_owner.py` |
| `test_git_guardrails_checkout_own_worktree.py`, `test_pattern_enforcer_own_worktree*.py` (4 parts) | 118 | 118, launcher and loose-file rows differ (D-LB, D-LS) | `test_floor_own_worktree.py` (119) |
| `test_git_guardrails_index_fold.py` (fold and index-write rows) | 34 | — | `test_floor_index_fold.py` (34) |
| `test_git_guardrails_harness_commit.py` (sections W, G1, G2, G3, S, M) | 44 | 44 + `-C $UNSET` | `test_floor_commit_index.py` (61, with the D-IW rows) |
| `test_git_commit_msg_hook.py` | 16 | 16 | `test_floor_commit_msg.py` (17, plus the S5 CLI row) |
| `test_kill_guard.py` | 73 | 73 | `test_floor_kill.py` (154) |
| `test_reap.py`, `test_reap_native_env.py` | whole | whole | `test_floor_carried.py` (2): each runs whole, with `tools/reap.py` (Owed) |

`test_floor_entry.py` (32) holds T1's entry cases: one spawned stub call per decision kind, a broken import of each
`_floor.FLOOR_MEMBERS` row (the row set and the case set must be equal) and of the shared lexer, an unparseable
`_floor.py`, a raising `decide_text`, an unreadable payload, a fresh `import _floor` that loads none of
`_hook_state`, `cli_usage`, `index_commit` and `reap`, a decision whose git reads outrun `_floor.BUDGET_S`, the
dispatcher's precedence and helper-skip rules, and the kept Bash helpers (`HELPER_NAMES`: the length cut, the
recursive-grep deny on Bash, PowerShell and Monitor, the backslash repair) judged through the one entry.

S4's rows (plan S5 input) are in `test_floor_delete.py` `test_s4_*`: a plugin download under
`~/.claude/plugins/cache/` holding no repository is a new allow (approved in the plan, OF-guards); the cache root,
anything above it and a folder holding a repository stay denied; a temp tree allows whether or not it holds
repositories (D-TT).

## The owner's rulings on the drop list (2026-11-23)

| id | v1 case | ruling | rows |
|---|---|---|---|
| D-IW | the index-write deny: a commit that `git add`/`rm`/`reset`/`stash`/`apply --cached` precedes in the same command (`SAME_COMMAND_WRITE`) | Narrowed to what git's commit-msg hook cannot re-check: the call skips hooks (`-n`, `--no-verify`, a bundled `n`), the command overrides git config (`core.hooksPath`, `GIT_CONFIG_*`), the repository's `core.hooksPath` is not `.claude/githooks` or its `commit-msg` is missing, or the publication amends (the hook re-runs the stage-record and peer-revert checks, not the amend check: `COMMIT_CHECKS`). | `test_floor_commit_index.py` `test_an_index_write_before_a_hooked_commit_allows`, `..._that_skips_the_hook_denies`, `test_a_head_move_before_an_amend_denies_even_when_hooked`, `test_a_hooks_path_without_the_commit_msg_hook_denies` |
| D-TT | a recursive delete of a temp tree holding git repositories (S4's `v2s4_s4b`) | Allowed: the core doctrine exempts temp dirs. The protected checkout, a worktree registered to it, and any path strictly inside the checkout (wherever the temp dir sits) keep their rules. | `test_floor_delete.py` `test_s4_a_temp_tree_holding_git_repositories_allows` and its project-root deny |
| D-LB | PP's allow of an own-worktree discard inside a shell launcher (`bash -c`, `sh -c`, `eval`, `env … bash -c`) | Kept MM's deny: a launcher-body call borrows no worktree ownership (`git_verdict`); stored-bytes evidence still allows inside a launcher. | `test_floor_own_worktree.py` launcher rows |
| D-LS | PP's allow of a loose file directly in a worktree's or scratch clone's `.claude/scratch/` | Kept MM's deny: a loose scratch file is evidence, not a job's output folder. | `test_floor_own_worktree.py` and `test_floor_retire.py` loose-file rows |
| K-XA | `kill_guard.py`'s docstring refused "an `xargs kill` fed by anything but pgrep", but the bare `<producer> \| xargs kill` passed | Denied, with every kill of pids the call does not name (`$VAR`, `$(...)` or backtick operands of `kill`, `taskkill /PID`, `Stop-Process -Id`); `$!`, `kill -0` and a `pgrep`/`pidof` substitution stay named; a quoted mention is data. `reap.py` stays the one stop route. | `test_floor_r1_branches.py` `test_a_kill_of_pids_the_call_does_not_name_denies` and its allow rows |

## Outcome changes beyond the rulings

- T1's lost allows (plan-approved, `harness-v2.md` T1: "all allow cases that now ask once"). Each allowed on `065798f`
  and now denies, so the agent asks the owner once: an owned worktree holding an ignored file, an owned worktree with
  a stored edit (`test_a_worktree_this_session_created_is_judged_like_any_other`); an untracked Godot `.import` file
  and an ignored Godot import temp file (the `imports` and `import_tmp` rows); an unpushed HEAD held only by a
  saved bundle (`test_a_saved_bundle_no_longer_retires_an_unpushed_worktree`). The rule reads "who created the worktree
  does not change the answer", so a submodule whose admin directory is gone now denies in an owned worktree too
  (`test_a_submodule_whose_admin_directory_is_gone_denies`). Tar and patch saved copies go with the bundle.
- Two new allows of the command's own `NAME=$(mktemp -d)` directories (temp trees, D-TT's reason): a multi-line
  script (v1's chain splitter missed newlines) and two mktemp variables in one `rm` (`test_floor_delete.py`
  `OWNED_PASS`).
- The recursive-grep deny now runs in the one entry. Through v1's `pre_bash_dispatch` it already denied these
  calls; S5's floor alone did not run it.

## Branch read (plan R1)

Method: the table rows ran under a line recorder over `dangerous_shell_guard.py`, `kill_guard.py` and `_floor.py`;
each unexecuted line was read for a deny or allow branch. `test_floor_r1_branches.py` adds a row for each v1 branch
no v1 test reached that a row can reach in process: the `--force-with-lease=`/`--force-if-includes=` and bundled
`-f` push spellings, `branch -D` naming no branch, the absent-target rewrite to `true` (and its present-target
deny), an invalid `killall -r` pattern, `Stop-Process -InputObject`, an unconditioned `wmic process delete`,
`xargs <opts> kill` fed by a non-pgrep producer, and a Win32_Process Terminate with no readable filter. Each asserts
v1's own answer, except K-XA's rows, which assert the ruling.

Left without an in-process row: fail-closed returns on a git read that fails (`_stash_changes_held`,
`stage_record_loss`), message variants of a branch whose deny a row already asserts (`HEAD cannot be read` on amend,
the `.git`-file reason), and the live process-table and PID readers (`reap.process_table`, the CIM and `ps`
lookups), which every row plants.

## Not carried as decision rows

- V1's Lost column (plan V1): `git_guardrails.py`'s process advisories and their rows: proof-required and unproven
  proofs, excluded proofs, skill verdict, re-attach cap (`test_git_guardrails_reattach_cap.py`), markdown density
  (`test_git_guardrails_md_density.py`), commit advice (`test_git_guardrails_commit_advice.py`), layer closure,
  pull proof, harness push close, `test_git_guardrails_advice.py` sections 4-5, the advisory sections of
  `test_git_guardrails_harness_commit.py`, and MM's merge-drop advisory (`test_git_guardrails_merge_drops.py`).
- V17's Lost column: `unbounded_scan_guard.py`'s volume and scope advisories (`test_unbounded_scan_guard*.py`; the
  deny's rows stay in `tests/test_recursive_grep_deny.py`), and `bash_backslash_fidelity.py`'s colon-operand
  `MSYS_NO_PATHCONV` repair (`test_s9_colon_operands`), cut to the backslash class (OF-V17).
- Rows that assert no allow or deny: v1's call-count asserts (absent-target `ls-files` read counts; the one kept,
  a launcher body walked once, runs the git check alone as v1 did), the kill test helper's self-check, the
  `settings.json` TaskStop registration (now `_floor.MATCHER`, asserted in `test_floor_entry.py`), the index-fold
  file's script-route meta rows (10), v1's per-file "imports and runs" spawn (one spawned entry case now), the
  deletion rule's `--retire-check` CLI (now `python3 .claude/hooks/_delete_rule.py <path>...`) and its timeout
  environment variable.
- A v1 fixture that is gone: the recovered `rm_denials.json` replay (gitignored scratch, absent in both repos).

## Suite cost

`conftest.py` prints each file's seconds and git and other spawns after every run. The floor's time is git
spawns: each real-repository row builds its fixture and the decision reads git several times; in-process rows
without git take milliseconds.

## Owed

- The small stop tool (T1). `tools/reap.py` carries unchanged with its whole v1 proofs: on `v2`, `dispatch_table.py`,
  `shell_census.py`, `sidecar_fanout.py` and `watch_records.py` still call about twenty of its internals
  (`invocation`, `session_live`, `owner_of`, `launch_census`, `live_launches`, `describe_launches`, `git_bash`,
  `process_exited`, ...), and `kill_guard.py` and `scripts/sync_plan_config.py` call `process_table`. The rewrite
  lands after B1/B16 delete those importers, keeping `process_table` for the two kept callers, with its proofs as rows.
- `hooks/approving_gates.py` and its test (B10's fourth delete): `sidecar_dispatch_context.py`,
  `pre_bash_dispatch.py` and `denial_coach.py` still import it on `v2`; it goes with that dispatch cut.
- Cutover deletes (S5 review finding 10), each a parallel path to a floor module: `git_guardrails.py`'s loss
  denies (`dangerous_shell_guard.py` owns them), `pre_bash_dispatch.py`'s `git_guardrails` and
  `dangerous_shell_guard` rows, `_git_commit.py`'s `git_environ` re-export and its `tools/` `sys.path` insert for
  v1's `sidecar_argv` importers, and three of the four command segmenters (`_shell_lex.shell_segments`,
  `_git_commit.segments`, `dangerous_shell_guard._segments_with_quotes`, `kill_guard._shell_segments`), which
  become one.
- `settings.json` registers `hooks/pre_bash.py` for `_floor.MATCHER` at cutover; until then the v1 entries stay.
- `baseline.manifest.json` (lane root, v1 publish manifest) still lists the deleted v1 test files; the manifest's
  rewrite (S7) drops them.
