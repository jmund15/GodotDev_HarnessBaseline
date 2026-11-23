"""S4 classifier. Every tracked .claude/ path of one target gets exactly one class.

Classes: shared, <repo>-local (pp-local / mm-local), delete, bench-moved.
conf: named (a V row or T-part names the file), family (a V row names the family, not this file),
      derived (follows from a named file: its test, fixture, or a helper a kept file imports),
      lock (only the repo's lock row decides), unplaced (kept by default and listed for the owner).
"""
import json
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, "C:/Users/jmund/Game_Dev/Godot_Projects/PushinPotions/.claude/worktrees/harness-v2-plan/.claude/scratch/v2_s4")
import common

D, S, BM = "delete", "shared", "bench-moved"

# ---- named sets (regex on the path below .claude/) -> (class, row, note) ------------------------
# bench-moved: hsr P2 move set (S4: classify as bench-moved whatever of it is still tracked)
BENCH = [
    r"^scripts/benchmark_campaign/", r"^scripts/sdf_suite/", r"^skills/benchmark_design/", r"^benchmark_runs/",
    r"^guards/bench-score\.md$", r"^hooks/bench_arm_escape_guard\.py$",
    r"^tests/test_bench_", r"^tests/_bench_", r"^tests/fixtures/(bench|benchmark)",
    r"^plans/(bench-instrument-fix|t2q-ranker-pages|bench-lifecycle-proof-2026-09-24|bench-low-confidence-adjudication|t1-offkey-credit|t1-session-ab|t2e-battery-pilot)\.md$",
    r"^workflows/(effort_on_agenttype_probe|score_cell|bench_[a-z_]+|benchmark_[a-z_]+)\.js$",
]

