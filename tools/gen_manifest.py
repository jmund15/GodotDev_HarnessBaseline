#!/usr/bin/env python3
"""
gen_manifest.py — regenerate baseline.manifest.json from the template/ tree.

Each manifest entry: {"path": <relpath under template/>, "layer": ..., "sync": ...}

  layer "pure"      : any Claude Code project, including non-code content production
  layer "coding"    : any *programming* project (builds, tests, PRs, refactors)
  layer "godot"     : Godot 4.x + C# projects (absorbs the former jmodot layer)
  sync  "auto"      : hash-tracked by baseline_sync.py in consumer projects
  sync  "seed"      : copied at bootstrap, thereafter project-owned (watch-only)

A consumer subscribes to a layer *prefix* of pure -> coding -> godot
(bootstrap --layers). Layer assignment has NO fallback: every template file must
match exactly one layer's pattern list, or generation fails loudly. The old
default-to-universal fallthrough is what let ~100 files mistag silently.

Run from the baseline repo root after adding/removing template files:
  python3 tools/gen_manifest.py
  python3 tools/gen_manifest.py --check  # verify without writing
"""
from __future__ import annotations

import fnmatch
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template"

SEED_PATTERNS = [
    ".claude/CLAUDE.md",
    ".claude/settings.project.json",
    ".claude/worklog-titles.md",
    ".claude/skills/game_vision/*",
    ".claude/skills/project_subsystems/*",
    ".claude/commands/checklists/known_failure_modes.md",
]

