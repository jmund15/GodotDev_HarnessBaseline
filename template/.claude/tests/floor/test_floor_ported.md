# Loss-floor port record (S5)

Where each v1 floor case went, and every case v2 does not carry as written. Sources: PushinPotions `origin/main`
`37128158e` (PP) and MaddManager `main` `abd59061` (MM). The five `test_dangerous_shell_guard_*` files and
`test_kill_guard.py` are the same cases in both repos; MM's `test_git_guardrails_*` files are an older generation
with the extra rows marked MM below.

## Ported rows

Counts are the v1 cases ported; the v2 file's pytest items group them (one parametrized row per case, or one test holding a
v1 case's several asserts).

| v1 file | PP | MM | v2 file (items) |
|---|---|---|---|
| `test_dangerous_shell_guard_rm_scope.py` | 66 | 66 | `test_floor_delete.py` (217, plus 7 S4 rows) |
| `test_dangerous_shell_guard_absent_target.py` | 36 | 36 | `test_floor_delete.py` |
| `test_dangerous_shell_guard_cache_cleanup.py` | 80 | 80 | `test_floor_delete.py` |
| `test_dangerous_shell_guard_owned_temp_cleanup.py` | 22 | 22 | `test_floor_delete.py` |
| `test_dangerous_shell_guard_session_dir.py` | 13 | 13 | `test_floor_delete.py` |
| `test_git_guardrails_advice.py` (sections 0-3) | 95 | 95 + 11 stash-held | `test_floor_git_discard.py` (106) |
| `test_git_guardrails_amend.py` | 6 | 6 | `test_floor_peer_loss.py` (21) |
| `test_git_guardrails_revert_lane.py` | 8 | 8 | `test_floor_peer_loss.py` |
| `test_peer_loss.py` | 11 | — | `test_floor_peer_loss.py` |
| `test_git_guardrails_owner_branch_delete.py` | 12 | 12 | `test_floor_git_owner.py` (42) |
| `test_git_guardrails_owner_discard.py` | 17 | 17 + 3 deleted-file | `test_floor_git_owner.py` |
| `test_git_guardrails_scratch_branch_delete.py` | 10 | 10 | `test_floor_git_owner.py` |
| `test_git_guardrails_checkout_own_worktree.py`, `test_pattern_enforcer_own_worktree*.py` (4 parts) | 118 | 118, launcher and loose-file rows differ (D-LB, D-LS) | `test_floor_own_worktree.py` (118) |
| `test_git_guardrails_index_fold.py` (fold and index-write rows) | 34 | — | `test_floor_index_fold.py` (34) |
| `test_git_guardrails_harness_commit.py` (sections W, G1, G2, G3, S, M) | 44 | 44 + `-C $UNSET` | `test_floor_commit_index.py` (45) |
| `test_git_commit_msg_hook.py` | 16 | 16 | `test_floor_commit_msg.py` (17, plus the S5 CLI row) |
| `test_kill_guard.py` | 73 | 73 | `test_floor_kill.py` (154) |
| `test_reap.py`, `test_reap_native_env.py`, `test_regenerable_clone.py` (3 parts) | whole | whole | `test_floor_carried.py` (5): each runs whole |

`test_floor_entry.py` (20) adds T1's entry cases: one spawned stub call per decision kind, a broken import of each
floor module and of the shared lexer, an unparseable `_floor.py`, a raising `decide_text`, an unreadable payload,
and the dispatcher's precedence and helper-skip rules.

S4's two rows (plan S5 input) are in `test_floor_delete.py` `test_s4_*`: a plugin download under
`~/.claude/plugins/cache/` holding no repository is a new allow (approved in the plan, OF-guards); the cache root,
anything above it and a folder holding a repository stay denied; a temp tree holding no repository allows (v1
already did); a temp tree holding repositories keeps v1's deny for the owner to rule on (D-TT).

## Drop list (nothing is dropped until the owner approves)

