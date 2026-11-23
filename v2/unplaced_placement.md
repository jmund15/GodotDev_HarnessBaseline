# Unplaced shared files: placement (OF-unplaced, job B13)

Placement record for the files the S4 inventory left unplaced (`harness-v2.inventory.md` §Unplaced files, PushinPotions `origin/main` 44 paths and MaddManager `main` 56 paths). This file replaces those unplaced rows for the 46 paths below as the record of each placement; the inventory keeps its other rows. Paths are relative to `template/.claude/` in the shared repo, which is where v2 keeps the v1 layout (job map §1).

Counts: 46 paths in this job's write set, one decision per path. 17 shared (kept, in this repo). 6 local (moved out of v2; PushinPotions or MaddManager keeps its own copy). 23 delete (listed for the cutover PR). The 46 are the union of both repos' lists after the owner exclusions below. Every PushinPotions path is also in MaddManager's list, so the Listed-in column reads `PP, MM` for those; a path with `MM` alone is in MaddManager's list only.

## Excluded: owned by another S6 job

| Path | Owner | Why it is not here |
|---|---|---|
| `tools/transcript_usage.py` | B9 | Named by B9's write set. |
| `tools/bash_fidelity_probe.py` | B10 | Named by B10's write set. |
| `scripts/validate_commands.py`, `tools/_supervised_launch.py`, `tools/session_timeline.py`, `tools/work_key.py` | C2 | Named by C2's write set. |
| `hooks/_prompt_provenance.py`, `hooks/prompt_git_state_delta.py`, `hooks/running_script_edit_guard.py`, `hooks/vault_mirror_start.py` | B14 | Named by B14's write set. |

## Placements

Reasons state the need: which kept unit names the file, or which dropped or split capability it served. Usage counts are not a reason (E2).