GODOT_PATTERNS = [
    ".claude/skills/testing/*",
    ".claude/skills/refactor_procedure/*",
    ".claude/skills/sprite_authoring/*",
    ".claude/skills/shader_authoring/*",
    ".claude/skills/parameterized_asset_pipeline/*",
    ".claude/skills/game_vision/*",
    ".claude/skills/project_subsystems/*",
    ".claude/rules/csharp_patterns.md",
    ".claude/rules/godot_files.md",
    ".claude/rules/scene_authoring.md",
    ".claude/rules/physics_patterns.md",
    ".claude/rules/cloud_dev.md",
    ".claude/hooks/session_context_loader.py",
    ".claude/hooks/cloud_test_enforcer.py",
    ".claude/hooks/analyze_godot_logs.py",
    ".claude/hooks/tres_nullstrip_guard.py",
    ".claude/hooks/apply_blanket_tool.py",
    ".claude/hooks/tool_cascade_audit.py",
    ".claude/hooks/pattern_enforcer.py",
    ".claude/commands/regression_gate.md",
    ".claude/commands/analyze_godot_logs.md",
    ".claude/commands/audit_test_accessors.md",
    ".claude/commands/pr_test_checklist.md",
    ".claude/commands/structure_audit.md",
    ".claude/commands/agents/structure_audit_agents.md",
    ".claude/commands/agents/pr_test_checklist_conventions.md",
    ".claude/cloud-install.sh",
    ".claude/scripts/run_test_suite.ps1",
    ".claude/auto-memory/gotcha_cascade_gate_*.md",
    ".claude/auto-memory/gotcha_godot*.md",
    ".claude/auto-memory/gotcha_gdunit4_*.md",
    ".claude/auto-memory/gotcha_tscn_*.md",
    ".claude/auto-memory/gotcha_editor_reserialize_*.md",
    ".claude/auto-memory/gotcha_export_enum_*.md",
    ".claude/auto-memory/gotcha_inherited_scene_*.md",
    ".claude/auto-memory/feedback_godot_*.md",
    ".claude/auto-memory/feedback_lsp_default_for_csharp.md",
    # Godot-specific items the old universal fallback mistagged:
    ".claude/auto-memory/gotcha_empty_tween_never_fires_finished.md",
    ".claude/auto-memory/gotcha_simulateframes_no_physics_tick.md",
    ".claude/auto-memory/gotcha_sprite3d_typed_ref_excludes_animatedsprite3d.md",
    ".claude/auto-memory/gotcha_unhandled_ready_exception_invisible_in_log.md",
    ".claude/auto-memory/feedback_considerations_consume_perception_not_queries.md",
    ".claude/commands/doc_usage.md",

    '.claude/hooks/activity_registry.py',
    '.claude/hooks/load_steps_validator.py',
    '.claude/hooks/tres_format_guard.py',
    '.claude/hooks/tres_script_strip_guard.py',
    '.claude/hooks/uid_cache_audit.py',
    '.claude/scripts/GodotProcess.ps1',
    '.claude/scripts/batch_diagnosis.ps1',
    '.claude/tools/gen_godot_class_index.py',
    '.claude/guards/any.md',
    '.claude/guards/survey.md',
    '.claude/hooks/duplicate_test_double_guard.py',
    '.claude/hooks/duplicate_test_double_baseline.json',
    '.claude/hooks/generate_family_manifest.py',
    '.claude/hooks/prototype_containment_guard.py',
    '.claude/hooks/session_model_rails.py',
    '.claude/hooks/test_suite_gate_coverage_guard.py',
    '.claude/scripts/gate_queue_watcher.ps1',
    '.claude/scripts/regression_gate.ps1',
    '.claude/scripts/GateQueue.ps1',
    '.claude/scripts/verify.ps1',
    '.claude/scripts/godot_bin.sh',
    '.claude/hooks/gate_queue_surface.py',
    '.claude/hooks/gate_cadence_guard.py',
    '.claude/hooks/provisional_totality_guard.py',
    '.claude/reference/source_trust.md',
    '.claude/reference/memory_domains.md',
    '.claude/reference/memory_domains.base.md',
    '.claude/scripts/run_integration_batched.ps1',
    '.claude/tests/test_run_integration_batched_adaptation.ps1',
    '.claude/skills/_brainstorm_shared/execution_depth.md',
    '.claude/skills/merge_conflicts/SKILL.md',
    '.claude/skills/prototype/SKILL.md',
    '.claude/tools/verify_claims.py',
    '.claude/tools/verify_doc_citations.py',
    # Former jmodot layer — folded into godot per the three-archetype taxonomy:
    ".claude/skills/jmodot/*",
    ".claude/skills/status_effect_authoring/*",
    ".claude/skills/vfx_patterns/*",
    ".claude/skills/logging_methodology/*",
    ".claude/rules/hsm_bt_patterns.md",
    ".claude/rules/jmodot_*.md",
    ".claude/rules/visual_layers.md",
    ".claude/hooks/check_logger_tag_prefix.py",
    ".claude/commands/agents/jmodot_submodule_procedure.md",
    ".claude/auto-memory/jmodot_*.md",
    ".claude/auto-memory/arch_rule_*.md",
    ".claude/auto-memory/Blackboard_NullStorage_Asymmetry.md",
    ".claude/auto-memory/gotcha_blackboard_*.md",
    ".claude/auto-memory/gotcha_component_caches_*.md",
    ".claude/auto-memory/feedback_prefer_typed_shapes_over_empty_markers.md",
    ".claude/auto-memory/feedback_prefer_data_params_over_injected_delegates.md",
    ".claude/auto-memory/feedback_typed_state_over_bb_flag_soup.md",
    ".claude/auto-memory/feedback_dont_defer_existing_framework_abstractions.md",
    ".claude/auto-memory/gotcha_config_exception_node_bound_not_static.md",
    ".claude/auto-memory/gotcha_getfirstchildof_logs_error_on_miss.md",
    ".claude/auto-memory/gotcha_authoring_validator_needs_static_resolvable_reference.md",
    ".claude/commands/doc_workflow_battery.md",
    ".claude/commands/workstation_setup.md",
    # Accepted cold-memory moves and new Godot harness proofs:
    ".claude/auto-memory/archive/arch_rule_*.md",
    ".claude/auto-memory/archive/feedback_dont_defer_existing_framework_abstractions.md",
    ".claude/auto-memory/archive/feedback_lsp_default_for_csharp.md",
    ".claude/auto-memory/archive/feedback_prefer_data_params_over_injected_delegates.md",
    ".claude/auto-memory/archive/feedback_typed_state_over_bb_flag_soup.md",
    ".claude/auto-memory/archive/gotcha_godot_resource_cache_stateful_tres_test_isolation.md",
    ".claude/auto-memory/archive/gotcha_simulateframes_no_physics_tick.md",
    ".claude/auto-memory/gotcha_editor_resave_drops_unloadable_ext_resource.md",
    ".claude/hooks/refcounted_free_guard.py",
    ".claude/tests/test_refcounted_free_guard_adaptation.py",
    ".claude/tests/test_activity_registry_*.py",
    ".claude/tests/test_duplicate_test_double_*.py",
    ".claude/tests/test_gate_cadence_*.py",
    ".claude/tests/test_pattern_enforcer_*.py",
    ".claude/tests/test_prototype_containment_*.py",
    ".claude/tests/test_provisional_totality_*.py",
    ".claude/tests/test_session_context_loader*.py",
    ".claude/tests/test_test_suite_gate_coverage_*.py",
    ".claude/tests/test_tres_nullstrip_*.py",
    ".claude/tests/test_tres_script_strip_*.py",
]