NAMED_DELETE = [
    # V7 external-provider dispatch (replaced by seat.sh, seats.json, workflow_transport.py)
    (r"^tools/(sidecar_fanout|model_registry|free_pool|watch_records|dispatch_result|dispatch_size|wake_digest|sidecar_argv)\.py$", "V7", ""),
    (r"^tools/(quota_[a-z_]+|provider_[a-z_]+|spend_[a-z_]+)\.py$", "V7", ""),
    (r"^hooks/(session_model_rails|budget_posture|dispatch_table|amend_delivery|foreground_wait_guard|workflow_provider_guard|dispatch_mechanism_guard|model_ladder_gate|provider_capacity_guard)\.py$", "V7", ""),
    (r"^hooks/(sidecar_dispatch_context|sidecar_recompact_reprompt|sidecar_watch_rearm)\.py$", "V7", "S1 Function group, replaced at cutover"),
    (r"^scripts/lib/sidecar_common\.sh$", "V7", ""),
    (r"^scripts/[a-z_]+_sidecar\.sh$", "V7", "the *_sidecar.sh launchers"),
    (r"^(tools|hooks|scripts)/agy_(?!seat|workspace|hook_bridge)[a-z_]+\.(py|sh)$", "V7", "agy_* except the Antigravity set"),
    # V8 review machinery
    (r"^commands/plan_check\.md$", "V8", ""), (r"^commands/agents/(plan_check_agents|review_agents)\.md$", "V8", ""),
    (r"^tools/(review_ledger|lens_briefs|fold_check|post_check|verify_claims|citers)\.py$", "V8", ""),
    (r"^workflows/review_fanout\.js$", "V8", ""),
    (r"^workflows/dispatch_chains\.js$", "T4", "placed by usage: 4 Workflow calls in 30 days against 426 for dispatch.js and 55 for explore_fanout.js; the owner decides"),
    # V9 proof runner and slots
    (r"^scripts/harness_tests\.py$", "V9", ""), (r"^scripts/_proof_runner/", "V9", ""), (r"^scripts/_read_recorder/", "V9", ""),
    (r"^scripts/_slot_bootstrap/", "V9", ""), (r"^tools/gate_growth\.py$", "V9", ""),
    (r"^hooks/(wide_test_guard|gate_cadence_guard)\.py$", "V9", ""),
    # V10 baseline sync
    (r"^tools/(baseline_sync|baseline_publish|baseline_compose|baseline_identity|baseline_linux_preview|layer_closure|lock_merge)\.py$", "V10", ""),
    (r"^baseline\.lock\.json$", "V10", ""), (r"^commands/sync_baseline\.md$", "V10", ""),
    (r"^hooks/(baseline_classification_guard|composed_output_guard)\.py$", "V10", ""),
    (r"^settings\.(base|coding|godot|project)\.json$", "V10", "settings.json becomes hand-owned"),
    (r"^skills/project_subsystems/adaptation\.(pure|coding|godot)\.defaults\.json$", "T7", "layer defaults merge goes"),
    (r"^skills/project_subsystems/adaptation\.coding\.json$", "T7", "MaddManager layer override; keys fold into adaptation.json"),
    # V11 self-governance and metrics
    (r"^instruction_rows/", "V11", ""), (r"^tools/(extract_numbers|restate_scan|rule_retirement|load_census|mechanism_census|orchestration_metrics|guard_denials|memory_hits|triage|escape_log|autolearn_outcomes|self_eval_archive_store)\.py$", "V11", ""),
    (r"^hooks/(rule_number_guard|provenance_label_guard|harness_growth_guard|harness_edit_skill_reminder|model_name_guard|self_eval_archive_guard|retire_trigger_advisory|denial_coach|log_instruction_loads|skill_load_marker|memory_hits_logger|critical_analysis_reminder|task_brief_facts|triage_commit_check|plan_memory_reminder|plan_job_map_guard|duplicate_commit_check|design_surface_reminder|prompt_memory_loader)\.py$", "V11", ""),
    (r"^hooks/(routing_[a-z_]+|tool_routing_[a-z_]+)\.py$", "V11", ""),
    (r"^hooks/(readonly_lens_write_guard|readonly_marker_arm|hand_pricing_advisory|harness_shell_write_guard|rail_probe_guard|activity_registry|shell_census)\.py$", "S1", "policing group unwired at M1, deleted at cutover"), (r"^tools/routing_[a-z_]+\.py$", "V11", ""),
    (r"^hooks/model_name_exemptions\.json$", "V11", ""),
    (r"^(orchestration_metrics|autolearn_outcomes|census_reviews|escape_log)\.[a-z.]+$", "V11", ""),
    (r"^self_evaluate_archive\.", "V11", "self_eval*"), (r"^scripts/autolearn\.sh$", "V11", ""), (r"^commands/autolearn\.md$", "V11", ""),
    (r"^commands/self_evaluate\.md$", "V11", "self_eval*"),
    # V12 session lifecycle
    (r"^commands/(session_end|session_digest|commit_push|clean_push)\.md$", "V12/T3", "replaced by /close"),
    (r"^tools/(session_digest|task_record)\.py$", "V12", ""), (r"^scripts/roadmap_atlas\.py$", "V12", ""), (r"^commands/roadmap_atlas\.md$", "V12", ""),
    (r"^hooks/(compact_directive_ledger|subagent_compaction_rail|_compaction_rail|_compaction_record)\.py$", "V12", "compaction ledger hooks"),
    # T3 commands that /drive replaces
    (r"^commands/(part_drive|feature_drive|part_execute|plan_handoff|design_drive)\.md$", "T3", "replaced by /drive"),
    # V17 client-quirk repairs (delete at cutover unless S4's crafted call reproduces the defect)
    (r"^hooks/(bash_backslash_fidelity|approving_gates|file_size_preblock|indexed_reference_guard|unbounded_scan_guard|semantic_search_scope_guard|video_read_hint)\.py$", "V17", "kept only if the crafted call reproduces the defect"),
    # T1/T4/T5: named replaced files
    (r"^hooks/pre_bash_dispatch\.py$", "T1", "becomes _floor.py plus the pre_bash.py stub"),
]

