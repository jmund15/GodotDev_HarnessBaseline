# Local rewires: what each repo changes in its own files at cutover

Q6 O1: these ride in S9's cutover commit, so one revert undoes them. The shared repo carries none of them. This file replaces the per-lane hand-off notes the fix round collected (fix map FX-ADAPT, FX-SEAT, FX-NAT, FX-SEATS-DATA, FX-DISP, FX-LOADER, FX-FLOORH). The first pull's removal recipe and the local-file lists are in `delete_list.md`; the registrations are in `hook_registrations.md`.

## PushinPotions

### `skills/project_subsystems/adaptation.json`

| Change | Value or source |
|---|---|
| Add `pre_bash` | `{protected, router_entrypoints}` as `PROJECT_PRE_BASH` in `tests/floor/conftest.py` (the bench grid, transport proxy, gate-script and `vstest\|dotnet test` rows, and the router entrypoint rows, each `{name, redirect, detach_flag}`; a bare name string is malformed). Print it with the command below and copy it whole. |
| Add `scratch_keep` | `["session_end", "boot", "kfm", "eval_out", "linux_preview"]`, the v1 `TOOL_OWNED_SCRATCH` of `tools/scratch_prune.py`. Drop a name whose writing tool is gone from PushinPotions' tree. |
| Delete `usage_reset` | no reader remains |
| `gate_cadence`: keep only `slice`, `merge`, `close` | its `main` row (and any `wide` or `reach*` row) has no reader; `gate_cadence_errors` rejects an unknown key |
| Delete the unread keys | `bench_liveness`, `content_nouns`, `drive_commands`, `gate_guards`, `high_risk_patterns`, `instruction_scope`, `lens_prefixes`, `lens_preloads`, `memory_domains`, `proof_bindings`, `proof_corpus`, `proof_excluded`, `proof_heavy`, `proof_serial`, `proof_timeouts`, `review_mandates`, `review_scope`, `rigor_defaults`, `sidecar_hook_rows`, `usage_bench_markers` (the reader finds no entry in `adaptation.DEFAULTS`; an unknown key is harmless, so this is tidying, not a fix) |
| Keep, though no shared file reads them | `teardown_helpers`, `test_quarantine_filter`, `content_scopes`, `sensitivity_tags` (read by PushinPotions-local hooks and scripts, and the `tag` command) |
| Add the v2 keys it lacks | `hook_members`, `project_name`, `vault_root`, `worklog_domains`, `review.checklists`, `pr_ready.checks`, `pr_pipeline` (below), `overnight_dir`, `dispatch`, `index_prebuild`; their values are the `forked_reconcile.md` rows that name them |

```
python3 -c "import json,sys; sys.path.insert(0,'tests/floor'); sys.path.insert(0,'hooks'); import conftest; print(json.dumps(conftest.PROJECT_PRE_BASH, indent=2))"
```

Run it from `template/.claude/` of the shared repo (the conftest imports `pytest`). The key list under "Delete the unread keys" is `sorted(set(adaptation.json) - set(tools/adaptation.DEFAULTS))`.

### `pr_pipeline` and the local `/pr_pipeline`

The owner ruled one shared batch loop with each project's steps in `adaptation.json`. `commands/pr_pipeline.md` (local, 18.5 KB) is deleted at cutover and the shared `/pr_pipeline` replaces it, so PushinPotions' steps live in this block. Paste it into `adaptation.json`:

```json
"pr_pipeline": {
  "discover": ["python3 .claude/tools/pr_vault_archive.py sweep", "/pr_pairing"],
  "sync": ["/pr_sync {pr}"],
  "gate": ["pwsh -NoProfile -File {worktree}/.claude/scripts/regression_gate.ps1"],
  "checklist": ["/pr_test_checklist #{pr}"]
}
```