# Any programming project, but not content production: build/test/PR machinery,
# plan/roadmap pipeline, tool-routing hook family, code-architecture doctrine.
CODING_PATTERNS = [
    ".claude/skills/architecture_brainstorm/*",
    ".claude/skills/architecture_philosophy/*",
    ".claude/skills/debugging/*",
    ".claude/hooks/pre_read_dispatch.py",
    ".claude/hooks/indexed_reference_guard.py",
    ".claude/hooks/post_read_dispatch.py",
    ".claude/hooks/routing_audit.py",
    ".claude/hooks/routing_classifier.py",
    ".claude/hooks/tool_routing_*.py",
    ".claude/commands/mvp_plan.md",
    ".claude/commands/part_drive.md",
    ".claude/commands/part_execute.md",
    ".claude/commands/plan_drive.md",
    ".claude/commands/plan_handoff.md",
    ".claude/commands/plan_part.md",
    ".claude/commands/roadmap_audit.md",
    ".claude/commands/roadmap_next.md",
    ".claude/commands/update_roadmap.md",
    ".claude/commands/merge_pr.md",
    ".claude/commands/pr_ready.md",
    ".claude/commands/review_pr.md",
    ".claude/commands/review_prs.md",
    ".claude/commands/sync_subsystems.md",
    ".claude/commands/architecture_brainstorm_redteam.md",
    ".claude/commands/agents/plan_check_agents.md",
    ".claude/commands/agents/pr_classification.md",
    ".claude/commands/agents/review_agents.md",
    ".claude/commands/agents/worklog_plan_triage.md",
    ".claude/commands/checklists/code_quality.md",
    ".claude/commands/checklists/test_quality.md",
    # code-language / testing / code-hygiene gotchas:
    ".claude/auto-memory/feedback_plan_check_auto_surface_on_exit.md",
    ".claude/auto-memory/feedback_reconcile_structure_against_existing_subsystems.md",
    ".claude/auto-memory/feedback_refactor_parity_audit.md",
    ".claude/auto-memory/feedback_test_fixture_must_match_production_topology.md",
    ".claude/auto-memory/feedback_tool_routing_discipline.md",
    ".claude/auto-memory/feedback_verify_type_contract_before_design_lock.md",
    ".claude/auto-memory/gotcha_default_interface_method_for_base_member.md",
    ".claude/auto-memory/gotcha_errorsonly_build_hides_cref_drift.md",
    ".claude/auto-memory/gotcha_explicit_dim_orphans_on_base_interface_removal.md",
    ".claude/auto-memory/gotcha_grep_brace_glob_silent_zero.md",
    ".claude/auto-memory/gotcha_namespace_rename_breaks_relative_using.md",
    ".claude/auto-memory/gotcha_pure_clr_poco_mirror_payload_type.md",
    ".claude/auto-memory/gotcha_relocation_doccomment_cref_boundary.md",
    ".claude/auto-memory/gotcha_schema_version_bump_breaks_version_tests.md",
    ".claude/auto-memory/gotcha_subscribe_before_synchronous_fire_bind.md",
    ".claude/auto-memory/gotcha_synthetic_fixture_hides_real_input_failure.md",
    ".claude/auto-memory/gotcha_type_name_equals_namespace_leaf.md",
    ".claude/skills/_brainstorm_shared/common.md",
    ".claude/auto-memory/feedback_plan_pending_requires_impl_arch_not_seam.md",
    ".claude/auto-memory/feedback_spec_strength_sets_agent_tier.md",
    ".claude/commands/session_refresh.md",
    ".claude/commands/test_compact.md",
    ".claude/commands/rule_consistency.md",
    ".claude/commands/claudemd_compact.md",
    ".claude/commands/session_audit.md",
    ".claude/commands/agents/session_audit_agents.md",
    ".claude/commands/doc_full.md",
    ".claude/commands/create_obsidian_design_doc.md",
    ".claude/commands/test_agents.md",
    ".claude/commands/system_check.md",
    ".claude/commands/routing_battery.md",
    ".claude/scripts/validate_commands.py",
    ".claude/tools/score_routing_battery.py",

    '.claude/commands/memory_audit.md',
    '.claude/rules/design_litmus.md',
    '.claude/rules/csharp_lsp.md',
    '.claude/tools/csharp-ls-adapter.js',
    '.claude/tools/setup-csharp-ls.sh',
    '.claude/rules/test_authoring.md',
    '.claude/commands/research.md',
    '.claude/skills/orchestration/SKILL.md',
    '.claude/workflows/dispatch.js',
    '.claude/workflows/dispatch_chains.js',
    '.claude/workflows/explore_fanout.js',
    '.claude/commands/agents/explore_agents.md',
    '.claude/commands/agents/worklog_drive_triage.md',
    # Accepted coding memories and regression proofs:
    ".claude/auto-memory/archive/feedback_plan_pending_requires_impl_arch_not_seam.md",
    ".claude/auto-memory/archive/feedback_tool_routing_discipline.md",
    ".claude/auto-memory/feedback_control_run_before_bisecting_a_runtime_failure.md",
    ".claude/auto-memory/feedback_gate_invalid_targeted_verification.md",
    ".claude/auto-memory/feedback_review_cannot_see_a_wrong_altitude_seam.md",
    ".claude/auto-memory/gotcha_author_lane_edit_clips_method_header.md",
    ".claude/auto-memory/gotcha_merge_silently_drops_one_sided_add.md",
    ".claude/auto-memory/gotcha_session_start_submodule_autofix_detaches_head.md",
    ".claude/tests/build_plan_brief_test.py",
    ".claude/tests/test_build_plan_auto_variant.py",
    ".claude/tests/test_git_guardrails_harness_commit.py",
    ".claude/tests/test_routing_classifier.py",
    # Accepted candidates requiring an exact layer entry:
    ".claude/auto-memory/archive/feedback_session_end_full_scope.md",
    ".claude/reference/semantic_search.md",
    ".claude/skills/benchmark_design/SKILL.md",
    ".claude/skills/benchmark_design/reference/configuration_and_transport.md",
    ".claude/skills/benchmark_design/reference/controls_and_subfloor.md",
    ".claude/skills/benchmark_design/reference/staircase_construction.md",

]

