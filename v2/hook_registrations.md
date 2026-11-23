# v2 hook registrations

S8 and S9 write each repo's hand-owned `settings.json` from this list (AS-a); the shared repo carries no `settings.json`. Each row is one registered command, `python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/<file>"`. A kept shared hook appears in exactly one row; a hook that runs as a dispatcher member or a floor member appears in its dispatcher's or floor's member column and has no row of its own. Timeouts are the v1 values except where the Members column says otherwise.

Replaces `hooks/README.md` (deleted by B13: its one live-hook fact is this list) and the v1 registrations in `settings.base.json` / `settings.coding.json`.

Also required in each `settings.json`: `permissions.deny` holds `"Agent"` (T4: Workflows are the one native route, and the `Agent` tool's effort pin is unproven). Each project at cutover removes its stale `compact_directive_anchor` PostToolUse entry; the anchor's pointer write is now a row 8 member.

## Registrations

| # | Event | Matcher | File | Timeout (s) | Members (run in this order) |
|---|---|---|---|---|---|
| 1 | PreToolUse | `Bash\|PowerShell\|Monitor\|TaskStop` (`_floor.MATCHER`) | `pre_bash.py` (the floor entry) | 75 | `_floor.FLOOR_MEMBERS` (`dangerous_shell_guard`, `kill_guard`), then `_floor.HELPER_NAMES` (`bash_length_guard`, `unbounded_scan_guard`'s recursive-grep deny, `run_call_router`, `foreground_wait_guard`, `bash_backslash_fidelity`, `compound_cd_approver`). The timeout must exceed `_floor.BUDGET_S` (40 s); 75 s leaves the process-table reads room. `kill_guard` and `run_call_router` read the project's `adaptation.json` `pre_bash` rows (`local_rewires.md`). |
| 2 | PreToolUse | `Write\|Edit` | `pre_edit_dispatch.py` | 60 | `running_script_edit_guard`, then the project's `hook_members.pre_edit` |
| 3 | PreToolUse | `Read\|Grep\|Glob\|mcp__plugin_semantic-search_semantic-search__search` | `pre_read_dispatch.py` | 10 | `semantic_search_scope_guard`, then the project's `hook_members.pre_read`. A deny is a JSON permission denial (exit 0), as row 2's. |
| 4 | PreToolUse | `Workflow\|Agent` | `pre_workflow_dispatch.py` | 90 | `subagent_dispatch_guard`, the args and jobs file expansion, `tools/launch_check.py`, `workflow_transport`, `workflow_script_lf_guard` (the file's docstring lists the steps) |
| 5 | PostToolUse | `Workflow` | `pre_workflow_dispatch.py` | 90 | the same file in its post-return mode: `launch_check`'s `dispatch.post_return` hook, once per job; nothing runs when the project sets none |
| 6 | PreToolUse | `SendMessage` | `peer_message_guard.py` | 20 | none |
| 7 | PreToolUse | `AskUserQuestion` | `overnight_ask_guard.py` | 10 (v1: client default) | none; the same file is also a member of row 12 |
| 8 | PostToolUse | `Write\|Edit` | `post_edit_dispatch.py` | 45 | `compact_directive_anchor` (writes the state-file pointer; bounded at 5 s by `_chain.EVENTS`), then the project's `hook_members.post_edit` |
| 9 | PostToolUse | `Read\|Grep\|Glob\|WebFetch\|WebSearch\|mcp__plugin_semantic-search_semantic-search__search` | `post_read_dispatch.py` | 10 | no shared member; the project's `hook_members.post_read`. Register the row only for a project that lists members, with the matcher set to the tools they act on; this one is the shared default. |
| 10 | PostToolUse | `*` | `runaway_scan_reaper.py` | 10 | none; one registration for every tool, so the reaper runs once per call (its throttle is machine-wide) |
| 11 | UserPromptSubmit | `*` | `runaway_scan_reaper.py` | 10 | none; the same file as row 10, for the prompt boundary (an autonomous drive sends no prompts for hours) |
| 12 | UserPromptSubmit | `*` | `prompt_dispatch.py` | 40 | `prompt_git_state_delta`, `overnight_ask_guard`, then the project's `hook_members.prompt` |
| 13 | SessionStart | `*` | `session_context_loader.py` | 600 | loads `session_context_loader_csharp` where present; a project's Godot step is its own (`local_rewires.md`) |
| 14 | SessionStart | `*` | `vault_mirror_start.py` | 120 | none; prints nothing where the project has no `tools/vault_sync.project.json`. Its tool call is bounded at 100 s (`TOOL_TIMEOUT_S`). |
| 15 | SessionStart | `startup\|resume` | `login_parity_check.py` | 120 | none |
| 16 | SessionStart | `compact` | `compact_directive_anchor.py` | 15 | none (B7, `hook_registrations/B7.md`) |
| 17 | PreCompact | `*` | `transcript_backup.py` | 30 | none |

Row 16 is B7's one row (its fragment `hook_registrations/B7.md` carries the command); its PostToolUse half is a row 8 member.

## Not registered, with the reason

- **Armed by a launcher, no settings entry:** `sidecar_workdir_guard` (`tools/launch_check.py` arms it for a writing seat in a linked worktree) and `agy_hook_bridge` (the agy seat's floor entry; `tools/agy_workspace.py` registers it in `.agents/hooks.json`, pointed at `pre_bash.py`).
- **Floor members, no row:** `dangerous_shell_guard`, `kill_guard`, `_git_commit`, `editor_live_build_guard` where a project adopts it, and the helpers named in row 1. `foreground_wait_guard` is a row 1 helper: it clamps a seat's and a native delegate's waits alike and needs no arming.
- **PushinPotions-local, in PushinPotions' own settings or `hook_members`:** `pattern_enforcer` and `tres_format_guard` (`hook_members.pre_edit`, in that order), `check_logger_tag_prefix` (`hook_members.post_edit`), `tres_script_strip_guard`, `tres_nullstrip_guard`, `runtime_mcp_worktree_guard` (Godot MCP matchers), `cloud_test_enforcer`, `prototype_containment_guard`, `provisional_totality_guard`, `refcounted_free_guard`. MaddManager's `convex_prod_target_guard` is MaddManager-local. `ollama_healthcheck` left the shared user-scope manifest (FX-SEATS-DATA); a machine that runs local models registers it as its own user-scope hook, and a machine that installed it keeps its settings entry until removed by hand.
- **Deleted in v2 (rows dropped by S8 and S9):** every v1 row for `log_instruction_loads`, `denial_coach`, `skill_load_marker`, `memory_hits_logger`, `dispatch_table`, `model_ladder_gate`, `amend_delivery`, `rail_probe_guard`, `session_model_rails`, `compact_directive_ledger`, `subagent_compaction_rail`, `sidecar_recompact_reprompt`, `pre_bash_dispatch` (replaced by row 1) and `kill_guard` as its own entries (a member of row 1); the `compact_directive_anchor` PostToolUse entry (a row 8 member); the `Write` and `LSP` coverage of the read rows (no member reads them).

## Reconcile

Run from the lane root. Each list must print empty and the last line `True`: every kept hook has a row or a stated reason, every named file exists, every floor and dispatcher member is in this doc, and row 1's matcher is the floor's.

```
python3 -c "
import pathlib, re, sys
H = pathlib.Path('template/.claude/hooks'); sys.path.insert(0, str(H))
import _floor, _chain
doc = open('v2/hook_registrations.md', encoding='utf-8').read()
print('hook files with no mention:', [p.stem for p in H.glob('[a-z]*.py') if p.stem not in doc])
print('named files that do not exist:', sorted({f for f in re.findall(r'\x60([a-z_]+)\.py\x60', doc) if not (H / (f + '.py')).exists()}))
members = [m for m, _, _ in _floor.FLOOR_MEMBERS] + list(_floor.HELPER_NAMES) + [n for r in _chain.EVENTS.values() for n in r.shared]
print('floor and dispatcher members not named:', [m for m in members if m not in doc])
print(_floor.MATCHER.replace('|', chr(92) + '|') in doc)
"
```