| Entry | From the old `pr_pipeline.md` |
|---|---|
| `pr_vault_archive.py sweep` | §1a "Archive what left the open set" |
| `/pr_pairing` | §1a's Jmodot pairing: `gh pr list --repo jmund15/Jmodot`, `git ls-tree <head> Jmodot`, the standalone-Jmodot-PR rows, and the `order: <A> before <B>` lines (a paired Jmodot PR merges first). S9 writes it as a new PushinPotions-local command from those paragraphs; it does not exist today |
| `/pr_sync {pr}` | §3b "Long-lived branch sync", and `pr_sync.md` Step 8's gate |
| `regression_gate.ps1` | §3a and `pr_sync.md` Step 8. Shared §5 runs one gate at a time, which is inside `Get-RuntimeSlotCount`'s limit, so `RuntimeSlots.ps1` has no entry. Old runs passed `-Detach`; the cutover session runs one PR through the foreground form first and, if the Bash timeout cuts it, switches to `-Detach` with `/regression_gate`'s watch |
| `/pr_test_checklist #{pr}` | §3a; the command lints its own document (`playtest_checklist_lint.py ... --emit-manifest`, `accounted N/N`), so the lint has no entry of its own |

What stays out of the block: the Jmodot gitlink merge order, the vault board (`Claude/TODO/PR Pipeline.md`) and `PR Reviews/<N>.md` rows, the owner waiver (`--no-review`), the `review_fanout.js` lens plumbing, and `dotnet build` of §2. The shared loop reviews through `/review_pr` and `/review`, so the lens plumbing is gone. The board and `PR Reviews` rows are not pipeline steps; PushinPotions' merge archive keeps its own. A project step the first run misses is an entry added here, not prose in the shared command.

After the pull, delete `.claude/commands/pr_pipeline.md` and `.claude/commands/reference/pr_pipeline_conflicts_brief.md` and `pr_pipeline_reports.md` (the shared command carries its own report). `forked_reconcile.md` row 11 states the open `pp_merge_pr` question.

### `settings.json`

Write it from `hook_registrations.md`: one entry per row, plus PushinPotions' own entries (Godot MCP matchers, `hook_members` through `adaptation.json`). Remove the `compact_directive_anchor` PostToolUse entry and every deleted-hook entry the "Deleted in v2" bullet lists. `permissions.deny` keeps `"Agent"`. `PostToolUse(Workflow)` is new (row 5).

### Callers of the deleted `--setup-runsettings`

`session_context_loader.py` no longer takes `--setup-runsettings`; the Godot step module holds `runsettings_cli`, which has no `__main__`. PushinPotions' callers become

```
python3 -c "import sys; sys.path.insert(0, '.claude/hooks'); import session_context_loader_godot as g; sys.exit(g.runsettings_cli([sys.argv[1]]))" <checkout>
```

The callers: `scripts/TwinCheckout.ps1:37` and the toolchain reference that holds the `.runsettings` prep (`commands/reference/workstation_setup_toolchain.md`, from the v1 `workstation_setup_land_runner.md` step 3). Both print `FIXED` or `OK`, exit 1 when nothing generates.

### Local files and one machine hook

- The four Q4 and F42 files stay local: `tools/vault_sync.project.json`, `user_scope/manifest.local.json`, `commands/checklists/known_failure_modes.md`, `reference/memory_domains.md`. `skills/project_subsystems/adaptation.godot.json` is PushinPotions-local (Q1; deleted from the shared tree).
- `ollama_healthcheck` left the shared user-scope manifest. This machine runs local models: register it as its own user-scope hook if wanted. A machine that installed it keeps the settings entry until it is removed by hand.
- Commands already PushinPotions-local whose deletes ride in the cutover commit: `/model_ingest`, `/rail_battery`, `/routing_battery`.

### Pull refusals