# Fully domain-agnostic — proven portable to a non-code content-production
# harness verbatim or with {{PLACEHOLDER}} substitution (two-domain rule).
PURE_PATTERNS = [
    ".claude/CLAUDE.md",
    ".claude/CLAUDE.core.md",
    ".claude/settings.base.json",
    ".claude/settings.project.json",
    ".claude/worklog-titles.md",
    # Sidecar/tooling trio consumed by any harness (pure):
    ".claude/reference/external_models.json",
    ".claude/scripts/claude_profile_functions.ps1",
    ".claude/auto-memory/MEMORY.md",
    ".claude/auto-memory/archive/*",
    ".claude/auto-memory/diagnose_specific_objection_before_pivot.md",
    ".claude/auto-memory/feedback_command_descriptions_one_line.md",
    ".claude/auto-memory/feedback_design_workflow_model_mix.md",
    ".claude/auto-memory/feedback_doc_revision_in_place.md",
    ".claude/auto-memory/feedback_dont_compress_socratic_on_rich_prompt.md",
    ".claude/auto-memory/feedback_dont_defer_immediately_addressable.md",
    ".claude/auto-memory/feedback_dont_unilaterally_reduce_planned_scope.md",
    ".claude/auto-memory/feedback_fix_self_introduced_regression_immediately.md",
    ".claude/auto-memory/feedback_honor_execution_directive.md",
    ".claude/auto-memory/feedback_inspect_existing_abstractions_first.md",
    ".claude/auto-memory/feedback_invoke_named_skill_not_manual_equivalent.md",
    ".claude/auto-memory/feedback_memory_file_refs_no_markdown_links.md",
    ".claude/auto-memory/feedback_memory_md_is_auto_managed.md",
    ".claude/auto-memory/feedback_no_performative_agreement.md",
    ".claude/auto-memory/feedback_no_unilateral_condensation.md",
    ".claude/auto-memory/feedback_pathspec_commit_stage_infra_deps.md",
    ".claude/auto-memory/feedback_plan_files_are_context_free_execution_docs.md",
    ".claude/auto-memory/feedback_plan_mode_is_claude_code_built_in.md",
    ".claude/auto-memory/feedback_plan_worklog_items_from_source_not_mirror.md",
    ".claude/auto-memory/feedback_read_files_enumerate_first.md",
    ".claude/auto-memory/feedback_read_files_multifile_completeness_directive.md",
    ".claude/auto-memory/feedback_read_files_output_volume_governs_spill.md",
    ".claude/auto-memory/feedback_recommended_fix_means_implement.md",
    ".claude/auto-memory/feedback_resolve_questions_in_plan_not_execution.md",
    ".claude/auto-memory/feedback_separate_preexisting_changes_before_commit.md",
    ".claude/auto-memory/feedback_session_end_command_over_passive_nudge.md",
    ".claude/auto-memory/feedback_session_end_full_scope.md",
    ".claude/auto-memory/feedback_session_start_hook_does_not_override_skill_procedure.md",
    ".claude/auto-memory/feedback_skill_vs_command_frontmatter_convention.md",
    ".claude/auto-memory/feedback_slash_command_naming.md",
    ".claude/auto-memory/feedback_user_distress_lexicon.md",
    ".claude/auto-memory/feedback_verbatim_content_to_review_agents.md",
    ".claude/auto-memory/feedback_verify_explore_agent_empirical_claims.md",
    ".claude/auto-memory/feedback_verify_plan_integration_target_is_live.md",
    ".claude/auto-memory/gotcha_baseline_clone_push_workflow.md",
    ".claude/auto-memory/gotcha_cross_project_memory_index_autoload.md",
    ".claude/auto-memory/gotcha_semantic_search_restricttodir_posix.md",
    ".claude/auto-memory/ground_in_system_purpose_before_subpart_plan.md",
    ".claude/auto-memory/precedent_is_evidence_not_authority.md",
    ".claude/auto-memory/process_rule_plan_high_execute_lower.md",
    ".claude/auto-memory/process_rule_spec_doc_coverage.md",
    ".claude/commands/agents/doc_before_writing.md",
    ".claude/commands/agents/documentation_structure.md",
    ".claude/commands/agents/orchestrator_action_protocol.md",
    ".claude/commands/agents/session_file_identification.md",
    ".claude/commands/agents/transcript_nuance_recall.md",
    ".claude/commands/aiworker_write_doc_audit.md",
    ".claude/commands/autolearn.md",
    ".claude/commands/checklists/known_failure_modes.md",
    ".claude/commands/clean_pull.md",
    ".claude/commands/clean_push.md",
    ".claude/commands/commit_push.md",
    ".claude/commands/create_pr.md",
    ".claude/commands/doc_architecture.md",
    ".claude/commands/doc_architecture_audit.md",
    ".claude/commands/doc_audit_fix.md",
    ".claude/commands/doc_retrospective.md",
    ".claude/commands/doc_start_here_update.md",
    ".claude/commands/eval_dashboard.md",
    ".claude/commands/idea_brainstorm_fanout.md",
    ".claude/commands/instruction_audit.md",
    ".claude/commands/memory_audit.md",
    ".claude/commands/memory_graph.md",
    ".claude/commands/reindex_search.md",
    ".claude/commands/routing_audit.md",
    ".claude/commands/self_evaluate.md",
    ".claude/commands/session_end.md",
    ".claude/commands/sync_baseline.md",
    ".claude/commands/sync_permissions.md",
    ".claude/commands/test_skill.md",
    ".claude/commands/worklog.md",
    ".claude/hooks/README.md",
    ".claude/hooks/_transcript_summary.py",
    ".claude/hooks/compound_cd_approver.py",
    ".claude/hooks/bash_shape_guard.py",
    ".claude/hooks/git_guardrails.py",
    ".claude/hooks/baseline_classification_guard.py",
    ".claude/hooks/dangerous_shell_guard.py",
    ".claude/hooks/unbounded_scan_guard.py",
    ".claude/hooks/running_script_edit_guard.py",
    ".claude/hooks/harness_edit_skill_reminder.py",
    ".claude/scripts/deepseek_sidecar.sh",
    ".claude/hooks/critical_analysis_reminder.py",
    ".claude/hooks/file_size_preblock.py",
    ".claude/hooks/json_merge.py",
    ".claude/hooks/log_instruction_loads.py",
    ".claude/hooks/plan_memory_reminder.py",
    ".claude/hooks/prompt_git_state_delta.py",
    ".claude/hooks/prompt_memory_loader.py",
    ".claude/hooks/transcript_backup.py",
    ".claude/rules/model_delegation.md",
    ".claude/scripts/autolearn.sh",
    ".claude/scripts/check_learnings.sh",
    ".claude/skills/_brainstorm_shared/appetite_invariant.md",
    ".claude/skills/idea_brainstorm/*",
    ".claude/skills/instruction_quality/*",
    ".claude/skills/mermaid_diagrams/*",
    ".claude/skills/obsidian_conventions/*",
    ".claude/skills/parallel_agents/*",
    ".claude/skills/worklog_reference/*",
    ".claude/tools/aggregate_routing_audit.py",
    ".claude/tools/analyze_eval_archive.py",
    ".claude/tools/baseline_sync.py",
    ".claude/tools/baseline_publish.py",
    ".claude/tools/baseline_identity.py",
    ".claude/tools/adaptation.py",
    ".claude/tools/baseline_compose.py",
    ".claude/tools/extract_subagent_tools.py",
    ".claude/workflows/*.js",

    '.claude/commands/apply_harness_edits.md',
    '.claude/commands/delegate.md',
    '.claude/commands/design_drive.md',
    '.claude/commands/explore.md',
    '.claude/commands/feature_drive.md',
    '.claude/commands/plan_check.md',
    '.claude/commands/ingest_conversation.md',
    '.claude/commands/orchestration_metrics.md',
    '.claude/commands/salvage_fanout.md',
    '.claude/guards/author.md',
    '.claude/guards/review.md',
    '.claude/hooks/activity_registry.py',
    '.claude/hooks/budget_posture.py',
    '.claude/hooks/design_surface_reminder.py',
    '.claude/hooks/model_pin_translate.py',
    '.claude/hooks/readonly_lens_write_guard.py',
    '.claude/hooks/readonly_marker_arm.py',
    '.claude/hooks/workflow_provider_guard.py',
    '.claude/scripts/anthropic_quota_probe.py',
    '.claude/scripts/codex_effort_probe.py',
    '.claude/scripts/doc_diff_check.py',
    '.claude/scripts/doc_warning_check.sh',
    '.claude/scripts/fetch_source.sh',
    '.claude/scripts/godot_docs_cache.sh',
    '.claude/scripts/schema_parity.js',
    '.claude/scripts/worklog_relevance_sidecar.sh',
    '.claude/skills/_brainstorm_shared/design_contract.md',
    '.claude/skills/_brainstorm_shared/plan_file_format.md',
    '.claude/skills/wait_what/SKILL.md',
    '.claude/tests/test_anthropic_quota_probe.py',
    '.claude/tests/test_baseline_classification_guard.py',
    '.claude/tests/test_baseline_sync.py',
    '.claude/tests/fixtures/v1_check_golden.json',
    '.claude/tests/test_baseline_publish.py',
    '.claude/tests/test_baseline_identity.py',
    '.claude/tests/_adaptation_fixture.py',
    '.claude/tests/test_adaptation.py',
    '.claude/tests/test_compound_cd_approver_adaptation.py',
    '.claude/tests/test_prompt_git_state_delta_adaptation.py',
    '.claude/tests/test_prompt_memory_loader_adaptation.py',
    '.claude/tests/test_plan_memory_reminder_adaptation.py',
    '.claude/tests/test_prose_adaptation_seams.py',
    '.claude/tests/test_baseline_compose.py',
    '.claude/tests/test_settings_composed.py',
    '.claude/tests/test_codex_effort_probe.py',
    '.claude/tests/test_delegate_command.py',
    '.claude/tests/test_sidecar_rate_limit_record.sh',
    '.claude/tests/test_sidecar_launch.py',
    '.claude/tools/model_registry.py',
    '.claude/tools/orchestration_metrics.py',
    '.claude/tools/guard_text.py',
    '.claude/tools/quota_bands.py',
    '.claude/tools/provider_bands.py',
    '.claude/tools/sidecar_launch.py',
    '.claude/tools/lens.py',
    '.claude/tools/kfm.py',
    '.claude/hooks/_hook_state.py',
    '.claude/hooks/skill_load_marker.py',
    '.claude/hooks/harness_growth_guard.py',
    '.claude/hooks/subagent_dispatch_guard.py',
    '.claude/hooks/dispatch_mechanism_guard.py',
    '.claude/hooks/sidecar_dispatch_context.py',
    '.claude/reference/claim_confidence.md',
    '.claude/reference/model_ladder_evidence.md',
    '.claude/reference/sidecar_dispatch.md',
    '.claude/rules/harness_tooling.md',
    '.claude/scripts/lib/sidecar_common.sh',
    '.claude/scripts/codex_sidecar.sh',
    '.claude/scripts/codex_proxy_sidecar.sh',
    '.claude/scripts/opencode_sidecar.sh',
    '.claude/workflows/explore_fanout.schema.json',
    '.claude/workflows/worklog_relevance.prompt.md',
    '.claude/workflows/worklog_relevance.schema.json',
    # Accepted candidates requiring an exact layer entry:
    ".claude/auto-memory/feedback_absence_is_not_a_defect_until_dated.md",
    ".claude/auto-memory/feedback_completion_is_not_engagement.md",
    ".claude/auto-memory/feedback_controlled_contrast_or_it_proves_nothing.md",
    ".claude/auto-memory/feedback_excluded_and_broken_look_identical.md",
    ".claude/auto-memory/feedback_fix_the_class_not_the_instance.md",
    ".claude/auto-memory/feedback_found_defect_is_fixed_now_peer_owned_is_a_git_fact.md",
    ".claude/auto-memory/feedback_move_destination_must_be_read.md",
    ".claude/auto-memory/feedback_one_observation_licenses_a_hypothesis_never_a_scope_decision.md",
    ".claude/auto-memory/feedback_orchestrate_loads_orchestration_skill.md",
    ".claude/auto-memory/feedback_price_the_tradeoff_before_recommending_it.md",
    ".claude/auto-memory/gotcha_auto_mode_classifier_fail_closed.md",
    ".claude/auto-memory/gotcha_program_spawned_shell_is_not_your_shell.md",
    ".claude/auto-memory/gotcha_self_reported_model_identity_is_not_authority.md",
    ".claude/commands/session_digest.md",
    ".claude/schemas/review_findings.json",
    ".claude/scripts/harness_tests.py",
    ".claude/tests/test_harness_tests_allow_cannot_run.py",
    ".claude/tests/test_harness_tests_adaptation.py",
    ".claude/tests/test_session_digest.py",
    ".claude/tools/session_digest.py",
    ".claude/tools/sidecar_fanout.py",
    ".claude/commands/codify.md",
    ".claude/commands/ladder_ingest.md",
    ".claude/commands/overnight.md",
    ".claude/commands/pin_ab.md",
    ".claude/commands/worker_audit.md",
    ".claude/commands/worktree_prune.md",
    ".claude/hooks/_command_text.py",
    ".claude/hooks/_git_commit.py",
    ".claude/hooks/_model_tier.py",
    ".claude/hooks/_session_transport.py",
    ".claude/hooks/heredoc_escape_guard.py",
    ".claude/hooks/kill_guard.py",
    ".claude/hooks/memory_hits_logger.py",
    ".claude/hooks/overnight_ask_guard.py",
    ".claude/hooks/pre_bash_dispatch.py",
    ".claude/hooks/self_eval_archive_guard.py",
    ".claude/hooks/shell_census.py",
    ".claude/hooks/sidecar_recompact_reprompt.py",
    ".claude/hooks/workflow_script_lf_guard.py",
    ".claude/reference/codex_session_runbook.md",
    ".claude/rules/harness_authoring.md",
    ".claude/scripts/lib/ccp_probe.py",
    ".claude/scripts/lib/oc_proxy_probe.py",
    ".claude/scripts/lib/text_only_shim.py",
    ".claude/scripts/opencode_refresh_models.py",
    ".claude/tests/_commit_guard_cases.py",
    ".claude/tests/_transport_fixture.py",
    ".claude/tests/dispatch_no_return_test.js",
    ".claude/tests/sidecar_resume_loop_test.sh",
    ".claude/tests/test_aggregate_routing_audit.py",
    ".claude/tests/test_analyze_eval_archive.py",
    ".claude/tests/test_budget_posture_cadence.py",
    ".claude/tests/test_budget_posture_rating_debt.py",
    ".claude/tests/test_budget_posture_transport.py",
    ".claude/tests/test_command_text_heredoc.py",
    ".claude/tests/test_dispatch_mechanism_guard.py",
    ".claude/tests/test_file_size_preblock.py",
    ".claude/tests/test_git_commit_seam.py",
    ".claude/tests/test_harness_edit_skill_reminder.py",
    ".claude/tests/test_heredoc_escape_guard.py",
    ".claude/tests/test_hook_state.py",
    ".claude/tests/test_kill_guard.py",
    ".claude/tests/test_ladder_ingest.py",
    ".claude/tests/test_ladder_ingest_sources.py",
    ".claude/tests/test_ladder_prose_check.py",
    ".claude/tests/test_log_instruction_loads.py",
    ".claude/tests/test_memory_hits_logger.py",
    ".claude/tests/test_model_registry.py",
    ".claude/tests/test_model_tier.py",
    ".claude/tests/test_nested_fanout_guard.py",
    ".claude/tests/test_orchestration_metrics_pending.py",
    ".claude/tests/test_orchestration_metrics_resource_lifetime.py",
    ".claude/tests/test_orchestration_metrics_session_sidecar.py",
    ".claude/tests/test_overnight_ask_guard.py",
    ".claude/tests/test_post_read_dispatch.py",
    ".claude/tests/test_pre_bash_dispatch.py",
    ".claude/tests/test_prompt_memory_loader.py",
    ".claude/tests/test_provider_bands_cache.py",
    ".claude/tests/test_quota_bands.py",
    ".claude/tests/test_routing_audit_subagent_exempt.py",
    ".claude/tests/test_running_script_edit_guard.py",
    ".claude/tests/test_self_eval_archive_guard.py",
    ".claude/tests/test_session_end_check.py",
    ".claude/tests/test_session_model_rails_rails.py",
    ".claude/tests/test_session_transport.py",
    ".claude/tests/test_shell_census.py",
    ".claude/tests/test_sidecar_dispatch_context.py",
    ".claude/tests/test_sidecar_scrub_passthrough.sh",
    ".claude/tests/test_sidecar_stall_watchdog.sh",
    ".claude/tests/test_skill_load_marker_ladder.py",
    ".claude/tests/test_subagent_fallback.ps1",
    ".claude/tests/test_text_only_shim.py",
    ".claude/tests/test_tool_routing_nudge_dedupe.py",
    ".claude/tests/test_tool_routing_nudge_vault_write.py",
    ".claude/tests/test_transcript_summary_context.py",
    ".claude/tests/test_unbounded_scan_guard.py",
    ".claude/tests/test_verify_transport_status_reason.py",
    ".claude/tests/test_void_check_engagement.py",
    ".claude/tests/test_worker_rate_difficulty.py",
    ".claude/tests/test_worker_rate_mutations.py",
    ".claude/tests/test_workflow_provider_guard.py",
    ".claude/tests/test_workflow_provider_guard_effort.py",
    ".claude/tests/test_workflow_provider_guard_ladder.py",
    ".claude/tests/test_workflow_provider_guard_roles.py",
    ".claude/tests/test_workflow_provider_guard_vocabulary.py",
    ".claude/tests/workflow_script_lf_guard_test.sh",
    ".claude/tools/ai_worker_metrics.py",
    ".claude/tools/index_composition.py",
    ".claude/tools/ladder_ingest.py",
    ".claude/tools/ladder_prose_check.py",
    ".claude/tools/lens_briefs.py",
    ".claude/tools/lf_normalize.py",
    ".claude/tools/memory_hits.py",
    ".claude/tools/ollama_contention_probe.md",
    ".claude/tools/ollama_contention_probe.py",
    ".claude/tools/session_end_check.py",
    ".claude/tools/sidecar_compact_end.py",
    ".claude/tools/sidecar_resume_check.py",
    ".claude/tools/verify_transport_status.py",
    ".claude/tools/void_check.py",
    ".claude/tools/worker_rate.py",

]

