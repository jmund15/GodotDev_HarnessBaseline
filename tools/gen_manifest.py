#!/usr/bin/env python3
"""
gen_manifest.py — regenerate baseline.manifest.json from the template/ tree.

Each manifest entry: {"path": <relpath under template/>, "layer": ..., "sync": ...}

  layer "core"      : any Claude Code project, including non-code content production
  layer "code"      : any *programming* project (builds, tests, PRs, refactors)
  layer "godot"     : Godot 4.x + C# projects
  layer "jmodot"    : projects built on the Jmodot framework submodule
  sync  "auto"      : hash-tracked by baseline_sync.py in consumer projects
  sync  "seed"      : copied at bootstrap, thereafter project-owned (watch-only)

A consumer subscribes to a layer *prefix* of core -> code -> godot -> jmodot
(bootstrap --layers). Layer assignment has NO fallback: every template file must
match exactly one layer's pattern list, or generation fails loudly. The old
default-to-universal fallthrough is what let ~100 files mistag silently.

Run from the baseline repo root after adding/removing template files:
  python3 tools/gen_manifest.py
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
    ".claude/settings.json",
    ".claude/worklog-titles.md",
    ".claude/skills/game_vision/*",
    ".claude/skills/project_subsystems/*",
    ".claude/commands/checklists/known_failure_modes.md",
]

JMODOT_PATTERNS = [
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
    # Jmodot-specific items the old universal fallback mistagged:
    ".claude/auto-memory/gotcha_config_exception_node_bound_not_static.md",
    ".claude/auto-memory/gotcha_getfirstchildof_logs_error_on_miss.md",
    ".claude/auto-memory/gotcha_authoring_validator_needs_static_resolvable_reference.md",
    ".claude/commands/doc_workflow_battery.md",
]

GODOT_PATTERNS = [
    ".claude/skills/testing/*",
    ".claude/skills/refactor_procedure/*",
    ".claude/skills/sprite_authoring/*",
    ".claude/skills/shader_authoring/*",
    ".claude/skills/parameterized_asset_pipeline/*",
    ".claude/skills/game_vision/*",
    ".claude/rules/csharp_*.md",
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
    ".claude/tools/csharp-ls-adapter.js",
    ".claude/tools/setup-csharp-ls.sh",
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
]

# Any programming project, but not content production: build/test/PR machinery,
# plan/roadmap pipeline, tool-routing hook family, code-architecture doctrine.
CODE_PATTERNS = [
    ".claude/skills/architecture_brainstorm/*",
    ".claude/skills/architecture_philosophy/*",
    ".claude/skills/debugging/*",
    ".claude/skills/project_subsystems/*",
    ".claude/hooks/pre_read_dispatch.py",
    ".claude/hooks/post_read_dispatch.py",
    ".claude/hooks/routing_audit.py",
    ".claude/hooks/routing_classifier.py",
    ".claude/hooks/tool_routing_*.py",
    ".claude/commands/mvp_plan.md",
    ".claude/commands/part_drive.md",
    ".claude/commands/part_execute.md",
    ".claude/commands/plan_check.md",
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
    ".claude/commands/clean_pull.md",
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
]

# Fully domain-agnostic — proven portable to a non-code content-production
# harness verbatim or with {{PLACEHOLDER}} substitution (two-domain rule).
CORE_PATTERNS = [
    ".claude/CLAUDE.md",
    ".claude/settings.json",
    ".claude/worklog-titles.md",
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
    ".claude/hooks/critical_analysis_reminder.py",
    ".claude/hooks/file_size_preblock.py",
    ".claude/hooks/json_merge.py",
    ".claude/hooks/log_instruction_loads.py",
    ".claude/hooks/plan_memory_reminder.py",
    ".claude/hooks/prompt_git_state_delta.py",
    ".claude/hooks/prompt_memory_loader.py",
    ".claude/hooks/transcript_backup.py",
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
    ".claude/tools/extract_subagent_tools.py",
    ".claude/workflows/*.js",
]

# Layer precedence: most specific wins (a file matching two lists is assigned
# the most specific layer, so broad globs in core/code can't swallow a
# godot/jmodot file).
LAYER_ORDER = [
    ("jmodot", JMODOT_PATTERNS),
    ("godot", GODOT_PATTERNS),
    ("code", CODE_PATTERNS),
    ("core", CORE_PATTERNS),
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


def main() -> None:
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
        sys.exit(1)
    manifest = {"version": 2, "files": files}
    (ROOT / "baseline.manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for f in files:
        counts[f["layer"]] = counts.get(f["layer"], 0) + 1
    print(f"{len(files)} files — " +
          ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    print("verify separation: python3 tools/audit_baseline.py")


if __name__ == "__main__":
    main()
