# .claude/hooks/ — artifact classes

Three kinds of file live here; consult before assuming a file is dead. This README documents the
pure layer. The coding and godot layers add sub-hooks to the same dispatchers, entry points and
command CLIs; each of those files documents itself in its docstring.

1. **Event hooks** — registered in `settings.json` `hooks`. Entry points:
   `pre_read_dispatch.py` / `post_read_dispatch.py` (read/search tool family),
   `pre_edit_dispatch.py` / `post_edit_dispatch.py` (Write|Edit family: before the edit
   `harness_edit_skill_reminder.py`, `readonly_lens_write_guard.py`, `running_script_edit_guard.py`;
   after it `design_surface_reminder.py`, `plan_memory_reminder.py`, `harness_growth_guard.py`,
   `self_eval_archive_guard.py`, `retire_trigger_advisory.py`, the reaper's `check()`) and
   `pre_bash_dispatch.py` (Bash/PowerShell/Monitor family: `git_guardrails.py`,
   `baseline_classification_guard.py`, `unbounded_scan_guard.py`, `compound_cd_approver.py`,
   `bash_shape_guard.py`, `sidecar_dispatch_context.py`, the four `--hook` commit guards, and last
   `bash_backslash_fidelity.py`, which repairs the Bash tool's `\\` collapse on a recorded client) — each dispatcher
   imports its sub-hooks in-process. Other entry points:
   `prompt_memory_loader.py`, `critical_analysis_reminder.py`, `prompt_git_state_delta.py`,
   `runaway_scan_reaper.py` (kills orphaned search processes: `check()` runs in-process from the three
   dispatchers, plus UserPromptSubmit and a PostToolUse matcher for the tools no dispatcher covers —
   Agent, Workflow, TaskStop, TaskOutput, Skill, ToolSearch, LSP, NotebookEdit, `mcp__*`; AskUserQuestion,
   SendMessage, the Task list, worktree, cron and notification tools rely on the prompt-time run),
   `log_instruction_loads.py`, `transcript_backup.py`, on PreCompact
   `compact_directive_ledger.py` (asks the summary for a status per owner message), and on
   SessionStart(compact) `sidecar_recompact_reprompt.py` (a sidecar child's brief) and
   `compact_directive_anchor.py` (the owner's own messages, under the same IDs). Sub-hooks behind the
   dispatchers (`routing_audit.py`, `file_size_preblock.py`, `semantic_search_scope_guard.py`) keep
   standalone `main()` for testing.
2. **Libraries** — imported, never registered: `_transcript_summary.py`, `json_merge.py` (also a
   CLI), `_owner_text.py` (the one owner-row parser; kill_guard, harness_growth_guard and the digest
   each project it) and `_file_lock.py` (the open-handle lock behind `_hook_state`, the
   self-evaluate store and orchestration metrics).
3. **Command CLIs** — invoked by slash commands, not events. The pure layer ships none; the higher
   layers name theirs from the command that runs them.

Output-channel rules: `instruction_quality` skill §13 — model-visible advisory output on
Pre/PostToolUse is `hookSpecificOutput.additionalContext` JSON ONLY; stderr is exit-2-only.
Audit principles for this directory: `instruction_quality` skill §13–§16.

Subagent prompt state: the coding layer's UserPromptSubmit turn-state hook writes `last_prompt`
only to the session-level state file `<sid>.json`. `routing_audit.py` falls back to that file, so
its prompt-cue carve-outs see the parent's prompt inside a subagent. `file_size_preblock.py` reads
only the agent-keyed `<sid>_<aid>.json` when `agent_id` is present, so its audit-cue check sees an
empty prompt there.