# Layer precedence: most specific wins (a file matching two lists is assigned
# the most specific layer, so broad globs in pure/coding can't swallow a
# godot file).
LAYER_ORDER = [
    ("godot", GODOT_PATTERNS),
    ("coding", CODING_PATTERNS),
    ("pure", PURE_PATTERNS),
]

# Runtime/session-state directories and build artifacts that exist on disk but are
# never part of the shipped template (the filesystem walk doesn't respect gitignore).
# Mirrors baseline_sync.py's consumer-side exclude set.
EXCLUDE_DIR_PARTS = {"__pycache__", "logs", ".cache", "sessions",
                     "worktrees", "plans", "scratch"}


def is_artifact(p: Path) -> bool:
    # Match exclude-parts against the path RELATIVE to TEMPLATE, not the absolute
    # path: a consumer clones this repo to `.../.claude/.cache/baseline-repo`, so the
    # absolute parts always contain `.cache` and would exclude EVERY template file
    # (silently emptying the manifest). Only segments *inside* template/ should count.
    try:
        parts = p.relative_to(TEMPLATE).parts
    except ValueError:
        parts = p.parts
    return p.suffix == ".pyc" or bool(EXCLUDE_DIR_PARTS.intersection(parts))


def match(relpath: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(relpath, p) for p in patterns)