# kept, named: (regex, class, row, note). class 'L' = the target repo's local class.
NAMED_KEEP = [
    # V1 loss floor (T1)
    (r"^hooks/(dangerous_shell_guard|git_guardrails|_peer_loss|kill_guard|_regenerable_clone|transcript_backup|_git_run|_git_commit|_shell_writes|sidecar_workdir_guard|agy_hook_bridge)\.py$", S, "V1/V16", "floor"),
    (r"^tools/(index_commit|reap|agy_seat|agy_workspace)\.py$", S, "V1/V7", "floor"),
    (r"^scripts/lib/json_schema_lite\.py$", S, "V7", "agy_seat import"),
    (r"^githooks/", S, "T1", "commit-msg repointed to --commit-msg"),
    # V2 Godot floor (PP-local)
    (r"^hooks/(tres_[a-z_]+_guard|editor_live_build_guard|runtime_mcp_worktree_guard|merge_build_check)\.py$", "L", "V2", "Godot floor"),
    # V3 / V5 MaddManager
    (r"^hooks/convex_prod_target_guard\.py$", "L", "V3", "MaddManager floor"),
    (r"^(skills/(ui_lock|product_ui)/|rules/(product_ui|e2e_authoring)\.md$|commands/agent_playtest\.md$|tools/(ui_ledger|ui_round|round_close|reach_run)\.py$|hooks/(_wide_runs|ui_round_amend_guard)\.py$)", "L", "V5", "MaddManager product tooling"),
    # V4 game support (PP-local)
    (r"^hooks/(refcounted_free_guard|pattern_enforcer|check_logger_tag_prefix|prototype_containment_guard|provisional_totality_guard|test_suite_gate_coverage_guard|floorcell_mutation_seam_guard|trail_mutation_seam_guard|duplicate_test_double_guard|_seam_guard|_cs_files|uid_cache_audit|analyze_godot_logs|godot_source_pins|generate_family_manifest)\.(py|json)$", "L", "V4", "game-code guard"),
    (r"^scripts/(verify|regression_gate|IntegrationBatches|run_test_suite|run_integration_batched|batch_diagnosis|engine_run|gdunit_recompile\.pin|godot_bin|godot_docs_cache|fetch_source|migrate_statmodifier_arrays|parse_trail_prof|engine_userdata|CSharpSource|CommentOnlyGate|EngineErrors|GodotProcess|StaticGuards|TreeDigest|TwinCheckout|WideRunGuard|RunPool|RuntimeSlots|PeerActivity|review_floor|gate_domain_map)\.", "L", "V4", "game build and test"),
    (r"^scripts/(audio_[a-z_]+|freesound_[a-z_]+|procgen_sim[a-z_.]*|engine_src/|fetch_source\.d/)", "L", "V4", "audio, procgen, docs cache"),
    (r"^tools/(analyze_godot_logs|gen_godot_class_index|csharp-ls-adapter|godot_mcp_launch|setup-csharp-ls|tree-sitter-c_sharp|type_decls|void_check|audit_test_accessors|playtest_checklist_lint)", "L", "V4", "game support"),
    (r"^commands/(regression_gate|analyze_godot_logs|procgen_sim|spell_arch_audit|spell_balance_audit|playtest_artifact|audit_test_accessors|pr_test_checklist|test_compact|structure_audit|update_project_stack|content_integrity)\.md$", "L", "V4", "game command"),
    # V16 kept helpers (shared)
    (r"^hooks/(compound_cd_approver|workflow_script_lf_guard|bash_length_guard|subagent_dispatch_guard|runaway_scan_reaper|peer_message_guard|overnight_ask_guard|login_parity_check|run_call_router|session_context_loader|session_context_loader_godot|session_context_loader_csharp|pre_workflow_dispatch|compact_directive_anchor)\.py$", S, "V16/T4/T5", "kept (trimmed or rewritten)"),
    (r"^tools/(vault_read|vault_sync|python_outline|yt_transcript|adaptation|verify_doc_citations|machine_slots|brief_block|quote_check|harness_sync)\.py$", S, "V16/T6/T7", "kept"),
    (r"^tools/vault_sync\.project\.json$", "L", "V16", "project config of vault_sync"),
    (r"^scripts/(video_frames|sync_plan_config)\.py$", S, "V16", "kept"),
    (r"^hooks/(semantic_search[a-z_]*|_semantic[a-z_]*)\.py$", S, "V16", "semantic-search hooks"),
    (r"^tools/semantic_search_[a-z_]+\.(py|mjs)$", S, "V16", "semantic-search tools"),
    # T4 engines and T3 shared commands / skills
    (r"^workflows/(dispatch|explore_fanout)\.js$", S, "T4", "kept Workflow engine"),
    (r"^workflows/explore_fanout\.schema\.json$", S, "T4", "explore_fanout schema"),
    (r"^commands/(explore|delegate|overnight|codify|worklog|reindex_search)\.md$", S, "T3", "shared command"),
    (r"^commands/(agents/explore_agents|reference/codify_[a-z_]+|reference/worklog_[a-z_]+)\.md$", S, "T3", "command reference"),
    (r"^skills/(orchestration|testing|debugging|architecture_philosophy|instruction_quality|change_control|wizard)/", S, "T3", "shared skill"),
    (r"^rules/design_litmus\.md$|^reference/rules/design_litmus\.md$", S, "T3", "shared coding rule"),
    (r"^agents/(pp|lane)-[a-z-]+\.md$", S, "T4", "agent definitions (project-prefixed names are an S6b rename)"),
    (r"^guards/(common|author|review|survey|single_flight|tool_profiles)[a-z._]*(md|json)$", S, "T4", "delegate rails (rewritten to 3 KB by S6a)"),
    (r"^templates/workflows/", S, "T6/Q7", "workflow templates; main_check.yml and land.yml are edited at cutover"),
    (r"^scripts/(main_check|land_run)\.(py|ps1)$", "L", "T6", "main-check runner, edited at cutover"),
    (r"^sessions/", "L", "V15", "session notes"),
]