| id | v1 case | evidence | what covers it in v2, or the loss |
|---|---|---|---|
| D-IW | the index-write deny: a commit that `git add`/`rm`/`reset`/`stash`/`apply --cached` precedes in the same command (`SAME_COMMAND_WRITE`, 9 deny rows in `test_floor_commit_index.py`) | The commit-time entry (`dangerous_shell_guard.py --commit-msg`) re-runs `base_verdict` (stage records) and `revert_verdict` (peer blobs) against the index git is writing, so both checks see the write. `amend_verdict` reads HEAD and its trailer, which an index write does not change, so the pre-tool check stays valid. | Covered only when git runs the githook. Not covered: `git commit -n`/`--no-verify`, and a checkout whose `core.hooksPath` is not `.claude/githooks`. A tracked `add && commit` already folds to a pathspec commit before any check. Proposed rule if dropped: deny only when the commit would skip the githook (either fact is readable at check time). Kept in S5. |
| D-LB | PP's allow of an own-worktree discard inside a shell launcher (`bash -c`, `sh -c`, `eval`, `env … bash -c`; PP `test_shell_launcher_discards_are_judged_on_resolved_facts`) | MM denies the same rows (`test_shell_launcher_discards_cannot_borrow_ownership`); v2 keeps MM's deny: a launcher-body call borrows no worktree ownership (`git_verdict`). Stored-bytes evidence still allows inside a launcher (PP's fold rows). | Loss of the PP allow: a launcher-wrapped discard in the session's own worktree now asks the owner. |
| D-LS | PP's allow of a loose file directly in a worktree's or scratch clone's `.claude/scratch/` (PP `test_pattern_enforcer_own_worktree` `job_output.txt`, PP `test_regenerable_clone.py` `notes.md`) | MM `7b8e6e66` denies it: a loose scratch file is evidence, not a job's output folder; v2 carries MM's `_regenerable_clone.py` hunks. | Loss of the PP allow: such a worktree deletion asks the owner. |
| D-TT | a recursive delete of a temp tree this session made that holds git repositories (S4's `v2s4_s4b`, five repos) | v1 denies (a temp path must hold no repository); `CLAUDE.core.md` exempts temp dirs without saying whether one holding repositories counts. | v2 keeps the deny; the owner rules. |

## Branch read (plan R1)

Method: the table rows ran under a line recorder over `dangerous_shell_guard.py`, `kill_guard.py` and `_floor.py`;
each unexecuted line was read for a deny or allow branch. `test_floor_r1_branches.py` adds a row for each v1 branch
no v1 test reached that a row can reach in process: the `--force-with-lease=`/`--force-if-includes=` and bundled
`-f` push spellings, `branch -D` naming no branch, the absent-target rewrite to `true` (and its present-target
deny), an invalid `killall -r` pattern, `Stop-Process -InputObject`, an unconditioned `wmic process delete`,
`xargs <opts> kill` fed by a non-pgrep producer, and a Win32_Process Terminate with no readable filter. Each asserts
v1's own answer.

Left without an in-process row: the registered-worktree and live-peer reasons of the scratch and worktree delete
checks (the carried `test_regenerable_clone*.py` reach them through the spawned entry), fail-closed returns on a git
read that fails (`_stash_changes_held`, `stage_record_loss`), message variants of a branch whose deny a row already
asserts (`HEAD cannot be read` on amend, the `.git`-file reason), and the live process-table and PID readers
(`reap.process_table`, the CIM and `ps` lookups), which every row plants.

| id | finding | v2 |
|---|---|---|
| K-XA | `kill_guard.py`'s docstring refuses "an `xargs kill` fed by anything but pgrep", but the bare `<producer> \| xargs kill` passes: `kill` matches as its own verb, so the xargs selector never sees it. Only `xargs <option> kill` denies. | carries v1's answer; the owner rules whether the bare shape should deny (it would be a deny v1's code does not make) |

## Not carried as decision rows

- V1's Lost column (plan V1): `git_guardrails.py`'s process advisories and their rows — proof-required and unproven
  proofs, excluded proofs, skill verdict, re-attach cap (`test_git_guardrails_reattach_cap.py`), markdown density
  (`test_git_guardrails_md_density.py`), commit advice (`test_git_guardrails_commit_advice.py`), layer closure,
  pull proof, harness push close, `test_git_guardrails_advice.py` sections 4-5, the advisory sections of
  `test_git_guardrails_harness_commit.py`, and MM's merge-drop advisory (`test_git_guardrails_merge_drops.py`).
- Rows that assert no allow or deny: v1's call-count asserts (absent-target `ls-files` read counts; the one kept,
  a launcher body walked once, runs the git check alone as v1 did), the kill test helper's self-check, the
  `settings.json` TaskStop registration (now `_floor.MATCHER`, asserted in `test_floor_entry.py`), the index-fold
  file's script-route meta rows (10), and v1's per-file "imports and runs" spawn (one spawned entry case now).
- A v1 fixture that is gone: the recovered `rm_denials.json` replay (gitignored scratch, absent in both repos).

## Owed

- T1's one-rule worktree and clone deletion and the small stop tool replace `_regenerable_clone.py` and
  `tools/reap.py`; both carry unchanged here, so their v1 proofs run whole (`test_floor_carried.py`) until the
  redesign converts them to rows. The plan's three lost allow cases (regenerable output outside the named cache
  directories, ignored files this session created, commits saved to a bundle) land with that rule.
- `settings.json` registers `hooks/pre_bash.py` for `_floor.MATCHER` at cutover; until then the v1 entries stay.
