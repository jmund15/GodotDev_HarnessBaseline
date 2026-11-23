# v2 hook registrations

Written by B14. S8 and S9 write each repo's hand-owned `settings.json` from this list (AS-a); the shared repo carries no `settings.json`. Each row is one registered command, `python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/<file>"`. A kept shared hook appears in exactly one row; a hook that runs as a dispatcher member or a floor member appears in its dispatcher's or floor's member column and has no row of its own. Timeouts are the v1 values unless a note says otherwise.

Replaces `hooks/README.md` (deleted by B13: its one live-hook fact is this list) and the v1 registrations in `settings.base.json` / `settings.coding.json`.

## Registrations

| # | Event | Matcher | File | Timeout (s) | Members (run in this order) |
|---|---|---|---|---|---|
| 1 | PreToolUse | `Bash\|PowerShell\|Monitor\|TaskStop` | `pre_bash.py` (the floor entry, S5b) | 75 | `_floor.py` and its modules; the kept Bash helpers (`bash_length_guard`, `bash_backslash_fidelity`, the recursive-grep deny, `compound_cd_approver`) run inside it. The registered timeout must exceed the floor's own 40 s budget (`_floor.BUDGET_S`); S5b sets the final value. |
| 2 | PreToolUse | `Write\|Edit` | `pre_edit_dispatch.py` | 60 | `running_script_edit_guard`, then the project's `hook_members.pre_edit` |
| 3 | PreToolUse | `Read\|Grep\|Glob\|mcp__plugin_semantic-search_semantic-search__search` | `pre_read_dispatch.py` | 10 | `semantic_search_scope_guard`, then the project's `hook_members.pre_read` |
| 4 | PreToolUse | `Workflow\|Agent` | `pre_workflow_dispatch.py` | 90 | `subagent_dispatch_guard`, `workflow_script_lf_guard`; its rewrite is the T4 job's (`job_map.md` B3 write set) |
| 5 | PreToolUse | `SendMessage` | `peer_message_guard.py` | 20 | none |
| 6 | PreToolUse | `AskUserQuestion` | `overnight_ask_guard.py` | 10 (v1: client default) | none; the same file is also a member of row 12 |
| 7 | PostToolUse | `Write\|Edit` | `post_edit_dispatch.py` | 45 | `runaway_scan_reaper`, then the project's `hook_members.post_edit` |
| 8 | PostToolUse | `Write\|Edit` | `compact_directive_anchor.py` | 15 | none (B7: writes the state-file pointer) |
| 9 | PostToolUse | `Read\|Grep\|Glob\|Write\|WebFetch\|WebSearch\|LSP\|mcp__plugin_semantic-search_semantic-search__search\|mcp__ai-worker__write_doc` | `post_read_dispatch.py` | 10 | `runaway_scan_reaper` (only for a tool row 10 does not cover), then the project's `hook_members.post_read` |
| 10 | PostToolUse | `^(?:Agent\|Workflow\|TaskStop\|TaskOutput\|Skill\|ToolSearch\|LSP\|NotebookEdit\|mcp__.*)$` | `runaway_scan_reaper.py` | 10 | none |
| 11 | UserPromptSubmit | `*` | `runaway_scan_reaper.py` | 10 | none; the same file as row 10 |
| 12 | UserPromptSubmit | `*` | `prompt_dispatch.py` | 40 | `prompt_git_state_delta`, `overnight_ask_guard`, then the project's `hook_members.prompt` |
| 13 | SessionStart | `*` | `session_context_loader.py` | 600 | loads `session_context_loader_csharp` / `_godot` steps where present |
| 14 | SessionStart | `*` | `vault_mirror_start.py` | 120 | none; prints nothing where the project has no `tools/vault_sync.project.json`. Its tool call is bounded at 100 s (`TOOL_TIMEOUT_S`). v1 shipped no row for it, so a project that wants the vault line must register it. |
| 15 | SessionStart | `startup\|resume` | `login_parity_check.py` | 120 | none |
| 16 | SessionStart | `compact` | `compact_directive_anchor.py` | 15 | none (B7, `hook_registrations/B7.md`) |
| 17 | PreCompact | `*` | `transcript_backup.py` | 30 | none |

Rows 8 and 16 are B7's two rows (its fragment `hook_registrations/B7.md` carries their commands). Row 10 and row 11 are one hook run twice by design: row 10 covers the tools the dispatchers' matchers miss, row 11 covers the prompt boundary. Row 9 skips a tool row 10 matches, so the reaper runs once per call.

## Not registered, with the reason

- **Launcher-armed, no settings entry:** `foreground_wait_guard` (Q9: `seat.sh` arms it for seats; each repo wires it for native delegates, an event the repo's own settings choose), `sidecar_workdir_guard`, `agy_hook_bridge` (the agy seat's floor entry; C1 points it at `pre_bash.py`).
- **Floor members, no row:** `git_guardrails`, `_git_commit`, `dangerous_shell_guard`, `kill_guard`, `editor_live_build_guard` where a project adopts it, `unbounded_scan_guard`'s recursive-grep deny.
- **PushinPotions-local, in PushinPotions' own settings or `hook_members`:** `pattern_enforcer` and `tres_format_guard` (`hook_members.pre_edit`, in that order), `check_logger_tag_prefix` (`hook_members.post_edit`), `tres_script_strip_guard`, `tres_nullstrip_guard`, `runtime_mcp_worktree_guard` (Godot MCP matchers), `cloud_test_enforcer`, `prototype_containment_guard`, `provisional_totality_guard`, `refcounted_free_guard`. MaddManager's `convex_prod_target_guard` is MaddManager-local.
- **Deleted in v2 (rows dropped by S8 and S9):** every v1 row for `log_instruction_loads`, `denial_coach`, `skill_load_marker`, `memory_hits_logger`, `dispatch_table`, `model_ladder_gate`, `amend_delivery`, `rail_probe_guard`, `session_model_rails`, `compact_directive_ledger`, `subagent_compaction_rail`, `sidecar_recompact_reprompt`, `pre_bash_dispatch.py` (replaced by row 1) and `kill_guard.py` as its own entries (members of row 1).
