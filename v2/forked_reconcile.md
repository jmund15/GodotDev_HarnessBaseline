# Forked shared files: v2 reconcile audit (S6b B12)

Source: PushinPotions `origin/main` `d9f2cbe1a`, its `.claude/baseline.lock.json`. Every v2 column is read at v2 tip `9091bba`, the base of the fix round (rows 20–27 and 30 were re-checked there). Paths are under `.claude/` of PushinPotions and `template/.claude/` of the shared repo.

**Disposition codes.** **U** upstreamed: the PushinPotions-local change is in v2, or a named carried/dropped row restates it. **M** moved: the text lives in a named PushinPotions-local file, or the owner's register names its local home. **D** dropped with its file (the delete row is named). **O** open: the local change is in neither v2 nor a named local file, or no v2 job has reconciled the file. Counts of "PP lines" are added lines of PushinPotions' change against the fork base, checked for exact-line presence in v2 and in PushinPotions' tree.

## 1. The ten extra forked rows, checked commit by commit (Q3)

The lock marks 49 shared files `forked`; the plan and inventory say 39 (P:562, I:153). The ten extras are:

| File | First marked `forked` at | Note |
|---|---|---|
| `hooks/{pre_edit,post_edit,pre_read,post_read,prompt}_dispatch.py`, `hooks/pre_workflow_dispatch.py`, `hooks/git_guardrails.py`, `tools/machine_slots.py`, `rules/harness_tooling.md` | `54773b267` (2026-11-22, `v2 M1 unwire the policing hooks`) | M1's fork marks, as the map inferred. |
| `scripts/validate_commands.py` | `4ed935825` (2026-06-11, `reconcile harness drift — … fork 10`) | **Not M1.** The map's inference is wrong for this file: it was forked in June. |

So M1 marks nine of the ten. For those nine, M1's bytes are the v2 base (Q3). Checked in v2: `machine_slots.py` has them (row 49); `pre_workflow_dispatch.py` has them (row 27, its M1 removals applied); the five dispatchers and `git_guardrails.py` do not (rows 21–26).

## 2. One row per forked file