DEL_FAMILY = [
    (r"^tools/(sidecar_[a-z_]+)\.py$", "V7", "sidecar_* family"),
    (r"^scripts/(codex|opencode|anthropic|deepseek|console_credit|go_quota|agy)_[a-z_]+\.(py|sh)$", "V7", "provider probes and launchers"),
    (r"^scripts/(opencode_refresh_models|anthropic_capacity_probe)\.py$", "V7", "provider tooling"),
    (r"^scripts/lib/(cli_envelope|ccp_probe|oc_proxy_probe|text_only_shim|below_normal_shell)\.(py|sh)$", "V7", "provider lib"),
    (r"^tools/(cli_usage|usage_report|usage_samples|plan_usage_calc|worker_rate|model_tier_readout|run_cost|exhausted_marker|launch_memory|ladder_ingest|ladder_prose_check|ollama_contention_probe|ai_worker_metrics|dispatch_take|delegate_prepare|battery_children|rail_battery|score_routing_battery|aggregate_routing_audit|agent_progress|stream_events|workflow_replay|tool_profiles|extract_subagent_tools|verify_transport_status)\.(py|md|mjs)$", "V7/V11", "external-provider and metrics family"),
    (r"^scripts/rail_battery/", "V11", "rail battery (a measurement)"),
    (r"^tools/(review_calibration|review_result|review_snapshot|finding_bar|citer_coverage|eval_[a-z_]+|analyze_eval_archive|gate_trend|spend_[a-z_]+)\.py$", "V8/V11", "review and eval family"),
    (r"^commands/(agents/(plan_check_agents|review_agents_godot|review_fold|redteam_agents|session_audit_agents)|reference/(plan_check_detail|session_audit_[a-z]+|session_end_main_check|sync_baseline_maintainer|harness_prune_census|ladder_board_sweep|update_roadmap_[a-z]+|eval_analysis))\.md$", "V8/V11/V12", "doctrine of deleted commands"),
    (r"^commands/(eval_dashboard|harness_prune|instruction_audit|ladder_ingest|model_ingest|orchestration_metrics|quota_dashboard|rail_battery|routing_audit|routing_battery|session_audit|session_refresh|update_roadmap|worker_audit|rule_consistency|plan_check|overnight_review)\.md$", "V8/V11/V12", "command of deleted machinery"),
    (r"^skills/orchestration/reference/(budget_posture|spend_lean|model_evidence|slice_pins|effort)\.md$", "V7", "orchestration reference on deleted machinery"),
    (r"^reference/(model_ladder_evidence|external_models[a-z._0-9-]*|opencode_[a-z_]+|codex_session_runbook|sidecar_dispatch|sidecar_runs|context_attribution_[0-9-]+|known_failure_modes_entries|reproduce_review_dedup)\.[a-z.0-9-]+$", "V7/V11", "reference of deleted machinery"),
]

