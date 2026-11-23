# Delete list: the cutover PR's deletes from the shared tree

**v2 shared set: 459 files** under `template/.claude/` (`git ls-files template/.claude | wc -l` at the B16 tip). B16's done-condition holds when that command prints this number.

Written by TB3 (the first table) and B16 (the rest). B16 deleted every path below from the template; the cutover PR deletes the same paths from each consumer repo that holds them, unless the path is PushinPotions- or MaddManager-local, in which case S9 places it in that repo (the **local** list).

Base for every row: `v2` at `6f21a0d`. Class and reason per group; the S4 class table is stale for the files named under "Kept".

## Deleted by B16, by reason

| Group | Count | Reason | Replacement or need |
|---|---|---|---|
| Local, project-owned and bench-moved files | 421 | Each repo owns its own copy (T7); Godot and C# game files are PushinPotions-local (K6, Q1); `auto-memory/` is V14 local; benchmarking moves to HarnessBench (V6) | S9 places each local file in its repo |
| V7 external-provider dispatch and its tests | see list | Replaced by `seat.sh`, `seats.json`, `capacity.py` and the login pair (Q3 O2) | `scripts/seat*.sh`, `tools/launch_check.py` |
| V8 review ledger family | see list | `/review` and `quote_check.py` replace it | `commands/review.md` |
| V9 proof runner (`scripts/harness_tests.py` and 40 of its tests) | see list | pytest and the shared CI replace it (T6) | `pytest -n auto` |
| V10 baseline sync and publish tools | see list | `harness_sync.py` replaces them (T7); S7 deletes the repo-root half | `tools/harness_sync.py` |
| V11 census, retirement, routing and advisory hooks | see list | They police doctrine volume v2 caps by budget | S7's budget test |
| Q15 O1: `/self_evaluate`, `/eval_dashboard`, rail and routing batteries | see list | Nothing in v2 reads what they chart | none |
| `hooks/git_guardrails.py` and 11 tests | 1 + 11 | The floor (`dangerous_shell_guard.py`, `kill_guard.py`) carries every loss deny; the advisories are V1's lost column | `tests/floor/` |
| The ten v1 guard files | 10 | `guards/rails.md` replaces them (A3's hand-off) | `guards/rails.md` |
| `schemas/review_findings.json`, orphaned fixtures and helpers | see list | No reader remains | none |

## Kept although S4 classed them delete or local

| File | Why kept |
|---|---|
| `tools/cli_usage.py` | Q10 O1: 34 kept tools import it |
| `hooks/foreground_wait_guard.py` and its test | Q9 O1: the wait backstop `seat.sh` arms; `run_call_router.py` imports `never_woken` |
| `tools/session_digest.py`, `commands/session_digest.md`, `tools/usage_report.py`, `tools/guard_denials.py`, `commands/{commit_push,update_roadmap,harness_prune,overnight_review}.md`, `commands/reference/update_roadmap_*.md`, `commands/agents/redteam_agents.md`, `hooks/{unbounded_scan_guard,bash_backslash_fidelity,semantic_search_scope_guard}.py` | Q2: the plan keeps each by name |
| `scripts/engine_src/common.js` | `scripts/schema_parity.js` builds the engines from it |
| `tools/tree-sitter-c_sharp.{wasm,LICENSE,provenance.json}` | `semantic_search_idle_install.py` copies them to the home install |
| `tools/reap.py` | T1's small stop tool, rewritten in place by S5d; `kill_guard.py` calls `reap.process_table`. S5d deleted `tests/test_reap.py`, `test_reap_native_env.py` and `floor/test_floor_carried.py`: their kept cases are `floor/test_floor_reap.py`'s rows (`floor/test_floor_ported.md`) |

## Rows added by B16 to TB3's table

| File dropped (`template/.claude/tests/`) | Subject at base | Why dropped | Need or replacement |
|---|---|---|---|
| `test_scan_fixtures.py` | `baseline_identity.scan_tree` and `_scan_fixtures.py` | the whole-tree scan case's subject is V10; the secret-shape case moved | `test_user_scope.py::test_secret_fixtures_match_their_lint_shapes` |
| `test_run_call_router.py` | `run_call_router.py` driven through `pre_bash_dispatch.dispatch` | the dispatcher is deleted; `run_call_router.py` has no caller on `v2` until its rewrite (plan V9) | none yet: the 59 cases need a rewrite against the file's own entry |
| `test_bash_backslash_fidelity.py` | `hooks/bash_backslash_fidelity.py` through settings composition | it composed `settings.json` from the deleted layers | `tests/floor/test_floor_entry.py` backslash-repair case only; the class rows are not carried (S5c) |
| `test_python_null_stdin.py`, `test_settings_composed.py`, `test_prose_adaptation_seams.py`, `test_claude_core_standalone.py` | the template's composed `settings.json`, `adaptation.json` and `CLAUDE.md` | those files are each repo's own (T7) | run in each repo by S9 if wanted |
| `test_compaction_state.py`, `test_hook_state_compaction_keep.py` | `_hook_state.skill_in_context` | the skill-in-context marker and its only reader are deleted | none |
| `test_codify_prompt.py`, `test_doc_diff_check.py`, `test_doc_warning_check.py`, `test_kernel_pool.py`, `test_lf_normalize.py` | tools absent from the shared tree | their subjects are MaddManager- or PushinPotions-only | each repo |
| `test_session_digest*.py` (five) | v1 `session_digest.py` internals | B8 rewrote the tool; `test_session_digest_cli.py` is its proof | `test_session_digest_cli.py` |
| `lean_type_fallback_test.js` | the engines' verifier fallback | B15 removed the verification stub | none |

## Local files for S9 (leave the shared tree; each repo keeps its own)

`auto-memory/`: all 175 files (V14 local).

### Local files other than auto-memory (246)

```
CLAUDE.godot.md
CLAUDE.md
cloud-setup.sh
commands/agents/pr_test_checklist_conventions.md
commands/agents/structure_audit_agents.md
commands/analyze_godot_logs.md
commands/audit_test_accessors.md
commands/checklists/code_quality_godot.md
commands/checklists/test_quality_godot.md
commands/pr_test_checklist.md
commands/reference/create_pr_jmodot.md
commands/reference/pr_ready_boot_correlation.md
commands/reference/regression_gate_reference.md
commands/reference/workstation_setup_toolchain.md
commands/regression_gate.md
commands/structure_audit.md
commands/test_compact.md
githooks/pre-commit
githooks/pre-merge-commit
guards/author.godot.md
guards/bench-score.md
guards/common.godot.md
guards/survey.godot.md
hooks/_cs_files.py
hooks/_wide_runs.py
hooks/analyze_godot_logs.py
hooks/apply_blanket_tool.py
hooks/check_logger_tag_prefix.py
hooks/cloud_test_enforcer.py
hooks/duplicate_test_double_baseline.json
hooks/duplicate_test_double_guard.py
hooks/editor_live_build_guard.py
hooks/generate_family_manifest.py
hooks/godot_source_pins.py
hooks/merge_build_check.py
hooks/pattern_enforcer.py
hooks/prototype_containment_guard.py
hooks/provisional_totality_guard.py
hooks/refcounted_free_guard.py
hooks/runtime_mcp_worktree_guard.py
hooks/session_context_loader_godot.py
hooks/test_suite_gate_coverage_guard.py
hooks/tool_cascade_audit.py
hooks/tres_format_guard.py
hooks/tres_nullstrip_guard.py
hooks/tres_script_strip_guard.py
hooks/uid_cache_audit.py
prototype_registry.md
reference/godot_class_index.md
reference/project_stack.md
reference/rules/csharp_patterns_examples.md
reference/rules/jmodot_utilities_examples.md
reference/scene_construction.md
reference/source_trust_godot.md
reference/vault_taxonomy.md
rules/cloud_dev.md
rules/csharp_patterns.md
rules/godot_files.md
rules/hsm_bt_patterns.md
rules/jmodot_framework_authoring.md
rules/jmodot_submodule.md
rules/jmodot_utilities.md
rules/physics_patterns.md
rules/scene_authoring.md
rules/test_authoring.md
rules/visual_layers.md
scripts/CSharpSource.ps1
scripts/CommentOnlyGate.ps1
scripts/EngineErrors.ps1
scripts/GodotProcess.ps1
scripts/IntegrationBatches.ps1
scripts/PeerActivity.ps1
scripts/RunPool.ps1
scripts/RuntimeSlots.ps1
scripts/StaticGuards.ps1
scripts/TreeDigest.ps1
scripts/TwinCheckout.ps1
scripts/WideRunGuard.ps1
scripts/batch_diagnosis.ps1
scripts/engine_userdata.py
scripts/fetch_source.d/godot_docs.sh
scripts/fetch_source.sh
scripts/freesound_fetch.py
scripts/freesound_search.py
scripts/godot_bin.sh
scripts/godot_docs_cache.sh
scripts/land_run.ps1
scripts/main_check.py
scripts/regression_gate.ps1
scripts/run_integration_batched.ps1
scripts/run_test_suite.ps1
scripts/sdf_suite/campaigns/opencode-free.env
scripts/sdf_suite/envelope_foot.txt
scripts/sdf_suite/envelope_head.txt
scripts/sdf_suite/preflight_opencode.py
scripts/sdf_suite/proposed/generate_breakdown.SDF_RESULT_FILES.patch
scripts/verify.ps1
settings.base.json
settings.coding.json
settings.godot.json
settings.project.json
skills/benchmark_design/SKILL.md
skills/benchmark_design/reference/axis_design.md
skills/benchmark_design/reference/board_publication.md
skills/benchmark_design/reference/calibration_and_replication.md
skills/benchmark_design/reference/configuration_and_transport.md
skills/benchmark_design/reference/configurations_and_outcomes.md
skills/benchmark_design/reference/controls_and_subfloor.md
skills/benchmark_design/reference/grid_health.md
skills/benchmark_design/reference/scoring.md
skills/benchmark_design/reference/staircase_construction.md
skills/benchmark_design/reference/validation_probes.md
skills/diagnostics_toolkit/scripts/classify_godot_log.ps1
skills/environment_bootstrap/SKILL.md
skills/game_vision/SKILL.md
skills/jmodot/SKILL.md
skills/jmodot/ai.md
skills/jmodot/combat.md
skills/jmodot/components.md
skills/jmodot/movement.md
skills/jmodot/squad_formations.md
skills/jmodot/stat_driven_ai.md
skills/jmodot/stats.md
skills/logging_methodology/SKILL.md
skills/merge_conflicts/SKILL.md
skills/parameterized_asset_pipeline/SKILL.md
skills/parameterized_asset_pipeline/references/provenance.md
skills/parameterized_asset_pipeline/references/style_spec.md
skills/parameterized_asset_pipeline/templates/ThemeBuilder.cs
skills/parameterized_asset_pipeline/templates/ThemeManifest.cs
skills/parameterized_asset_pipeline/templates/conform_external.py
skills/parameterized_asset_pipeline/templates/contact_sheet.py
skills/parameterized_asset_pipeline/templates/icons.py
skills/parameterized_asset_pipeline/templates/lint_core.py
skills/parameterized_asset_pipeline/templates/palette.py
skills/parameterized_asset_pipeline/templates/theme_gen.py
skills/project_subsystems/SKILL.md
skills/project_subsystems/adaptation.json
skills/prototype/SKILL.md
skills/prototype/reference/probe_verification.md
skills/refactor_procedure/SKILL.md
skills/shader_authoring/SKILL.md
skills/sprite_authoring/SKILL.md
skills/status_effect_authoring/SKILL.md
skills/vfx_patterns/SKILL.md
skills/vfx_patterns/reference/glow_and_particles.md
skills/vfx_patterns/reference/interfaces_and_files.md
tests/_commit_guard_cases.py
tests/_merge_guard_cases.py
tests/agent_fixtures/linux_proc/dotnet_test_capture.json
tests/csharp-ls-adapter_contract_test.js
tests/test_CSharpSource.py
tests/test_CommentOnlyGate.py
tests/test_EngineErrors.ps1
tests/test_GodotProcess.py
tests/test_IntegrationBatches.py
tests/test_PeerActivity.py
tests/test_RunPool.py
tests/test_RuntimeSlots.py
tests/test_StaticGuards.py
tests/test_TreeDigest.py
tests/test_TwinCheckout.py
tests/test_WideRunGuard.py
tests/test_analyze_godot_logs.py
tests/test_apply_blanket_tool_contract.py
tests/test_batch_diagnosis.py
tests/test_check_logger_tag_prefix.py
tests/test_cloud_test_enforcer.py
tests/test_conformance_gate.ps1
tests/test_cs_files.py
tests/test_duplicate_test_double_guard.py
tests/test_duplicate_test_double_guard_contract.py
tests/test_editor_live_build_guard.py
tests/test_engine_userdata_contract.py
tests/test_fetch_source.py
tests/test_freesound_fetch.py
tests/test_freesound_search.py
tests/test_gen_godot_class_index.py
tests/test_generate_family_manifest.py
tests/test_generate_family_manifest_contract.py
tests/test_godot_bin_contract.py
tests/test_godot_docs_cache_contract.py
tests/test_godot_process_nested_worktree.ps1
tests/test_godot_source_pins.py
tests/test_harness_policy_contracts.py
tests/test_land_run.py
tests/test_land_run_part2.py
tests/test_land_run_part3.py
tests/test_main_check.py
tests/test_merge_build_check.py
tests/test_pattern_enforcer_autofix.py
tests/test_pattern_enforcer_logging_scope.py
tests/test_pattern_enforcer_tool_cascade.py
tests/test_pr_playtest_source_contract.py
tests/test_prototype_containment_guard_commit_detection.py
tests/test_provisional_totality_guard_commit_detection.py
tests/test_reach_run.py
tests/test_refcounted_free_guard_adaptation.py
tests/test_regression_gate.py
tests/test_regression_gate_reuse.py
tests/test_regression_gate_verdicts.ps1
tests/test_run_integration_batched_adaptation.ps1
tests/test_run_pool.ps1
tests/test_run_test_suite.py
tests/test_run_test_suite_userdir.py
tests/test_runtime_mcp_worktree_guard.py
tests/test_runtime_slots.ps1
tests/test_session_context_loader_godot.py
tests/test_session_context_loader_health.py
tests/test_setup-csharp-ls_contract.py
tests/test_standalone_recompile.py
tests/test_startup_integrity.py
tests/test_suite_watchdog.py
tests/test_test_profile.py
tests/test_test_profile_contract.py
tests/test_test_suite_gate_coverage_guard.py
tests/test_test_suite_gate_coverage_guard_contract.py
tests/test_test_tools_target.py
tests/test_tool_cascade_audit_contract.py
tests/test_tool_cascade_audit_walk.py
tests/test_tres_format_guard.py
tests/test_tres_format_worktree.py
tests/test_tres_nullstrip_guard_commit_detection.py
tests/test_tres_nullstrip_guard_output.py
tests/test_tres_script_strip_guard_commit_detection.py
tests/test_tres_script_strip_guard_output.py
tests/test_tres_script_strip_guard_trigger.py
tests/test_type_decls.py
tests/test_uid_cache_audit.py
tests/test_verify_retry_only.py
tests/test_verify_static_guards.py
tests/test_void_check_engagement.py
tests/test_workflow_templates.py
tests/test_workstation_profile_godot.py
tools/csharp-ls-adapter.js
tools/gen_godot_class_index.py
tools/godot_mcp_launch.cjs
tools/load_census.godot.json
tools/reach_run.py
tools/setup-csharp-ls.sh
tools/test_profile.py
tools/type_decls.py
tools/void_check.py
tools/workstation_profile_godot.py
workflows/effort_on_agenttype_probe.js
worklog-titles.md
```

## Every other deleted path (V7, V8, V9, V10, V11, Q15, floor-absorbed, replaced guards, dead tests)

### commands/ (1)

```
commands/agents/review_agents_godot.md
```

### guards/ (7)

```
guards/author.coding.md
guards/author.md
guards/common.md
guards/gate.md
guards/review.md
guards/survey.coding.md
guards/survey.md
```

### hooks/ (45)

```
hooks/activity_registry.py
hooks/amend_delivery.py
hooks/approving_gates.py
hooks/baseline_classification_guard.py
hooks/budget_posture.py
hooks/composed_output_guard.py
hooks/critical_analysis_reminder.py
hooks/denial_coach.py
hooks/design_surface_reminder.py
hooks/dispatch_mechanism_guard.py
hooks/dispatch_table.py
hooks/duplicate_commit_check.py
hooks/gate_cadence_guard.py
hooks/git_guardrails.py
hooks/hand_pricing_advisory.py
hooks/harness_edit_skill_reminder.py
hooks/harness_growth_guard.py
hooks/harness_shell_write_guard.py
hooks/log_instruction_loads.py
hooks/memory_hits_logger.py
hooks/model_ladder_gate.py
hooks/model_name_exemptions.json
hooks/model_name_guard.py
hooks/plan_job_map_guard.py
hooks/plan_memory_reminder.py
hooks/pre_bash_dispatch.py
hooks/provenance_label_guard.py
hooks/rail_probe_guard.py
hooks/readonly_lens_write_guard.py
hooks/readonly_marker_arm.py
hooks/retire_trigger_advisory.py
hooks/routing_audit.py
hooks/routing_classifier.py
hooks/rule_number_guard.py
hooks/self_eval_archive_guard.py
hooks/session_model_rails.py
hooks/shell_census.py
hooks/sidecar_dispatch_context.py
hooks/sidecar_watch_rearm.py
hooks/skill_load_marker.py
hooks/task_brief_facts.py
hooks/tool_routing_nudge.py
hooks/triage_commit_check.py
hooks/wide_test_guard.py
hooks/workflow_provider_guard.py
```

### reference/ (8)

```
reference/codex_session_runbook.md
reference/external_models.json
reference/known_failure_modes_entries.md
reference/model_ladder_evidence.md
reference/opencode_run_preamble.md
reference/reproduce_review_dedup.cjs
reference/sidecar_dispatch.md
reference/sidecar_runs.md
```

### schemas/ (1)

```
schemas/review_findings.json
```

### scripts/ (20)

```
scripts/_read_recorder/node_recorder.js
scripts/_read_recorder/sitecustomize.py
scripts/anthropic_console_secondary_sidecar.sh
scripts/anthropic_console_sidecar.sh
scripts/anthropic_secondary_sidecar.sh
scripts/anthropic_sidecar.sh
scripts/autolearn.sh
scripts/codex_proxy_sidecar.sh
scripts/deepseek_sidecar.sh
scripts/harness_tests.py
scripts/lib/below_normal_shell.sh
scripts/lib/ccp_probe.py
scripts/lib/cli_envelope.py
scripts/lib/oc_proxy_probe.py
scripts/lib/sidecar_common.sh
scripts/lib/text_only_shim.py
scripts/opencode_refresh_models.py
scripts/opencode_sidecar.sh
scripts/rail_battery/probes.json
scripts/worklog_relevance_sidecar.sh
```

### skills/ (1)

```
skills/project_subsystems/adaptation.coding.json
```

### tests/ (408)

```
tests/_citer_boundary_cases.py
tests/_display_words.py
tests/_git_bash.js
tests/_harness_repo_case.py
tests/_ledger_rows.py
tests/_mcp_plant.py
tests/_runner_fixture.py
tests/_settings_probe.py
tests/_sidecar_launch.py
tests/_transport_fixture.py
tests/_walk_files.py
tests/agent_fixtures/smoke/sa_design_semantics.md
tests/agent_fixtures/smoke/sa_intuitiveness_testability.md
tests/agent_fixtures/smoke/sa_robustness_performance.md
tests/codify_prompt_fixture/5a10d094_owner_rows.jsonl
tests/dispatch_chains_transport_test.js
tests/dispatch_spill_root_test.js
tests/fixtures/analyze_eval_archive/console.txt
tests/fixtures/analyze_eval_archive/stats.json
tests/fixtures/compact_bloat/rows.jsonl
tests/fixtures/dispatch_kind_init_events.json
tests/fixtures/dispatch_m6_jobs.json
tests/fixtures/gate_trend/gate_run_20261026-162451-95356.out
tests/fixtures/gate_trend/gate_run_20261101-232503-25176.out
tests/fixtures/gate_trend/gate_run_20261102-040801-45292.out
tests/fixtures/gate_trend/gate_run_20261111-070510-38248.out
tests/fixtures/gate_trend/gate_run_20261111-084808-37220.out
tests/fixtures/gate_trend/gate_run_20261111-102846-1416.out
tests/fixtures/go_usage/glm_go_result.json
tests/fixtures/model_ladder_gate_bash_result.json
tests/fixtures/om_failed_retry/subagents/workflows/wf_3193d0a9-1e4/journal.jsonl
tests/fixtures/om_failed_retry/workflows/wf_3193d0a9-1e4.json
tests/fixtures/plan_check_doctrine_consistency_replay.spill.md
tests/fixtures/review_gap_only_seat_return.json
tests/fixtures/sidecar_journal/failed_launch/failed.record.json
tests/fixtures/sidecar_journal/failed_launch/failed.record.json.exit
tests/fixtures/sidecar_journal/failed_launch/failed.record.json.journal.jsonl
tests/fixtures/sidecar_journal/failed_launch/failed.record.json.pid
tests/fixtures/sidecar_journal/foreground_launch/journaled.record.json
tests/fixtures/sidecar_journal/foreground_launch/journaled.record.json.exit
tests/fixtures/sidecar_journal/foreground_launch/journaled.record.json.out
tests/fixtures/sidecar_journal/foreground_launch/journaled.record.json.pid
tests/fixtures/stop_audit/review_fold_native/SOURCE.txt
tests/fixtures/stop_audit/review_fold_native/in_run_report.json
tests/fixtures/stop_audit/review_fold_native/result.json
tests/fixtures/stop_audit/review_foldless/SOURCE.txt
tests/fixtures/stop_audit/review_foldless/in_run_report.json
tests/fixtures/stop_audit/review_foldless/journal.jsonl
tests/fixtures/stop_audit/review_foldless/result.json
tests/fixtures/stop_audit/worklog_relevance/SOURCE.txt
tests/fixtures/stop_audit/worklog_relevance/in_run_report.json
tests/fixtures/stop_audit/worklog_relevance/journal.jsonl
tests/fixtures/stop_audit/worklog_relevance/result.json
tests/fixtures/v1_check_golden.json
tests/fold_routing_vectors.json
tests/lean_type_fallback_test.js
tests/linux_preview_green.fixture.log
tests/linux_preview_red.fixture.log
tests/read_coverage_corpus.json
tests/review_fanout_lossless_test.js
tests/review_fanout_mandate_map_schema_test.js
tests/review_fanout_read_coverage_test.js
tests/review_fanout_schema_parity_test.js
tests/review_fanout_spill_test.js
tests/sidecar_resume_loop_test.sh
tests/test_actionable_advisories.py
tests/test_activity_registry_cadence.py
tests/test_agent_progress.py
tests/test_aggregate_routing_audit.py
tests/test_ai_worker_metrics.py
tests/test_amend_delivery.py
tests/test_analyze_eval_archive.py
tests/test_anthropic_console_credentials.sh
tests/test_anthropic_console_secondary_sidecar.py
tests/test_anthropic_console_sidecar.py
tests/test_anthropic_secondary_sidecar.py
tests/test_anthropic_sidecar.py
tests/test_approving_gates.py
tests/test_autolearn_outcomes.py
tests/test_baseline_classification_guard.py
tests/test_baseline_compose.py
tests/test_baseline_git_longpaths.py
tests/test_baseline_identity.py
tests/test_baseline_linux_preview.py
tests/test_baseline_publish.py
tests/test_baseline_publish_part2.py
tests/test_baseline_sync.py
tests/test_baseline_sync_doctor.py
tests/test_baseline_sync_part2.py
tests/test_baseline_sync_part3.py
tests/test_bash_backslash_fidelity.py
tests/test_battery_children.py
tests/test_below_normal_shell.py
tests/test_budget_posture_cadence.py
tests/test_budget_posture_dispatch_cache.py
tests/test_budget_posture_rating_debt.py
tests/test_budget_posture_transport.py
tests/test_ccp_probe_continuation.py
tests/test_ccp_probe_identity.py
tests/test_citer_coverage.py
tests/test_claude_core_standalone.py
tests/test_claudemd_citations.py
tests/test_cli_envelope.py
tests/test_codex_child_timeout.py
tests/test_codex_proxy_sidecar.py
tests/test_codify_prompt.py
tests/test_compaction_state.py
tests/test_composed_output_guard.py
tests/test_conformance_j1.py
tests/test_conformance_j10.py
tests/test_conformance_j3.py
tests/test_conformance_j4.py
tests/test_conformance_j6.py
tests/test_conformance_j7.py
tests/test_critical_analysis_reminder.py
tests/test_deepseek_sidecar.py
tests/test_delegate_prepare.py
tests/test_denial_coach.py
tests/test_design_surface_reminder.py
tests/test_dispatch_lifecycle_replay.py
tests/test_dispatch_mechanism_guard.py
tests/test_dispatch_size.py
tests/test_dispatch_table.py
tests/test_dispatch_table_part2.py
tests/test_dispatch_table_part3.py
tests/test_dispatch_table_verification.py
tests/test_dispatch_take.py
tests/test_doc_diff_check.py
tests/test_doc_warning_check.py
tests/test_duplicate_commit_check.py
tests/test_duplicate_commit_check_project.py
tests/test_escape_log.py
tests/test_eval_dashboard.py
tests/test_eval_findings.py
tests/test_eval_history.py
tests/test_eval_render.py
tests/test_exhausted_marker.py
tests/test_explore_result.py
tests/test_extract_numbers.py
tests/test_extract_subagent_tools.py
tests/test_fanout_needs.py
tests/test_finding_bar.py
tests/test_finding_ids.py
tests/test_fold_check.py
tests/test_fold_check_post_check_modes.py
tests/test_fold_check_reuse.py
tests/test_fold_check_subject_path.py
tests/test_fold_check_submodule.py
tests/test_fresh_consumer.py
tests/test_gate_cadence_guard.py
tests/test_gate_growth.py
tests/test_gate_growth_recovery.py
tests/test_gate_trend.py
tests/test_git_commit_msg_hook.py
tests/test_git_guardrails_advice.py
tests/test_git_guardrails_amend.py
tests/test_git_guardrails_checkout_own_worktree.py
tests/test_git_guardrails_harness_commit.py
tests/test_git_guardrails_owner_branch_delete.py
tests/test_git_guardrails_owner_discard.py
tests/test_git_guardrails_revert_lane.py
tests/test_git_guardrails_scratch_branch_delete.py
tests/test_go_plan_cap_display.py
tests/test_guard_text.py
tests/test_hand_pricing_advisory.py
tests/test_harness_edit_skill_reminder.py
tests/test_harness_edit_skill_reminder_doctrine.py
tests/test_harness_growth_guard.py
tests/test_harness_shell_write_guard.py
tests/test_harness_tests_adaptation.py
tests/test_harness_tests_admission.py
tests/test_harness_tests_cannot_run.py
tests/test_harness_tests_case_reach.py
tests/test_harness_tests_checkout_reap.py
tests/test_harness_tests_durations.py
tests/test_harness_tests_gate_time.py
tests/test_harness_tests_growth.py
tests/test_harness_tests_integrity.py
tests/test_harness_tests_latency.py
tests/test_harness_tests_name_binding.py
tests/test_harness_tests_nested_recorder.py
tests/test_harness_tests_part_binding.py
tests/test_harness_tests_pass_cache.py
tests/test_harness_tests_pass_cache_part2.py
tests/test_harness_tests_pass_cache_part3.py
tests/test_harness_tests_postprocess.py
tests/test_harness_tests_proof_cwd.py
tests/test_harness_tests_proofs_for.py
tests/test_harness_tests_red_at_head.py
tests/test_harness_tests_runner.py
tests/test_harness_tests_runner_env.py
tests/test_harness_tests_runner_key.py
tests/test_harness_tests_runner_part2.py
tests/test_harness_tests_runner_part3.py
tests/test_harness_tests_runner_part4.py
tests/test_harness_tests_runner_part5.py
tests/test_harness_tests_selection_reach.py
tests/test_harness_tests_temp_sweep.py
tests/test_harness_tests_write_exhaust.py
tests/test_hook_state_compaction_keep.py
tests/test_kernel_pool.py
tests/test_ladder_ingest.py
tests/test_ladder_ingest_sources.py
tests/test_ladder_prose_check.py
tests/test_layer_closure.py
tests/test_layer_closure_citations.py
tests/test_layer_closure_registration.py
tests/test_lens_briefs.py
tests/test_lens_briefs_job_map.py
tests/test_lens_briefs_later_round.py
tests/test_lens_briefs_prepare.py
tests/test_lf_normalize.py
tests/test_load_census.py
tests/test_lock_merge.py
tests/test_log_instruction_loads.py
tests/test_lookup_shape_cites.py
tests/test_mechanism_census.py
tests/test_merge_commit_scope.py
tests/test_model_context_windows.py
tests/test_model_ladder_gate.py
tests/test_model_pricing.py
tests/test_model_registry.py
tests/test_model_registry_capacity.py
tests/test_model_registry_for_role.py
tests/test_model_registry_ladder_slice.py
tests/test_model_registry_plans.py
tests/test_model_registry_rails.py
tests/test_model_registry_workflow_agents.py
tests/test_oc_proxy_probe.py
tests/test_ollama_contention_probe.py
tests/test_opencode_proxy_start.sh
tests/test_opencode_refresh_go.py
tests/test_opencode_refresh_models.py
tests/test_opencode_sidecar_check.sh
tests/test_opencode_sidecar_launch_id.sh
tests/test_opencode_sidecar_shipped.py
tests/test_pattern_enforcer_own_worktree.py
tests/test_plan_job_map_guard.py
tests/test_plan_memory_reminder_adaptation.py
tests/test_post_check.py
tests/test_post_check_bar.py
tests/test_post_check_engine_adapters.py
tests/test_post_check_explore_label_prefix.py
tests/test_post_check_out_of_subject.py
tests/test_post_check_part2.py
tests/test_post_check_part3.py
tests/test_post_check_part4.py
tests/test_post_check_record_blobs.py
tests/test_post_check_reuse.py
tests/test_post_check_seat_attribution.py
tests/test_post_check_subject_root.py
tests/test_pre_bash_dispatch.py
tests/test_pre_bash_dispatch_part2.py
tests/test_pre_bash_dispatch_part3.py
tests/test_pre_bash_dispatch_part4.py
tests/test_pre_bash_dispatch_reaper_deny.py
tests/test_prose_adaptation_seams.py
tests/test_provenance_label_guard.py
tests/test_provider_bands_cache.py
tests/test_provider_bands_pick.py
tests/test_provider_capacity.py
tests/test_python_null_stdin.py
tests/test_quota_bands.py
tests/test_rail_probe_guard.py
tests/test_readonly_lens_write_guard.py
tests/test_readonly_marker_arm.py
tests/test_restate_scan_contract.py
tests/test_retire_trigger_advisory.py
tests/test_review_calibration.py
tests/test_review_launch_validation.py
tests/test_review_ledger.py
tests/test_review_ledger_bar.py
tests/test_review_ledger_blobs.py
tests/test_review_ledger_roster.py
tests/test_review_result.py
tests/test_review_result_defer.py
tests/test_review_result_submodule_blobs.py
tests/test_review_rounds_replay.py
tests/test_review_schema_strict.py
tests/test_routing_audit_subagent_exempt.py
tests/test_routing_classifier.py
tests/test_routing_edit_anchor.py
tests/test_rule_number_guard.py
tests/test_rule_paths_match_stamp.py
tests/test_rule_retirement.py
tests/test_run_call_router.py
tests/test_scaled_advisory_facts.py
tests/test_scan_fixtures.py
tests/test_score_routing_battery.py
tests/test_self_eval_archive_guard.py
tests/test_self_eval_archive_store.py
tests/test_sentence_diff.py
tests/test_session_audit_inputs.py
tests/test_session_audit_inputs_contract.py
tests/test_session_audit_premises.py
tests/test_session_digest.py
tests/test_session_digest_agent_work.py
tests/test_session_digest_bounds.py
tests/test_session_digest_question_answers.py
tests/test_session_digest_workflow_results.py
tests/test_session_model_rails_rails.py
tests/test_settings_composed.py
tests/test_settings_probe.py
tests/test_shell_census.py
tests/test_sidecar_argv.py
tests/test_sidecar_balance_retry.sh
tests/test_sidecar_band_exhausted.sh
tests/test_sidecar_capacity_gate.sh
tests/test_sidecar_common_adaptation.py
tests/test_sidecar_common_amend.py
tests/test_sidecar_common_registration.py
tests/test_sidecar_common_start.py
tests/test_sidecar_completion.py
tests/test_sidecar_detach.sh
tests/test_sidecar_disclosure_default.sh
tests/test_sidecar_dispatch_context.py
tests/test_sidecar_dispatch_context_part2.py
tests/test_sidecar_dispatch_context_part3.py
tests/test_sidecar_dispatch_context_part4.py
tests/test_sidecar_drift_messages.py
tests/test_sidecar_durable_files.sh
tests/test_sidecar_fanout.py
tests/test_sidecar_fanout_admission.py
tests/test_sidecar_fanout_chains.py
tests/test_sidecar_fanout_completion.py
tests/test_sidecar_fanout_detach.py
tests/test_sidecar_fanout_detach_admission.py
tests/test_sidecar_fanout_fallback.py
tests/test_sidecar_fanout_hold.py
tests/test_sidecar_fanout_launcher_refusal.py
tests/test_sidecar_fanout_load.py
tests/test_sidecar_fanout_native_split.py
tests/test_sidecar_fanout_outdir.py
tests/test_sidecar_fanout_parity.py
tests/test_sidecar_fanout_pick_drain.py
tests/test_sidecar_fanout_records.py
tests/test_sidecar_fanout_review_fold_bar.py
tests/test_sidecar_fanout_rows.py
tests/test_sidecar_fanout_usage_limit.py
tests/test_sidecar_fanout_waves_reset.py
tests/test_sidecar_inherited_machinery.sh
tests/test_sidecar_inventory_lifecycle.sh
tests/test_sidecar_launch.py
tests/test_sidecar_list_cost.sh
tests/test_sidecar_long_context.sh
tests/test_sidecar_perm_default.sh
tests/test_sidecar_price_window.sh
tests/test_sidecar_producer_identity.py
tests/test_sidecar_quota_fit.sh
tests/test_sidecar_rate_limit_record.sh
tests/test_sidecar_record_config_fields.sh
tests/test_sidecar_record_rescues.sh
tests/test_sidecar_repay.sh
tests/test_sidecar_replace_retry.py
tests/test_sidecar_result_ends_child.sh
tests/test_sidecar_resume_check.py
tests/test_sidecar_scrub_passthrough.sh
tests/test_sidecar_snapshot_sweep.sh
tests/test_sidecar_stall_watchdog.sh
tests/test_sidecar_stopped_run.sh
tests/test_sidecar_tool_profiles.sh
tests/test_sidecar_tools_limit.sh
tests/test_sidecar_turn1_plan_root.sh
tests/test_sidecar_usage_limit.sh
tests/test_sidecar_watch_rearm.py
tests/test_skill_load_marker_ladder.py
tests/test_stream_events.py
tests/test_survey_guard_profile_parity.py
tests/test_text_only_shim.py
tests/test_tier_table.py
tests/test_tool_profiles.py
tests/test_tool_profiles_init_events.py
tests/test_tool_routing_nudge_dedupe.py
tests/test_tool_routing_nudge_vault_write.py
tests/test_tool_routing_post_grep.py
tests/test_triage.py
tests/test_triage_admits.py
tests/test_triage_commit_check.py
tests/test_triage_commit_check_project.py
tests/test_verify_transport_status_reason.py
tests/test_wake_digest_amend.py
tests/test_wake_digest_watcher_shell.py
tests/test_watch_records_fanout_end.py
tests/test_watch_records_pane.py
tests/test_watch_records_wait.py
tests/test_watch_records_workflow.py
tests/test_wide_test_guard.py
tests/test_worker_rate_difficulty.py
tests/test_worker_rate_epoch.py
tests/test_worker_rate_mutations.py
tests/test_worker_rate_provenance.py
tests/test_workflow_provider_guard.py
tests/test_workflow_provider_guard_bar.py
tests/test_workflow_provider_guard_citers.py
tests/test_workflow_provider_guard_effort.py
tests/test_workflow_provider_guard_ladder.py
tests/test_workflow_provider_guard_ladder_slice.py
tests/test_workflow_provider_guard_measurement.py
tests/test_workflow_provider_guard_plans.py
tests/test_workflow_provider_guard_rails.py
tests/test_workflow_provider_guard_repo_root.py
tests/test_workflow_provider_guard_returns.py
tests/test_workflow_provider_guard_roles.py
tests/test_workflow_provider_guard_vocabulary.py
tests/test_worklog_relevance_sidecar.py
tests/test_worklog_relevance_sidecar_shipped.py
tests/test_worklog_user_tasks_conformance_j9.py
tests/workflow_replay_contract_test.js
```

### tools/ (65)

```
tools/agent_progress.py
tools/aggregate_routing_audit.py
tools/ai_worker_metrics.py
tools/analyze_eval_archive.py
tools/autolearn_outcomes.py
tools/baseline_compose.py
tools/baseline_identity.py
tools/baseline_linux_preview.py
tools/baseline_publish.py
tools/baseline_sync.py
tools/battery_children.py
tools/citer_coverage.py
tools/delegate_prepare.py
tools/dispatch_size.py
tools/dispatch_take.py
tools/escape_log.py
tools/eval_dashboard.py
tools/eval_findings.py
tools/eval_history.py
tools/eval_render.py
tools/exhausted_marker.py
tools/extract_numbers.py
tools/extract_subagent_tools.py
tools/finding_bar.py
tools/fold_check.py
tools/free_pool.py
tools/gate_growth.py
tools/gate_trend.py
tools/guard_text.py
tools/ladder_ingest.py
tools/ladder_prose_check.py
tools/launch_memory.py
tools/layer_closure.py
tools/lens_briefs.py
tools/load_census.py
tools/lock_merge.py
tools/mechanism_census.py
tools/memory_hits.py
tools/model_registry.py
tools/ollama_contention_probe.md
tools/ollama_contention_probe.py
tools/post_check.py
tools/provider_bands.py
tools/provider_capacity.py
tools/quota_bands.py
tools/restate_scan.py
tools/review_calibration.py
tools/review_ledger.py
tools/review_result.py
tools/rule_retirement.py
tools/score_routing_battery.py
tools/self_eval_archive_store.py
tools/sidecar_argv.py
tools/sidecar_completion.py
tools/sidecar_fanout.py
tools/sidecar_launch.py
tools/sidecar_resume_check.py
tools/stream_events.py
tools/tool_profiles.py
tools/triage.py
tools/verify_transport_status.py
tools/wake_digest.py
tools/watch_records.py
tools/worker_rate.py
tools/workflow_replay.mjs
```

## TB3 rows (test files dropped by TB3)

Written by job TB3 (test conversion, doctrine-contract tests). Rule applied: a test file is dropped when its subject is already gone from `v2` or when it only polices prose. A test whose subject is a file still present on `v2` and classed delete is left for B16, which deletes it with its subject; those are listed in the TB3 return, not here. B16 appends its own rows below this table and writes the header count.

| File dropped (`template/.claude/tests/`) | Subject at base | Why dropped | Need or replacement |
|---|---|---|---|
| `test_instruction_quality_split.py` | the split layout of `instruction_quality` (`reference/` files, the harness-edit hook that named them) | the layout and its hook are gone (A9 moved the skill; V11 deletes `harness_edit_skill_reminder.py`); the asserts police the old file split | the skill's size is the re-attach cap in `skill_bodies`, checked by S7's budget test (`P:410`) |
| `test_rigor_doctrine.py` | `skills/orchestration/reference/rigor.md` and `rigor_stages.md` (deleted by A2) | the bar owner and stage table it cites are gone; its remaining cases police retired tier and verdict words (prose) and drive `harness_tests.py`'s proof corpus (V9) | the rigor bar is one line in the core doctrine (A1); no test polices its wording |
| `test_seat_outcome_parity.py` | `workflows/review_fanout.js` (V8, absent at base) and `tools/review_ledger.py`'s `_sidecar_seats` | one side of the parity it proves is deleted; the seat outcome rule lives in `/review`'s seat route now (Q6) | `/review` diff mode is covered by the seat launcher's own proofs (B1) |
| `test_check_learnings.py` | `check_learnings.sh`, the Stop hook's transcript scan (absent at base; no reference anywhere outside tests) | the autolearn scan is deleted with V11's `autolearn*`; the script is gone | none; the Stop hook no longer exists |
| `test_scaled_advisory_facts.py` case `test_plan_domain_set_dedupes_across_files_and_omits_loaded_skills` | `hooks/plan_memory_reminder.py` (V11 deletes it) | the hook it proves is deleted; its domain-set rule has no live subject | none; the memory domains are read by the skill that remains |
| `test_scaled_advisory_facts.py` case `test_number_fact_once_but_new_number_fires` | `hooks/rule_number_guard.py` (V11 deletes it) | the guard it proves is deleted | none; no rule-number guard exists in v2 |
| `test_tool_profiles.py` citer sweep block (planted repository, `citers.py`) | `tools/citers.py` (V8 deletes it; it imports `sentence_diff`, deleted) | the subject tool is deleted, so its planted-repository case has nothing to run | none; the live-tree sweep goes with `citers.py` in B16 |

Manifest rows for these four files (`baseline.manifest.json`, `tools/layer_entries.json`) are repo-root v1 publish machinery; B16 removes them with the cutover delete list (Q11).