| # | Shared file | Layer · fork base | v2 version (owner, commit) | PP-local change | Disp. | Evidence and what is open |
|---|---|---|---|---|---|---|
| 1 | `CLAUDE.core.md` | pure · `9aceeed` | A1 `38716df`, 54 lines (8 KB core) | 5 lines | U | Restated under A1 rows C15, C21, C46, C58, C70, C71 (`v2/carried_dropped/A1.md`); verbatim text not kept. |
|  2 | `commands/agents/documentation_structure.md` | pure · `3b8ee0d6` | A10 `afe600d`, 59 lines | 9 lines | M | B12b. The fork is `{{PROJECT_NAME}}` and `{{VAULT_ROOT}}` rendered as literals; v2 reads `adaptation.json` `project_name` and `vault_root` (PP sets both at S9). PP's NPC example and its `/doc_npc` pointer belong in PP-local `/doc_npc`; its naming examples are illustrations, and v2's generic ones serve. |
|  3 | `commands/agents/pr_classification.md` | coding · `b84f7b0b` | none: v2 holds base bytes | 22 lines | M | B12b. PP's domain table is `adaptation.json` `pr_domains`, which PP already holds (Logic, Meta, Jmodot). S9 adds a Gameplay row from the `playtest-subject-folders` tag paths and renames Jmodot to Framework, the shared label. The `execution_depth.md` reference died with that file (A4); the "tested source is not Logic" warning is in v2. |
| 4 | `commands/agents/transcript_nuance_recall.md` | pure · `3b8ee0d6` | deleted, A10 `5f59322` | 2 lines | D | Register A10: dropped (its callers `/autolearn` and `/self_evaluate` are gone). |
| 5 | `commands/aiworker_write_doc_audit.md` | pure · `3b8ee0d6` | deleted, A10 `c491dec` | 3 lines | M | Moved out of shared by Q12 O1 (register A10). Copy at `v2/q12_move/commands/`, de-projected, not PP's bytes. PP keeps its own copy locally. |
|  6 | `commands/checklists/code_quality.md` | coding · `3b8ee0d6` | A5 `a904561`, 81 lines | 40 lines | M | B12b. PP-local `commands/checklists/code_quality_godot.md` (tracked, read by the shared file) holds every Godot and Jmodot item. Dropped: PP's `IngredientTrait` naming example, `pattern_enforcer` (unwired by M1) and two memory pointers; v2's generic wording is newer. |
|  7 | `commands/checklists/test_quality.md` | coding · `3b8ee0d6` | A5 `a904561`, 36 lines | 9 lines | M | B12b. PP-local `test_quality_godot.md` holds Structure coupling, the Mocks/ doubles check and Lifecycle-trapped logic. The rest of the diff is v2's newer generic wording. |
| 8 | `commands/claudemd_compact.md` | pure · `3b8ee0d6` | A10 `afe600d`, 135 lines | 4 lines | U | M1 band replaced by T2's 15 KB bound (register A10, `/claudemd_compact` rows). |
|  9 | `commands/create_pr.md` | pure · `3b8ee0d6` | A10 `afe600d`, 145 lines | 93 lines | U | B12b. Upstreamed: the no-confirmation rule, `git -C Jmodot *` in `allowed-tools`, and the cut of two bullets that restated Step 3-Update. Already shared: the Jmodot pairing and Test plan (`reference/create_pr_jmodot.md`). Moved: Step 4b's playtest receipt to PP's `pr_ready.checks` file (S9); it blocks before PR creation like the other project checks. |
|  10 | `commands/doc_start_here_update.md` | pure · `3b8ee0d6` | A10 `afe600d`, 131 lines | 7 lines | U | B12b. PP's literal vault paths are `project_name` (v2 reads it). Upstreamed PP's cut of lines that restated other steps. PP's content-domain text is its `content_domains` value, already set. |
|  11 | `commands/merge_pr.md` | coding · `ffc2a2d8` | A10 `afe600d`, 332 lines | 82 lines | M | B12b. PP's merge flow is PP machinery: `/pr_sync` and `agents/pr_branch_sync.md` (both PP-local), a sync worktree and gate receipt, the playtest ledger and `pr_vault_archive.py`. S9 keeps PP's bytes as PP-local `commands/pp_merge_pr.md` and repoints `/pr_pipeline`; shared `/merge_pr` stays the one generic path (MaddManager). Difference and reason: PP isolates the merge in a worktree because concurrent sessions share its primary checkout; the shared Step 4 stashes and checks out in place. |
|  12 | `commands/plan_part.md` | coding · `f4e5d92f` | A4 `26012f8`, 251 lines | 16 added, 146 removed | D | B12b. PP's 85-line version splits the phases into `plan_part_phase1_3.md` and `plan_part_phase4_5.md` and cites `execution_depth.md`, `/design_drive`, `task_record.py` and `/plan_check`, all deleted (A4 register, inventory). All five phases run on every call, so the split saves no read. v2's A4 text stands; S9 deletes the two PP-local phase files. |
| 13 | `commands/pr_ready.md` | coding · `ffc2a2d8` | A6 `4ec669e`, 74 lines | 67 lines | M | Register A6: PP's Steps 0a–0c go to PP-local `pr_ready.checks`, which S9 writes; the file does not exist yet. The old review steps (54 lines) are A6's rewrite. Closes at S9. |
|  14 | `commands/reference/plan_part_composition.md` | coding · `6cd286f7` | A4 `26012f8`, 31 lines | 6 lines | D | B12b. PP's six rows only repoint links at the phase files row 12 drops. |
|  15 | `commands/reference/workstation_setup_land_runner.md` | pure · `ffc2a2d8` | A10 `1b3cfc2`, 37 lines | 26 lines | U | B12b. Upstreamed steps 4 and 5 (main-check worktree and `main-check-state` seed), which `scripts/main_check.py` defines. PP's `MAIN_CHECK_RUNNER` and `MAIN_CHECK_DIR` variables, `.runsettings` prep and the "land.yml is GitHub-hosted" fact are project values for its toolchain reference. |
| 16 | `commands/review_pr.md` | coding · `ffc2a2d8` | A6 `4ec669e`, 52 lines | 50 added, 51 removed | D | Register A6 drops or rewires the PP steps: 1c classify, 1d memory search, 5 regression gate, agent templates (A5). Line-level check not done. |
| 17 | `commands/sync_subsystems.md` | coding · `3b8ee0d6` | A10 `5f59322`, 59 lines | 5 lines | U | Register A10: Step 0 and Step 1 replaced by `reserved_paths:` (T7). |
|  18 | `commands/workstation_setup.md` | pure · `1302c0d1` | A10 `afe600d`, `1b3cfc2`, 267 lines | 88 lines | U | B12b. Upstreamed `core.longpaths`, the `stale` user-scope row, and `install` without a prompt. PP's NuGet, .NET, Godot-pin, csharp-ls and runtimeconfig phases go to PP-local `workstation_setup_toolchain.md` (v2 reads it; S9 folds them in). Dropped with their tools (V10): `baseline_sync.py check`, the `lock_merge.py` driver, the WSL preview. |
| 19 | `gitignore.harness` | pure · `ffc2a2d8` | **B12, this lane** (residue, commit A) | 2 lines removed | U | PP removed `.claude/tests/` and its comment (`14cb8b4a4`). The exclusion ships in shared `searchignore.harness` (`ef4727a`), so v2 takes PP's bytes. The v1 test `test_baseline_sync_doctor.py` uses its own fixture, not this file. |
| 20 | `hooks/README.md` | pure · `3b8ee0d6` | deleted, B13 `6c3004b` | 35 added, 27 removed | D | Register B13 R1–R3: doctrine dropped. Its one live-hook fact is `v2/hook_registrations.md`, written and reconciled against the tip (its Reconcile command). |
| 21 | `hooks/git_guardrails.py` | pure · `9aceeed` | deleted, B16 `5696d6f` | 385 added, 681 removed | D | The floor (`dangerous_shell_guard`, `kill_guard`, `_git_commit`) carries every loss deny; M1's one hunk and the advisory helpers went with the file. None of its 34 M1-removed lines of 25+ characters survives in v2's hooks. |
| 22 | `hooks/post_edit_dispatch.py` | pure · `9aceeed` | stub over `_chain.py`, FX-DISP `fc7d937` | 1 added, 13 removed | U | M1's removed members are gone: none of its 15 removed lines of 25+ characters is in the stub or `_chain.py`. The shared members are `_chain.EVENTS["post_edit"]`; a project's own come from `adaptation.json` `hook_members` (`hook_registrations.md` rows 2, 3, 8, 9, 12). |
| 23 | `hooks/post_read_dispatch.py` | pure · `9aceeed` | stub over `_chain.py`, FX-DISP `fc7d937` | 8 added, 55 removed | U | M1's removed members are gone: none of its 59 removed lines of 25+ characters is in the stub or `_chain.py`. The shared members are `_chain.EVENTS["post_read"]`; a project's own come from `adaptation.json` `hook_members` (`hook_registrations.md` rows 2, 3, 8, 9, 12). |
| 24 | `hooks/pre_edit_dispatch.py` | pure · `9aceeed` | stub over `_chain.py`, FX-DISP `fc7d937` | 2 added, 23 removed | U | M1's removed members are gone: none of its 16 removed lines of 25+ characters is in the stub or `_chain.py`. The shared members are `_chain.EVENTS["pre_edit"]`; a project's own come from `adaptation.json` `hook_members` (`hook_registrations.md` rows 2, 3, 8, 9, 12). |
| 25 | `hooks/pre_read_dispatch.py` | pure · `9aceeed` | stub over `_chain.py`, FX-DISP `fc7d937` | 16 added, 109 removed | U | M1's removed members are gone: none of its 62 removed lines of 25+ characters is in the stub or `_chain.py`. The shared members are `_chain.EVENTS["pre_read"]`; a project's own come from `adaptation.json` `hook_members` (`hook_registrations.md` rows 2, 3, 8, 9, 12). |
| 26 | `hooks/prompt_dispatch.py` | pure · `9aceeed` | stub over `_chain.py`, FX-DISP `fc7d937` | 1 added, 8 removed | U | M1's removed members are gone: none of its 6 removed lines of 25+ characters is in the stub or `_chain.py`. The shared members are `_chain.EVENTS["prompt"]`; a project's own come from `adaptation.json` `hook_members` (`hook_registrations.md` rows 2, 3, 8, 9, 12). |
| 27 | `hooks/pre_workflow_dispatch.py` | pure · `9aceeed` | FX-NAT, 282+ lines, launch checks through `launch_check.py` | 14 added, 24 removed | U | Re-checked at tip `9091bba`: none of M1's 16 removed lines of 25+ characters survives. 12 of 14 M1 additions describe members the rewrite drops; superseded by the one launch-check path (B1, `launch_check.py`). |
|  28 | `reference/semantic_search.md` | coding · `ffc2a2d8` | A1 `57926fc`, 11 lines | 5 added, 4 removed | U | B12b. Upstreamed the scope guard's subdirectory rewrite. C# chunking is already owned by `semantic_search_idle.md`; PP's vault paths are `doc_roots`. |
| 29 | `rules/harness_tooling.md` | pure · `9aceeed` | A1 `83a9dae`, 54 lines | 5 added, 6 removed | U | Register A1: `close growth` line dropped (H14), commit-proof rule carried (H16), `git_guardrails` quick checks dropped (H18), machine-slot rule carried (H22). |
| 30 | `scripts/validate_commands.py` | coding · `1e1cd7e0` | deleted, `fdf2100` (callerless) | 61 added, 80 removed | D | B12b, commit `847b164` cut its PP-specific checks; the file itself is gone at the tip. The six dead links §5 listed are no longer checked by any tool. |
| 31 | `skills/_brainstorm_shared/common.md` | coding · `ffc2a2d8` | A4 `26012f8`, 33 lines | none | U | PP's bytes are identical to the fork base (`cmp`), so there is no local change. The `forked` mark is stale; PP's lock should drop it. |
| 32 | `skills/_brainstorm_shared/design_contract.md` | coding · `6cd286f7` | A4 `26012f8`, 37 lines | 1 added, 1 removed | U | The one PP cross-link is re-pointed to `/plan`, `/drive`, `/review` by A4 (`26012f8`). |
|  33 | `skills/_brainstorm_shared/doc_conventions.md` | coding · `ffc2a2d8` | A4 `26012f8`, 86 lines | 5 added, 5 removed | D | B12b. PP's changes are its project name and its `pp-game` scope spelling; no tool checks scope values, so the shared `project-game` serves. |
|  34 | `skills/_brainstorm_shared/roadmap_schema.md` | coding · `c7a103f6` | A4 `26012f8`, 193 lines | 34 added, 34 removed | D | B12b. PP's changes are game examples, the `pp-game` spelling and text older than the fork base; v2 keeps the later shared text. |
|  35 | `skills/architecture_brainstorm/SKILL.md` | coding · `f4e5d92f` | A4 `26012f8`, 265 lines | 38 added, 155 removed | D | B12b. As row 12: PP's 98-line split cites `execution_depth.md`, `/part_drive` and `/plan_check`. A4 rewrote the file on `/plan`, `/drive`, `/review`; PP's trigger lists are the same triggers in fewer lines. |
|  36 | `skills/architecture_brainstorm/reference/part_authoring.md` | coding · `ffc2a2d8` | A4 `26012f8`, 151 lines (= base) | 111 added, 93 removed | D | B12b. As row 35: PP's "detail layer" rewrite cites the same deleted surfaces; A4's version stands. |
|  37 | `skills/architecture_philosophy/SKILL.md` | coding · `324163ce` | A9 `7aa2a28`, 95 lines | 91 added, 111 removed | M | B12b. Register A9 H5: PP's Godot and project sections stay in its local `reference/*.md`; S9 places them. |
|  38 | `skills/architecture_philosophy/structure_rules.md` | coding · `3b8ee0d6` | A9 `7aa2a28`, 48 lines | 147 added, 83 removed | M | B12b. Register A9 H5: R2, R5, R13, the alias table, the folder map and the audit cadence go to a PP-local file `/structure_audit` reads. |
|  39 | `skills/debugging/SKILL.md` | coding · `3b8ee0d6` | A9 `c357073`, 58 lines | 23 added, 10 removed | M | B12b. Register A9 H2: PP's router rows and Godot lines go to PP-local `reference/symptom_router_godot.md`, which PP's `CLAUDE.md` names. |
|  40 | `skills/debugging/reference/phase1-loop.md` | coding · `ffc2a2d8` | A9 `c357073`, 42 lines | 20 added, 16 removed | M | B12b. As row 39 (H2): the Godot loop menu, memory buckets and `JmoLogger` lines. |
|  41 | `skills/debugging/reference/phase2-reproduce.md` | coding · `ffc2a2d8` | none: v2 == base | 18 added, 16 removed | M | B12b. As row 39 (H2). The shared file is the generic base, which is what v2 wants. |
|  42 | `skills/debugging/reference/phase4-hypothesise.md` | coding · `ffc2a2d8` | none: v2 == base | 13 added, 16 removed | M | B12b. As row 39 (H2). The shared file is the generic base. |
|  43 | `skills/debugging/reference/phase6-cleanup.md` | coding · `ffc2a2d8` | A9 `c357073`, 19 lines | 6 added, 6 removed | M | B12b. As row 39 (H2): the `[DIAG-` grep over `.cs`, `DebugEnabled` and fixture-folder lines. |
| 44 | `skills/obsidian_conventions/SKILL.md` | pure · `3b8ee0d6` | A10 `afe600d`, 82 lines | 5 added, 5 removed | M | Vault roots are project values in `drafts/PushinPotions.CLAUDE.md` §Design Source (register A1 M7). |
| 45 | `skills/testing/advanced.md` | godot · `3b8ee0d6` | deleted, A9 `1eedb74` | 1 line | M | Q1 ruling: Godot half is PushinPotions-local. Register A9 H1: PP keeps its bytes at the same path. |
| 46 | `skills/testing/reference/authoring.md` | godot · `3b8ee0d6` | deleted, A9 `1eedb74` | 11 lines | M | As row 45 (register A9 H1). |
| 47 | `skills/testing/scene_runner.md` | godot · `3b8ee0d6` | deleted, A9 `1eedb74` | 5 lines | M | As row 45 (register A9 H1). |
| 48 | `skills/worklog_reference/SKILL.md` | pure · `e750aa3f` | A8 `81adbc6`, `63206e3`, 121 lines | 29 added, 39 removed | M | Register A8: PP domain rows go to `adaptation.json` `worklog_domains` (key exists in `tools/adaptation.py`, B6). PP's values are written at S9. |
| 49 | `tools/machine_slots.py` | pure · `9aceeed` | C2 `3ad8fea`, 418 lines (= PP's M1 bytes) | 6 added, 64 removed | U | M1 relief intact: no `PYTHONPATH`, `NODE_OPTIONS` or `BASH_ENV` injection. C2 removed `scripts/_slot_bootstrap/`. |

## 3. Counts and the done-condition

Run from the lane root; it reads the table above against tip `9091bba`.

```
python3 -c "
import re, subprocess, collections
tip = set(subprocess.run(['git', 'ls-tree', '-r', '--name-only', '9091bba', '--', 'template/.claude'], capture_output=True, text=True).stdout.split('\n'))
rows = re.findall(r'(?m)^\|\s*(\d+) \| \x60([^\x60]+)\x60 \|.*? \| ([UMDO]) \|', open('v2/forked_reconcile.md', encoding='utf-8').read())
print(len(rows), collections.Counter(d for _, _, d in rows), 'no tip file:', [n for n, f, _ in rows if 'template/.claude/' + f not in tip])
"
```

Output: `49 Counter({'U': 19, 'M': 19, 'D': 11}) no tip file: ['4', '5', '20', '21', '30', '45', '46', '47']`.

- **Rows:** 49, one per forked file. **Shared v2 versions:** 41 files have one at `template/.claude/<path>`. Eight have none, because their owner deleted them: rows 4, 5, 20, 21, 30, 45, 46, 47.
- **Disposition:** U 19 (rows 1, 8, 9, 10, 15, 17, 18, 19, 22–29, 31, 32, 49), M 19 (2, 3, 5, 6, 7, 11, 13, 37–48), D 11 (4, 12, 14, 16, 20, 21, 30, 33, 34, 35, 36), O 0.
- **Done-condition** ("no row is left open"): met. Row 13 closes at S9 when `pr_ready.checks` is written; rows 2, 3, 11 and 37–43 close when S9 places the PP-local content they name. `local_rewires.md` lists S9's other edits.

## 4. Rows that rest on unmerged lanes

None. Every v2 column names a commit at or before tip `9091bba`.

## 5. Owner returns

- **S9 (PushinPotions cutover):** place the PP-local content rows 2, 3, 11, 13 and 37–43 name; delete PP's `plan_part_phase1_3.md` and `plan_part_phase4_5.md`; keep PP's merge flow as `commands/pp_merge_pr.md`.

## 6. What this audit did and did not check

- Line presence is exact after whitespace trim, for lines of 25 characters or more. A line counted as "in neither" may be reworded rather than dropped; the owner has to say which.
- Register rows are taken from `v2/carried_dropped/*.md` as written. I did not re-derive them.
- Forked files that owners rewrote from base rather than from PushinPotions' bytes are counted by their diff, not by intent.