`pull` refuses 13 files by name. Each sits at a path v2 ships, holds different bytes, and is left by the removal recipe: seven are `local` lock rows, and six (the agy files) are in no lock row, so the dry run on lock-listed files in `delete_list.md` §First pull did not see them. The set is every path v2 ships that PushinPotions' `origin/main` (`d9f2cbe1a`) holds with other bytes, less the `tracked` and `forked` lock rows the recipe removes.

S9 deletes all 13 before the pull, so v2's copy stands. Each was settled from both copies and PushinPotions' history since the fork. None needed a port: v2 already carries what each PushinPotions change does, or the change is v1 machinery v2 removed. None is PushinPotions-only, so `delete_list.md` gains no local row.

| File (under `.claude/`) | Evidence |
|---|---|
| `hooks/agy_hook_bridge.py` | PushinPotions' runs every `settings.json` hook of the event, and its recent commits (`a733e027f`, `a5fadfab5`, `298458e6b`) add PostToolUse, SessionStart, Stop and amendment delivery to those hooks. v2 has no shared `settings.json`, so its bridge runs the floor's one entry on `run_command` (`b7f35fc`; plan V7). The agy seat is read-only (`9a21083`), so no edit-side hook has a call to act on. |
| `tests/test_agy_hook_bridge.py` | Proves that settings-driven chain. v2's proves the floor denies a destructive call, allows an ordinary one, denies when the floor is missing, and runs as agy spawns it. |
| `tools/agy_seat.py` | PushinPotions' file is byte-equal to the copy v2 took (`6d98297`, from PushinPotions `60fc0fe9`). v2's adds `fa548be` (a transcript path is judged in the form it was written, on any host) and `e8f92fd`. |
| `tests/test_agy_seat.py` | The same equality; v2's adds the `fa548be` case and pytest collection. |
| `tools/agy_workspace.py` | v2's equals PushinPotions' minus the PostToolUse, PreInvocation, SessionStart and Stop registrations (`3fedcff`: the bridge answers `{}` to them) and the `CLAUDE.coding.md` and `CLAUDE.godot.md` layers (plan T2 folds them into the core and `CLAUDE.md`). The memory-index rule (`7d3e77291`) is in both. |
| `tests/test_agy_workspace.py` | v2's holds each PushinPotions case except the PostToolUse one, which a case proving PreToolUse is the only registered event replaces. |
| `skills/architecture_brainstorm/reference/doc_and_review.md` | PushinPotions' copy (`local`, last edited 2026-11-19) is the detail-layer rewrite whose siblings `forked_reconcile.md` rows 35 and 36 settled for A4's version. It cites `execution_depth.md` and `/design_drive`, which v2 deleted. v2's `SKILL.md` cites the section names v2's file has. Its one addition, the Step 7 architecture quality audit, restates `architecture_philosophy` §Extension shapes and §Authored data is an API, `plan_file_format.md`'s Families and Authored surfaces sections, and the `code_quality.md` rows for the dual-concern knob, the orthogonal-axis rung and the re-authored value. |
| `skills/change_control/SKILL.md` | PushinPotions' copy (`local`, project layer) holds its gate mechanics, which `carried_dropped.md` rules PushinPotions-local. Its three recent commits (`54773b267`, `14acceee6`, `2fc0521ab`) are the M1 unwire, the bench leaving, and census routing; v2's cadence file already states the bench-CI red clause. The mechanics have owners in PushinPotions: `commands/regression_gate.md` and its reference (silent-skip `INVALID`, REUSE, `-RetryOnly`), the `scripts/regression_gate.ps1` header (baseline, stamp trust), and the `tres_nullstrip_guard.py` docstring (the null-strip rationale). See the next list for what S9 moves. |
| `tests/test_prompt_dispatch.py` | PushinPotions' 806 lines prove the v1 dispatcher: a timing record, per-member budgets, threaded capture, and a chain that `54773b267` cut to three members, one of them `sidecar_watch_rearm` (deleted). v2's `_chain` (`fc7d937`) runs members in order behind one fault boundary, and its proof covers order, joining, exit codes, faults, import faults, stdin and the shared chain. |
| `tests/test_runaway_scan_reaper.py` | v2's carries every non-registration case, the Windows-only skip PushinPotions added for the Linux lanes (`e88470cfd`) and the 3.5x spawn bound. The registration and dispatcher-surface cases left with v1's `settings.json` rows and dispatchers. |
| `tests/test_setup_docs.py` | The same four cases; v2's is the pytest form. |
| `tools/usage_report.py` | PushinPotions' 1,021 lines are the v1 report: a weekly-reset window, source classes, chain classes and a `turns` command, over `model_registry` prices. Plan §Capabilities keeps a trimmed report priced from `seats.json`, which v2's 115 lines are; `usage_reset` is deleted above for want of a reader. |
| `tests/test_usage_report.py` | Proves that v1 report; v2's proves the `seats.json` pricing and the session-by-model rows. |

