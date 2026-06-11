# Memory Index

> Auto-loaded at SessionStart (first 200 lines OR 25KB).
> One line per memory file, ≤150 chars — the hook TRIGGERS recall; mechanism lives in the topic file. Add a pointer when writing a new file.
> Organized by topic — file each new entry under its section.

## Communication & process discipline
- [Honor execution directive](feedback_honor_execution_directive.md) — "execute here" is final; don't re-ask continue-vs-handoff. Safety gates still pause.
- [Fix self-introduced regressions immediately](feedback_fix_self_introduced_regression_immediately.md) — consciously-added regression → fix in-session, never park.
- [User distress lexicon — STOP signal](feedback_user_distress_lexicon.md) — ALL CAPS / 'WHAT' / '????' → STOP; acknowledge + ask, no fix same turn.
- [No performative agreement](feedback_no_performative_agreement.md) — don't open with sycophantic agreement; restate, verify, or fix it.
- [No unilateral condensation](feedback_no_unilateral_condensation.md) — chat content IS the file spec; port 1:1, never silently digest.
- [Don't unilaterally reduce planned scope](feedback_dont_unilaterally_reduce_planned_scope.md) — plan is the contract; scope cuts need explicit user re-authorization.
- [Doc revision in place](feedback_doc_revision_in_place.md) — rewrite affected sections in body; never bury corrections as v1.1 addendums.
- ["Recommended fix" means implement](feedback_recommended_fix_means_implement.md) — default to shipping in-session; deferral needs explicit justification.
- [Don't defer immediately-addressable work](feedback_dont_defer_immediately_addressable.md) — scope-1 + harmless → DO IT NOW; worklog is for derail/judgment/later-info.
- [Plan worklog items from source, not the mirror](feedback_plan_worklog_items_from_source_not_mirror.md) — read the item's Context block; title-only mirror misleads on scope.
- [Hook ≠ skill-procedure license](feedback_session_start_hook_does_not_override_skill_procedure.md) — governs extra-procedural pauses; Socratic/TDD gates stay mandatory.
- [Slash command naming](feedback_slash_command_naming.md) — scan existing prefixes; convention-aligned names first.
- [Command descriptions stay to one line](feedback_command_descriptions_one_line.md) — line 1 ~90 chars action-first; slash-invoked, no auto-trigger.
- [Skill vs command frontmatter convention](feedback_skill_vs_command_frontmatter_convention.md) — skills `description: >-` multi-line; commands single-line.
- [Memory file refs — bare code-styled](feedback_memory_file_refs_no_markdown_links.md) — cite as bare `` `name.md` `` not `[link]` (except this index's links).
- [Precedent is evidence, not authority](precedent_is_evidence_not_authority.md) — verify code precedent against skills/rules/CLAUDE.md before matching; guideline wins.

## Planning, brainstorm & handoff discipline
- [Resolve plan questions at plan-time](feedback_resolve_questions_in_plan_not_execution.md) — deferred decisions stall the executor or ship as guesses; Verify > ASK.
- [/plan_check auto-surface on ExitPlanMode](feedback_plan_check_auto_surface_on_exit.md) — invoke pre-exit when litmus met (3+ files / new type / family refactor / deletions).
- [Plan Mode is a Claude Code built-in](feedback_plan_mode_is_claude_code_built_in.md) — local skills/commands describe handoff, never internals.
- [Spec-Doc Coverage subsection before plan-mode exit](process_rule_spec_doc_coverage.md) — map design-doc mechanics → plan section; missing rows = defer or pull in.
- [Plan xhigh, execute lower via fresh session](process_rule_plan_high_execute_lower.md) — plan file as handoff seam; junior-engineer litmus before fresh-handoff.
- [Rich context ≠ skip-steps license](feedback_dont_compress_socratic_on_rich_prompt.md) — briefing is starter material, NOT license to skip Socratic/Plan gates.
- [Plan-pending needs impl-arch, not just a seam](feedback_plan_pending_requires_impl_arch_not_seam.md) — locked seam ≠ designed implementer; seam-only → arch-pending.

## Architectural & design rules
- [switch(type) = CLOSED-SET intent](arch_rule_closed_set_switch.md) — identical-behavior cases mean the TYPE is the smell; delete the type, don't add the case.
- [TransitionCondition Resources must be stateless](arch_rule_transition_condition_stateless.md) — no latch fields, no cached subs; Check is pure of (agent,bb) + Exports.
- [Shared Resource holds zero per-consumer state](arch_rule_resource_config_runtime_split.md) — config-Resource + per-consumer runtime (CreateRuntime); shared .tres cross-stomps; rejects resource_local_to_scene.
- [BB flags = HSM-transition-only](arch_rule_bb_flag_cross_system.md) — cross-system signaling routes via events/component-state/physics, never BBDataSig.Flag=true.
- [Gameplay geometry matches input dimensionality](arch_rule_constraint_scope_match_input_axis.md) — 1D input → 1D snap; N+1 dims break the player's control model.
- [OnExit must not clobber consumer reads](arch_rule_onexit_must_not_clobber_consumer_onenter.md) — OnExit runs before consumer OnEnter; clearing BB there kills the snapshot.
- [Hysteresis at .tres for analog HSM boundaries](arch_rule_hysteresis_for_analog_hsm_boundaries.md) — asymmetric Min/Max per pair; 10% deadband kills stick-noise flicker.
- [Windowed-history → Component+Condition split](arch_rule_windowed_history_component_condition_split.md) — positive shape of stateless TransitionCondition; CombatLogger precedent.
- [Pragmatic narrowing at boundary](arch_rule_pragmatic_narrowing_at_boundary.md) — when "correct" refactor cascades >5x, bounded adapter at <5 sites with anchored seam.
- [Typed component property over BB-bool flag soup](feedback_typed_state_over_bb_flag_soup.md) — typed `{get;private set;}` + dedicated TransitionCondition.
- [ErrorsOnly build hides cref drift](gotcha_errorsonly_build_hides_cref_drift.md) — mutes CS1574; `<see cref>` to a removed member ships green; grep after removal.
- [Config exceptions are node-bound](gotcha_config_exception_node_bound_not_static.md) — NodeConfigurationException needs a Node; pure code throws CLR, rewrap at the Node boundary.
- [Inspect existing abstractions first](feedback_inspect_existing_abstractions_first.md) — extending a 2+ subclass family beats inventing parallel types.
- [Autoload line earns its place](arch_rule_autoload_line_earns_its_place.md) — default to a global.tscn child; own [autoload] line only when boot order is load-bearing.
- [Prefer typed shapes over empty markers](feedback_prefer_typed_shapes_over_empty_markers.md) — no members-less marker interfaces; lead with concrete typed fields/records.
- [Prefer data params over injected delegates](feedback_prefer_data_params_over_injected_delegates.md) — pass values/typed pairs as data; reserve Func<> for pure projections.
- [Reconcile against existing subsystems](feedback_reconcile_structure_against_existing_subsystems.md) — map concerns onto existing subsystems + roadmaps before inventing folders.
- [Refactor parity audit before merge](feedback_refactor_parity_audit.md) — line-by-line behavior diff old→new; "deferred/stub/TODO" markers are merge-blockers.
- [Don't defer existing framework abstractions](feedback_dont_defer_existing_framework_abstractions.md) — grep BBDataSig + Jmodot.Core before saying "X varies per project".

## Testing & TDD
- [Godot signal tests via EmitSignal](feedback_godot_signal_test_via_emitsignal.md) — in Logic tests (no SceneTree), fire lifecycle signals via EmitSignal; tree mutation won't.
- [Test namespace must match gate filter](arch_rule_test_namespace_matches_gate_filter.md) — tests must live under Tests/Logic|Integration|Sanity w/ matching ns, or gate skips them.
- [GdUnit4 filter uses test class FQN](gotcha_gdunit4_filter_uses_test_class_name.md) — filter on `<TestClassName>.<method>`, not the production class under test.
- [Cached BB-dependency defeats post-init injection](gotcha_component_caches_bb_dependency_post_init_injection_noop.md) — BB dep cached at Initialize → #if TOOLS setter for E2E inject.
- [Schema bump breaks version-pinned tests](gotcha_schema_version_bump_breaks_version_tests.md) — Load migrates to current; grep tests for the old version literal; full suite catches.

## Tool routing & workflow
- [semantic-search restrictToDir is posix](gotcha_semantic_search_restricttodir_posix.md) — repo-relative posix path; absolute silently returns 0. Verify absence unrestricted.
- [Tool routing — specialized MCP > default](feedback_tool_routing_discipline.md) — ai-worker, LSP, semantic-search vs Read/Grep. Read/Grep is wrong by habit.
- [LSP default for C# symbol queries](feedback_lsp_default_for_csharp.md) — grep only for .tscn/.tres/StringName/anchor discovery.
- [read_files enumerate first, no directory paths](feedback_read_files_enumerate_first.md) — glob to concrete paths before bundling; directory passing fails silently.
- [read_files N≥4 needs completeness directive](feedback_read_files_multifile_completeness_directive.md) — extraction silently omits files without "return one per input path".
- [read_files output volume governs spill](feedback_read_files_output_volume_governs_spill.md) — OUTPUT chars drive spill (≠ truncation); manifest is truncation-aware; cap is global per call.
- [Verify Explore agent empirical claims](feedback_verify_explore_agent_empirical_claims.md) — 1-grep prior-art check before an agent claim shapes decisions; agents err confidently.
- [Send verbatim content to review agents](feedback_verbatim_content_to_review_agents.md) — never abbreviate code in an audit CONTEXT (agents flag it as defects); shared `contextPrefix` for size.
- [Invoke the named slash-command](feedback_invoke_named_skill_not_manual_equivalent.md) — canonical artifacts (verdict header, tiered findings) matter; manual subs lose them.
- [Session-end command over passive nudge](feedback_session_end_command_over_passive_nudge.md) — registry-drift: /session_end-conditional + Step 0 git-diff gate, not PostToolUse stderr.
- [Session-end pipeline must scope to full session](feedback_session_end_full_scope.md) — audit/autolearn/self_evaluate use full-session delta, not recent slice.
- [MEMORY.md is agent-maintained](feedback_memory_md_is_auto_managed.md) — write topic file + add MEMORY.md pointer in same turn; no separate hooks/workflows.
- [Separate pre-existing changes before commit](feedback_separate_preexisting_changes_before_commit.md) — bulk-mechanical commits: edit-signature detector isolates your changes.
- [Autonomous loop needs positive liveness](arch_rule_autonomous_loop_positive_liveness.md) — convergence loops must prove each lens RAN; never read "0 findings" as success.

## Jmodot framework
- [Jmodot 2D Movement Architecture](jmodot_2d_movement_architecture.md) — MovementProcessor2D, shared BBDataSig, TurnRateProfile3D rename, HitContext2D bridge.
- [Jmodot CombatFactoryDefaults seam](jmodot_combat_factory_defaults_seam.md) — 6 factories resolve project defaults via static seam wired from PP autoload.
- [Jmodot framework boundary rule](jmodot_framework_boundary_rule.md) — Jmodot must not reference `PushinPotions.*`; static seam pattern; no temp-violation carve-outs.

## Godot / scene / data / lifecycle
- [Inherited scene leaves resource slots empty](gotcha_inherited_scene_empty_resource_slot.md) — base .tscn sub-resource unfillable by inheritors; instance the concrete leaf.
- [Export enum out-of-range silent false](gotcha_export_enum_out_of_range_silent_false.md) — .tres stores raw int after enum collapse; comparisons always false, build green.
- [Editor resave null-strips value-type Exports](gotcha_editor_reserialize_value_export_null_strip.md) — bulk resave writes `=null` → type-zero; breaks `>=`; grep after resave commits.
- [Godot Variant Int64 fold gotcha](gotcha_godot_variant_int64_fold.md) — generic-object fold receives boxed `long`, not `int`. Cast to `long` first; `(int)v` throws.
- [Blackboard StringName-via-Variant boxing](gotcha_blackboard_stringname_variant_boxing.md) — StringName payloads return as distinct wrappers; compare by value.
- [Blackboard null-storage Set/TryGet asymmetry](Blackboard_NullStorage_Asymmetry.md) — `bb.Set<T>(key,null)` for ref T stores `Variant.Nil`; `TryGet` returns false.
- [PackedScene inlines pathless Resource Exports](arch_rule_packedscene_resource_inline_copy.md) — Pack serializes a pathless `[Export]` Resource as a copy; re-propagate post-Instantiate.
- [Atomic-rename tmp is durable](arch_rule_atomic_rename_tmp_is_durable.md) — write-tmp → delete-dest → rename: failure ON rename means tmp IS the new state; don't clean it.
- [ResourceSaver extension dispatch](gotcha_godot_resourcesaver_extension_dispatch.md) — `foo.tres.tmp` fails FileUnrecognized; insert `.tmp` BEFORE the ext → `foo.tmp.tres`.
- [Namespace rename breaks relative using](gotcha_namespace_rename_breaks_relative_using.md) — moving a ns breaks bare `using X;` → misleading CS0535; fully-qualify first.
- [Type==namespace-leaf collision](gotcha_type_name_equals_namespace_leaf.md) — class==ns-leaf → bare type ref binds to the namespace (CS0118); rename the type.
- [Base-member DIM needs explicit form](gotcha_default_interface_method_for_base_member.md) — `Type IBase.Member()=>…` else CS0535; explicit default is interface-access-only.
- [Explicit DIM orphans on interface removal](gotcha_explicit_dim_orphans_on_base_interface_removal.md) — `IFoo.Member=>…` → CS0540 when IFoo leaves the base list; grep explicit-DIMs first.
- [Godot typed Dictionary export gotchas](gotcha_godot_typed_dictionary.md) — `Dictionary<Resource,V>` [Export]: `.tres` literal form + null-key from empty slot; scene-load test.
- [uid-only refs break orphan detection](gotcha_godot4_uid_only_refs_break_orphan_detection.md) — Godot-4 uid-only ext_resource → text orphan sweeps over-count; verify in editor.