| Path (under `template/.claude/`) | Placement | Listed in | Reason |
|---|---|---|---|
| `gitignore.harness` | shared | PP, MM | Baseline sync writes these ignore lines into every consumer; every repo needs them for harness scratch and logs. |
| `output-styles/terse.md` | shared | PP, MM | Default output style: settings.base.json sets outputStyle to terse, so every repo loads it. |
| `scripts/claude_profile_functions.ps1` | shared | PP, MM | /workstation_setup Phase 4 provisions these shell functions; that command is kept and reshaped. |
| `scripts/defender_exclusions.ps1` | shared | PP, MM | /workstation_setup's Defender step runs it; project-neutral because paths arrive as parameters. |
| `searchignore.harness` | shared | PP, MM | /reindex_search names it; every repo needs the search ignore list. |
| `tests/test_case_pool.py` | shared | PP, MM | Proof of tests/_case_pool.py, the test runner A1 carries (H13); its overlap cases are the cost of proving concurrency. |
| `tests/test_claude_core_standalone.py` | shared | PP, MM | Proves the shared CLAUDE.core.md stands alone; in-process, no spawn. |
| `tests/test_doc_tool_flags.py` | shared | PP, MM | Proves doc examples name only flags their tool parses; in-process. Fails in this lane until B2 and B9 land (see Blocked). |
| `tests/test_hook_call.py` | shared | PP, MM | Proof of tests/_hook_call.py, the in-process hook call T6 relies on; its one spawned case checks the verdict matches a real run. |
| `tests/test_push_command_contract.py` | shared | PP, MM | Checks the kept push and pull commands grant every call their steps make; in-process. |
| `tests/test_python_null_stdin.py` | shared | PP, MM | Proves settings env PYTHON_BASIC_REPL ends a Python run on NUL stdin; one spawn per case, the control waits out BOUND_S by design. |
| `tests/workflow_scripts_parse_test.js` | shared | PP, MM | Every kept workflow script parses in the Workflow runtime form (B3b); no spawn. |
| `tools/index_composition.py` | shared | PP, MM | /reindex_search's drift signal, kept (A8). |
| `tools/roadmap_pos.py` | shared | PP, MM | /update_roadmap computes each Part's Pos from its Deps; kept (A8). |
| `tools/root_strays.py` | shared | PP, MM | /harness_prune's Strays step runs it (plan §Capabilities, kept and trimmed). |
| `tools/subsys.py` | shared | PP, MM | /sync_subsystems and /close read the subsystem catalog; kept (plan §Capabilities). |
| `tools/user_scope.py` | shared | PP, MM | /workstation_setup and environment_bootstrap check and install home-folder files; kept. |
| `scripts/doc_diff_check.py` | local | PP, MM | Asserts a C# edit touched only comments; the PushinPotions C# gate is its only caller. |
| `scripts/doc_warning_check.sh` | local | MM | C# XML-doc build gate; PushinPotions' regression_gate and csharp_patterns name it. |
| `tests/_pwsh_drive.py` | local | MM | PowerShell and Godot proof helper; its importers are PushinPotions' C# and Godot gate tests. |
| `tests/test_pattern_enforcer_own_worktree_part2.py` | local | MM | Shard of the pattern_enforcer proof; pattern_enforcer is a PushinPotions game-code guard (plan §Capabilities). |
| `tests/test_pattern_enforcer_own_worktree_part3.py` | local | MM | Shard of the pattern_enforcer proof, as part2. |
| `tests/test_pattern_enforcer_own_worktree_part4.py` | local | MM | Shard of the pattern_enforcer proof, as part2. |
| `hooks/README.md` | delete | PP, MM | Describes the v1 three-layer hook tree (event, pure, coding, godot sub-hooks); T7 removes the layers. The B14 registration list replaces its one needed fact. |
| `scripts/check_learnings.sh` | delete | PP, MM | Stop-hook helper for autolearn; autolearn is dropped (Q5 O2, A8) and no kept caller remains. |
| `tests/_engine_masks.js` | delete | PP, MM | Prompt masks for the benchmark engine proofs; benchmarking leaves the core (hsr split, V6). No kept importer. |
| `tests/_registry_ids.js` | delete | PP, MM | Reads the model registry's judge ids for the benchmark engine proofs; the registry leaves the core (V6, V7). No kept importer. |
| `tests/_settings_routing.py` | delete | PP, MM | Routing-hook registration check for the prompt proofs; routing batteries dropped (Q15 O1). The B14 registration list replaces the settings probe. |
| `tests/fixtures/daemon_dry_run/dry_run.golden.txt` | delete | MM | Golden output of the sidecar daemon's dry run; the daemon is cut (V7) and no kept test reads it. |
| `tests/fixtures/orchestration_archive_summary/console.txt` | delete | MM | Fixture for the eval archive summary; /eval_dashboard and its tools are dropped (Q15 O1). |
| `tests/lib/measure_common.ps1` | delete | PP, MM | Helper for two semantic-search memory measurements of a change already decided (archived plans). Forward value: re-run from history if the shim's memory model changes. |
| `tests/measure_semantic_search_idle.ps1` | delete | PP, MM | Idle-unload memory measurement of the decided change above; needs a live engine and cold rebuilds, runs in no routine gate. |
| `tests/measure_semantic_search_shared.ps1` | delete | PP, MM | Multi-client memory measurement of the decided change above; same reason as the idle measurement. |
| `tests/test_drive_phase_links.py` | delete | PP, MM | Already absent on v2: A4's commit 77edb9a removed it with the drive commands. Row only. |
| `tests/test_go_roster_20260926.py` | delete | PP, MM | Proves a dated external-model roster; external-model routing leaves the core (V6, V7). |
| `tests/test_instruction_quality_lens_seats.py` | delete | PP, MM | Names the plan-check instruction-quality lens and a session-audit seat, both deleted (A5, V8). The skill split stays proved by test_instruction_quality_split.py. |
| `tests/test_routing_scenarios.py` | delete | MM | Proves the size vocabulary of triage fields; triage.py and rigor levels are deleted (T5, V8). |
| `tools/_job_brief.py` | delete | MM | Restores a durable job's brief and amendment; A2 drops job-brief recovery (amendments are lost, Q3 O2). Its only kept importer, sidecar_fanout.py, is deleted by V7. |
| `tools/codify_prompt.py` | delete | MM | Writes the prompt for an independent /codify derivation; dropped with the derivation subagent (Q5, A8). |
| `tools/explore_result.py` | delete | MM | Consolidates a sidecar lens fan-out; /explore's one Workflow route replaces it (A8, A10). Its importer lens_briefs.py is deleted (V8). |
| `tools/kernel_pool.py` | delete | PP, MM | One-off Windows kernel-pool diagnosis; no kept command or hook names it, and the finding lives in archived gotcha notes. |
| `tools/lf_normalize.py` | delete | PP, MM | Rewrites CRLF working copies; /close drops it (A7) because .gitattributes makes the repo LF. The kill-guard tests use only its name as a fake command line. |
| `tools/load_census.coding.json` | delete | MM | Layer entrypoints for load_census.py, which V11 deletes; S7's budget test replaces its --budgets check. |
| `tools/report_shell.py` | delete | PP, MM | Page shell for the eval and benchmark browser reports; its only importer, eval_render.py, is in the eval split (Q15 O1). |
| `tools/sentence_diff.py` | delete | PP, MM | Sentence comparison for triage.py, citers.py and harness_growth_guard.py; all three are deleted (T5, V8, V11). |
| `tools/session_audit_inputs.py` | delete | PP, MM | Freezes /session_audit's inputs; /session_audit and its lenses are deleted (V8, A5). |

## Deletes for the cutover PR

All 23 `delete` rows above. The cutover PR reads this file for them. Tests that import a deleted file are not in this list: they belong to other owners and are named under "Outside this write set".

## Local moves

The six `local` rows leave v2 in one commit. PushinPotions and MaddManager keep their own copies, so neither repo loses a file. MaddManager has no C# or Godot gate, so its copies of `doc_diff_check.py`, `doc_warning_check.sh` and `_pwsh_drive.py` have no caller there. That repo's owner decides whether to keep them.

## Outside this write set, by owner

Each row is a file this job did not change. Each must change or go before the named file passes; the owner lands the change.