Run the deletions from PushinPotions' checkout root, then the pull:

```
git rm -q .claude/hooks/agy_hook_bridge.py .claude/skills/architecture_brainstorm/reference/doc_and_review.md   .claude/skills/change_control/SKILL.md .claude/tests/test_agy_hook_bridge.py .claude/tests/test_agy_seat.py   .claude/tests/test_agy_workspace.py .claude/tests/test_prompt_dispatch.py .claude/tests/test_runaway_scan_reaper.py   .claude/tests/test_setup_docs.py .claude/tests/test_usage_report.py .claude/tools/agy_seat.py   .claude/tools/agy_workspace.py .claude/tools/usage_report.py
```

`change_control` leaves PushinPotions two edits. Its cadence table rows become `adaptation.json` `gate_cadence` rows, because the shared file reads them from there: `slice` is `.claude/scripts/run_test_suite.ps1` with the targeted `-Filter` of the changed item (after a failure, only the failed tests), plus `FullyQualifiedName~Tests.Logic.Lint` after any `.cs` change; `close` is `.claude/scripts/verify.ps1 -Scope <the drive's domains>`. PushinPotions already has the `main_check` key the third row names. Its `gate_cadence` holds only an object-valued `main` row today, which the "keep only" row above removes.

Derivation: the set of 13 is computed, not read from one pull. Compare each blob under `template/.claude/` with PushinPotions' blob at the same path, and drop a path whose lock row is `tracked` or `forked` and not in a `delete_list.md` local block.

## MaddManager

- **`/review_prs` (batch PR review): replaced by the shared `/pr_pipeline`.** Delete `.claude/commands/review_prs.md` and add this key to `adaptation.json`; MaddManager's gate is its `verify:merge` script (`package.json`), and it has no pairing, sync or checklist step of its own:

  ```json
  "pr_pipeline": {
    "gate": ["npm --prefix {worktree} run verify:merge"]
  }
  ```

  Dropped with `review_prs.md`: its `dotnet build` and `gdunit4` steps (no Godot code in the repo), the 15-agent batching math (the shared loop reviews one PR per `/review_pr`), and the opportunistic rebase between merges (the shared §3 re-syncs after each merge, through `/merge_pr`'s Step 1). Its `drift_scan`, staging and `ui_lock` rules are `gate_cadence` and `ui_lock` matters, not pipeline steps.
- `commands/roadmap_next.md` stays MaddManager-local (A11).
- `adaptation.json`: add `pre_bash` with only the protected rows whose commands MaddManager runs (`git grep` each pattern of `PROJECT_PRE_BASH`; the .NET gate rows are PushinPotions' by T7), `scratch_keep`, and drop `usage_reset` and the dead `gate_cadence` rows as above. Its `convex_prod_target_guard` stays a MaddManager-local hook entry.
- `settings.json`: the same rules as PushinPotions, minus the Godot and C# entries. It has no `.runsettings` caller.