# doctrine directories placed by V13 (kept, simplified by S6a) when no rule above names the file
DOCTRINE = re.compile(r"^(commands|skills|rules|reference|guards|agents|templates|schemas|user_scope|workflows)/")
CODE_DIRS = re.compile(r"^(hooks|tools|scripts)/")


def rel(p):
    return p[len(".claude/"):]


def first(rules, r):
    for pat, *rest in rules:
        if re.search(pat, r):
            return (pat, *rest)
    return None


def stem(r):
    b = r.rsplit("/", 1)[-1]
    return b.rsplit(".", 1)[0]


def kept_cls(c, local):
    return local if c == "L" else c


def lock_default(r, lk, local):
    row = lk.get(".claude/" + r)
    if row is None:
        return local, "no lock row"
    layer, status = row.get("layer"), row.get("status")
    if status == "local" or layer in (None, "project"):
        return local, f"lock {layer}/{status}"
    if layer == "godot":
        return (local if local == "pp-local" else "delete"), f"lock godot/{status} (K6: Godot files are PushinPotions-local)"
    return S, f"lock {layer}/{status}"


def classify(key):
    t = common.TARGETS[key]
    local = t["local"]
    files = common.file_list(key)
    lk = common.lock(key)["files"]
    txt = common.texts(key)
    res = {}

    def put(p, c, row, conf, note=""):
        res[p] = {"cls": c, "row": row, "conf": conf, "note": note}

    # pass 1: non-test, non-fixture paths
    for p in files:
        r = rel(p)
        if r.startswith("auto-memory/"):
            put(p, local, "V14", "named", "memory; archive pass at S10 moves any that describe deleted machinery")
            continue
        if r.startswith("plans/"):
            m = first([(b,) for b in BENCH], r)
            if m:
                put(p, BM, "V6", "named", "hsr P2 move set")
            else:
                put(p, local, "V15", "named", "plan file; archive per §In-flight work at S10")
            continue
        if first([(b,) for b in BENCH], r):
            put(p, BM, "V6", "named", "hsr P2 move set")
            continue
        m = first(NAMED_DELETE, r)
        if m:
            put(p, D, m[1], "named", m[2])
            continue
        m = first(NAMED_KEEP, r)
        if m:
            put(p, kept_cls(m[1], local), m[2], "named", m[3])
            continue
        m = first(DEL_FAMILY, r)
        if m:
            put(p, D, m[1], "family", m[2])
            continue
    # pass 2 tests, fixtures
    code_class = {}
    for p, v in res.items():
        r = rel(p)
        if CODE_DIRS.match(r) or r.startswith("workflows/") or r.startswith("skills/") or r.startswith("commands/"):
            code_class[stem(r)] = v["cls"]
    # code stems also for unplaced code; resolved after closure. Names to match tests against:
    mod_stems = {}
    for p in files:
        r = rel(p)
        if (CODE_DIRS.match(r) or r.startswith("workflows/")) and not r.startswith("scripts/rail_battery/") and not r.startswith("scripts/benchmark_campaign/"):
            mod_stems.setdefault(stem(r), p)

    def subject_of(test_path):
        s = stem(rel(test_path))
        s = re.sub(r"^test_", "", s)
        s = re.sub(r"_test$", "", s)
        parts = s.split("_")
        for n in range(len(parts), 0, -1):
            cand = "_".join(parts[:n])
            if cand in mod_stems:
                return mod_stems[cand]
            if "_" + cand in mod_stems:
                return mod_stems["_" + cand]
        return None

    def referenced(test_path):
        body = txt.get(test_path, "")
        hits = []
        for s_, p_ in mod_stems.items():
            if len(s_) >= 8 and re.search(r"(?<![A-Za-z0-9_])" + re.escape(s_) + r"(?![A-Za-z0-9_])", body):
                hits.append(p_)
        return hits

    pending_tests = []
    for p in files:
        r = rel(p)
        if p in res:
            continue
        if r.startswith("tests/"):
            pending_tests.append(p)

    # pass 3 non-test code and doctrine
    for p in files:
        r = rel(p)
        if p in res or r.startswith("tests/"):
            continue
        if re.match(r"^hooks/(pre_edit|post_edit|pre_read|post_read|prompt)_dispatch\.py$", r):
            c, why = lock_default(r, lk, local)
            put(p, c, "S1/T1", "unplaced", "dispatcher: its policing members go, and T1/T4 name no successor entry for edit, read or prompt events; members left after V11 decide (S6b)")
            continue
        if DOCTRINE.match(r):
            c, why = lock_default(r, lk, local)
            put(p, c, "V13", "lock", f"doctrine kept and simplified by S6a unless its rules go; {why}")
            continue
        if CODE_DIRS.match(r):
            c, why = lock_default(r, lk, local)
            put(p, c, "-", "unplaced", f"no V row names it; {why}")
            continue
        c, why = lock_default(r, lk, local)
        # top-level .claude files
        if r in ("CLAUDE.md", "CLAUDE.core.md", "CLAUDE.coding.md", "CLAUDE.godot.md", "README.md"):
            put(p, c, "T2", "named", "doctrine entry file")
        elif r in ("settings.json",):
            put(p, local, "V10", "named", "hand-owned per repo after cutover")
        elif r.startswith("worklog") or r.startswith("searchignore") or r in ("package.json", "index_composition_baseline.json"):
            put(p, c, "-", "unplaced", f"no V row names it; {why}")
        else:
            put(p, c, "-", "unplaced", f"no V row names it; {why}")

    # closure: an unplaced helper that a kept non-test file imports or names is kept (conf derived)
    kept_cls_set = {S, local}
    changed = True
    code_files = [p for p in files if (CODE_DIRS.match(rel(p)) or rel(p).startswith("workflows/")) and rel(p).endswith((".py", ".js", ".mjs", ".cjs", ".sh", ".ps1"))]
    while changed:
        changed = False
        for p in code_files:
            v = res.get(p)
            if not v or v["conf"] != "unplaced" or v["row"] == "S1/T1":
                continue
            s_ = stem(rel(p))
            pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(s_) + r"(?![A-Za-z0-9_])")
            importers = [q for q in code_files if q != p and res[q]["cls"] in kept_cls_set and res[q]["conf"] != "unplaced" and pat.search(txt.get(q, ""))]
            if importers:
                cls = S if any(res[q]["cls"] == S for q in importers) else local
                v["note"] = f"named by kept {rel(importers[0])}" + (f" (+{len(importers)-1})" if len(importers) > 1 else "")
                v["cls"], v["row"], v["conf"] = cls, "-", "derived"
                changed = True
    # the remaining unplaced: hint = kept referrers vs deleted referrers
    for p in code_files:
        v = res[p]
        if v["conf"] != "unplaced":
            continue
        s_ = stem(rel(p))
        pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(s_) + r"(?![A-Za-z0-9_])")
        refs = [q for q in files if q != p and not rel(q).startswith(("auto-memory/", "plans/", "tests/")) and pat.search(txt.get(q, ""))]
        dels = [q for q in refs if res.get(q, {}).get("cls") in (D, BM)]
        v["note"] += f"; referrers: {len(refs) - len(dels)} kept, {len(dels)} deleted or moved"

    # tests: subject wins, then referenced modules, then unplaced
    for p in pending_tests:
        r = rel(p)
        if r.startswith("tests/fixtures/") or r.startswith("tests/agent_fixtures/") or r.startswith("tests/lib/") or not re.search(r"(^tests/test_|_test\.|\.sh$)", r):
            continue
        subj = subject_of(p)
        if subj:
            sv = res[subj]
            put(p, sv["cls"], sv["row"], "derived", f"test of {rel(subj)}")
            continue
        refs = referenced(p)
        classes = Counter(res[x]["cls"] for x in refs)
        kept = [c for c in classes if c not in (D, BM)]
        if refs and not kept:
            put(p, D if D in classes else BM, "-", "derived", f"names only deleted or moved files ({rel(refs[0])}{'...' if len(refs) > 1 else ''})")
        elif kept:
            c = S if S in kept else kept[0]
            put(p, c, "-", "derived", f"names kept {rel(next(x for x in refs if res[x]['cls'] == c))}")
        else:
            c, why = lock_default(r, lk, local)
            put(p, c, "-", "unplaced", f"test names no known module; {why}")

    # fixtures and helpers under tests/: follow whatever names them, to a fixed point
    fx = [p for p in files if p not in res]

    def key_of(p):
        r = rel(p)
        m = re.match(r"^tests/(fixtures|agent_fixtures)/([^/]+)", r)
        if m:
            return m.group(2)
        return r.rsplit("/", 1)[-1] if "/" in r[len("tests/"):] or r.startswith("tests/") else r

    keys = {p: key_of(p) for p in fx}
    pats = {k: re.compile(re.escape(k.rsplit(".", 1)[0] if re.search(r"\.(py|js|ps1)$", k) else k)) for k in set(keys.values())}
    user_pool = [p for p in files if rel(p).startswith("tests/") and p in txt]
    pending = set(fx)
    for _ in range(6):
        moved = False
        for p in sorted(pending):
            users = [u for u in user_pool if u != p and u in res and res[u]["conf"] != "unplaced" and pats[keys[p]].search(txt.get(u, ""))]
            if not users:
                continue
            classes = Counter(res[u]["cls"] for u in users)
            kept = [c for c in classes if c not in (D, BM)]
            if kept:
                put(p, S if S in kept else kept[0], "-", "derived", f"{keys[p]} named by {len(users)} kept test(s)")
            else:
                put(p, D if D in classes else BM, "-", "derived", f"{keys[p]} named only by deleted or moved tests")
            pending.discard(p)
            moved = True
        if not moved:
            break
    for p in pending:
        c, why = lock_default(rel(p), lk, local)
        put(p, c, "-", "unplaced", f"no classified test names {keys[p]}; {why}")
    assert set(res) == set(files), (len(res), len(files))
    return files, res


def main():
    out = {}
    for key in common.TARGETS:
        files, res = classify(key)
        out[key] = res
        c = Counter(v["cls"] for v in res.values())
        u = sum(1 for v in res.values() if v["conf"] == "unplaced")
        print(key, len(files), dict(c), "unplaced", u)
    json.dump(out, open(common.SCR + "/classes.json", "w"), indent=0)


if __name__ == "__main__":
    main()