def classify(relpath: str) -> str | None:
    """Return the layer for a template-relative path, or None if unclassified.

    Shared with audit_baseline.py so generator and audit can never disagree.
    """
    for layer, patterns in LAYER_ORDER:
        if match(relpath, patterns):
            return layer
    return None


def main() -> int:
    check_only = "--check" in sys.argv[1:]
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", newline="\n")

    files = []
    unclassified = []
    # Sort by the posix relpath STRING, not the Path object: Path comparison uses
    # os.path.normcase, so it's case-insensitive on Windows and case-sensitive on
    # Linux — same inputs, different manifest order per OS. A string key is stable
    # cross-platform.
    for p in sorted(TEMPLATE.rglob("*"), key=lambda x: x.relative_to(TEMPLATE).as_posix()):
        if not p.is_file() or p.name == ".gitkeep":
            continue
        if is_artifact(p):
            continue  # runtime/session-state artifacts — not part of the template
        rel = p.relative_to(TEMPLATE).as_posix()
        layer = classify(rel)
        if layer is None:
            unclassified.append(rel)
            continue
        sync = "seed" if match(rel, SEED_PATTERNS) else "auto"
        files.append({"path": rel, "layer": layer, "sync": sync})
    if unclassified:
        # No fallback layer, by design: the old default-to-universal fallthrough
        # is how ~100 domain files mistagged silently. Fail loudly instead.
        print("ERROR: unclassified template files — add each to exactly one "
              "layer pattern list:", file=sys.stderr)
        for rel in unclassified:
            print(f"  {rel}", file=sys.stderr)
        return 1

    manifest = {"version": 2, "files": files}
    expected = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    manifest_path = ROOT / "baseline.manifest.json"
    if check_only:
        try:
            current = manifest_path.read_bytes()
        except OSError:
            current = None
        if current != expected:
            print("ERROR: baseline.manifest.json is stale", file=sys.stderr)
            return 1
        counts: dict[str, int] = {}
        for entry in files:
            counts[entry["layer"]] = counts.get(entry["layer"], 0) + 1
        print(f"manifest current: {len(files)} files — " +
              ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
        return 0

    manifest_path.write_bytes(expected)
    counts: dict[str, int] = {}
    for entry in files:
        counts[entry["layer"]] = counts.get(entry["layer"], 0) + 1
    print(f"{len(files)} files — " +
          ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    print("verify separation: python3 tools/audit_baseline.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