- Tests of deleted tools (fail on this lane until their owner deletes them): `tests/test_kernel_pool.py` (kernel_pool), `tests/test_sentence_diff.py`, `tests/test_codify_prompt.py`, `tests/test_explore_result.py`, `tests/test_session_audit_inputs.py`, `tests/test_session_audit_inputs_contract.py`, `tests/test_lf_normalize.py`, `tests/test_check_learnings.py`, `tests/test_analyze_eval_archive.py` (fixture deleted here), `tests/test_lens_briefs_prepare.py` (explore_result, session_audit_inputs), `tests/test_sidecar_dispatch_context.py` (`_job_brief`). Owner: the job that deletes each tool's kept importer (TB1 for tool tests, C2 for caller cuts).
- Local tests whose helper or subject moved local: `tests/test_CommentOnlyGate.py`, `test_CSharpSource.py`, `test_GodotProcess.py`, `test_IntegrationBatches.py`, `test_PeerActivity.py`, `test_batch_diagnosis.py`, `test_regression_gate.py`, `test_regression_gate_reuse.py`, `test_RunPool.py`, `test_RuntimeSlots.py`, `test_run_test_suite.py`, `test_TreeDigest.py`, `test_TwinCheckout.py`, `test_WideRunGuard.py` (import `_pwsh_drive`); `tests/test_doc_diff_check.py`, `tests/test_doc_warning_check.py`; `tests/test_pattern_enforcer_own_worktree.py` (its `main` asserts parts 2 to 4 exist). Owner: PushinPotions' local test set; these are PushinPotions-local and leave v2 with their subjects.
- Importers of deleted modules that are themselves being deleted: `tools/sidecar_fanout.py` (imports `_job_brief`; V7), `tools/lens_briefs.py` (imports `explore_result`, `session_audit_inputs`; V8), `tools/triage.py` and `tools/citers.py` (import `sentence_diff`; V8), `hooks/harness_growth_guard.py` (imports `sentence_diff`; V11), `tools/eval_render.py` (imports `report_shell`; the eval split). Owner: the V7, V8, V11 and eval-split jobs. Delete each importer before, or in the same wave as, the module it imports.
- Docs that name deleted files: `reference/semantic_search_idle.md` (names `measure_*` and `measure_common`); `reference/sidecar_runs.md` (names `_job_brief` and `session_digest.py --handoff` and `--project-dir`, which the tool lacks). Owner: the doc owners for search and sidecar runs.
- Named only, with no import (text to update when the owner next edits the file): `tools/dispatch_size.py` (docstring names `codify_prompt.py`); `tests/test_instruction_quality_split.py` (docstring names the deleted lens-seats test); `tests/floor/test_floor_kill.py` and `tests/test_kill_guard.py` (use `lf_normalize.py` as a fake process command line, not a file read); the workflow_replay fixtures (journal text naming `report_shell`).
- v1 publish machinery: `tools/gen_manifest.py` and `tools/layer_entries.json` at the lane root name most of the 46 paths by layer. Owner: B16 and S7, which delete the v1 publish machinery.

## Blocked on other jobs (this lane)

- `tests/test_doc_tool_flags.py` fails at 065798f before this job's commits: `tools/capacity.py` (B2) and `tools/usage_report.py` (B9) are absent, and `reference/sidecar_runs.md` names two `session_digest.py` flags the tool lacks. Its failure is the proof working. It passes when B2, B9 and the `sidecar_runs.md` owner land. This job changed no file it reads.

## Choices (where the plan or inventory left a call to this job)

- **Local over delete for C# and PowerShell gate helpers.** `doc_diff_check.py`, `doc_warning_check.sh` and `_pwsh_drive.py` have one caller each, in PushinPotions' gate. A project-owned file keeps its place with its caller. Moving it out of the shared repo is the T7 rule for Godot and C# files (K6).
- **Local for the pattern_enforcer shards.** The shards split one proof round-robin across four processes to fit a wall-clock budget. Dropping them from the shared repo leaves the parent proof and its shards with the project that owns the hook. PushinPotions' owner should cut the sharding at its cause (T6: in-process plus one spawn per entry point), not here.
- **Delete for the semantic-search measurements.** They measure a memory change already decided and recorded in archived plans. They need a live engine and cold rebuilds and run in no routine gate. Forward value: the measurement can be re-run from git history if the shim's memory model changes.
- **Delete for `kernel_pool.py`.** It is a one-off Windows diagnosis. No kept unit names it, and the finding is in an archived gotcha note.
- **Keep the six kept tests as they are.** Each already meets T6: four in-process, one with a single spawn case, the null-stdin proof with one spawn per case. The seven-second case-pool proof is its concurrency evidence. The null-stdin control waits out its 8 s bound on purpose, since the control must show the process still running. No conversion was needed, so no test was rewritten.
- **`hooks/README.md` deleted, not rewritten.** Its one needed fact (which hook files are live) is the registration list B14 writes to `/v2/hook_registrations.md`. A rewrite would restate the layers T7 removes.
