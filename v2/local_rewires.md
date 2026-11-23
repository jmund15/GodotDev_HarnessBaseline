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
| Add the v2 keys it lacks | `hook_members`, `project_name`, `vault_root`, `worklog_domains`, `review.checklists`, `pr_ready.checks`, `overnight_dir`, `dispatch`, `index_prebuild`; their values are the `forked_reconcile.md` rows that name them |

```
python3 -c "import json,sys; sys.path.insert(0,'tests/floor'); sys.path.insert(0,'hooks'); import conftest; print(json.dumps(conftest.PROJECT_PRE_BASH, indent=2))"
```

Run it from `template/.claude/` of the shared repo (the conftest imports `pytest`). The key list under "Delete the unread keys" is `sorted(set(adaptation.json) - set(tools/adaptation.DEFAULTS))`.

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

Seven `local` lock rows sit at a path v2 now ships, so `pull` refuses them by name (a stray is never overwritten). S9 resolves each before the pull: delete the local copy so v2's text stands, or push its content upstream first.

```
.claude/skills/architecture_brainstorm/reference/doc_and_review.md
.claude/skills/change_control/SKILL.md
.claude/tests/test_prompt_dispatch.py
.claude/tests/test_runaway_scan_reaper.py
.claude/tests/test_setup_docs.py
.claude/tests/test_usage_report.py
.claude/tools/usage_report.py
```

Derivation: the dry run in `delete_list.md` §First pull, on a copy of PushinPotions' lock-listed files; the list is `plan_pull`'s conflicts.

## MaddManager

- **`/review_prs` (batch PR review): pending Q2.** The shared tree no longer carries it (A10 deleted it as MaddManager's); `commands/review.md:14` still points at it and is left alone. The owner picks one:
  1. keep it MaddManager-local, without the Godot steps (MaddManager keeps its own copy; no shared change);
  2. a project-neutral shared `/pr_pipeline` that replaces it (a new shared command; the batch loop moves into the shared tree);
  3. drop batch review (MaddManager deletes its copy; `review.md:14` is repointed).
  Until the pick, `commands/review_prs.md` is in the MaddManager local list in `delete_list.md`, so the first-pull recipe does not remove it.
- `commands/roadmap_next.md` stays MaddManager-local (A11).
- `adaptation.json`: add `pre_bash` with only the protected rows whose commands MaddManager runs (`git grep` each pattern of `PROJECT_PRE_BASH`; the .NET gate rows are PushinPotions' by T7), `scratch_keep`, and drop `usage_reset` and the dead `gate_cadence` rows as above. Its `convex_prod_target_guard` stays a MaddManager-local hook entry.
- `settings.json`: the same rules as PushinPotions, minus the Godot and C# entries. It has no `.runsettings` caller.
