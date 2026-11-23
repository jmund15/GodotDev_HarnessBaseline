# Forked shared files: v2 reconcile audit (S6b B12)

Source: PushinPotions `origin/main` `d9f2cbe1a`, its `.claude/baseline.lock.json`. v2 read at lane base `b183746`; v2 tip is `e3adbe6` (B3d's commit, after the base). Paths are under `.claude/` of PushinPotions and `template/.claude/` of the shared repo.

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
| 2 | `commands/agents/documentation_structure.md` | pure · `3b8ee0d6` | A10 `afe600d`, 59 lines | 9 lines | O | Vault roots and naming examples absent from v2 and from PP-local files; no register row. Owner A10. |
| 3 | `commands/agents/pr_classification.md` | coding · `b84f7b0b` | none: v2 holds base bytes | 22 lines | O | PushinPotions' domain table (Logic, Gameplay, signals) is in neither; A10 keeps the file shared unrewritten. Owner A10. |
| 4 | `commands/agents/transcript_nuance_recall.md` | pure · `3b8ee0d6` | deleted, A10 `5f59322` | 2 lines | D | Register A10: dropped (its callers `/autolearn` and `/self_evaluate` are gone). |
| 5 | `commands/aiworker_write_doc_audit.md` | pure · `3b8ee0d6` | deleted, A10 `c491dec` | 3 lines | M | Moved out of shared by Q12 O1 (register A10). Copy at `v2/q12_move/commands/`, de-projected, not PP's bytes. PP keeps its own copy locally. |
| 6 | `commands/checklists/code_quality.md` | coding · `3b8ee0d6` | A5 `a904561`, 81 lines | 40 lines | O | 15 lines verbatim in PP-local `commands/checklists/code_quality_godot.md`; 25 reworded C# items (Node retrieval, Logging, Naming, Nullability) in neither. Owner A5. |
| 7 | `commands/checklists/test_quality.md` | coding · `3b8ee0d6` | A5 `a904561`, 36 lines | 9 lines | O | 2 lines in PP-local `test_quality_godot.md`; 7 items (Structure coupling, Lifecycle-trapped logic, Horizontal slicing, Lifecycle re-run) in neither. Owner A5. |
| 8 | `commands/claudemd_compact.md` | pure · `3b8ee0d6` | A10 `afe600d`, 135 lines | 4 lines | U | M1 band replaced by T2's 15 KB bound (register A10, `/claudemd_compact` rows). |
| 9 | `commands/create_pr.md` | pure · `3b8ee0d6` | A10 `afe600d`, 145 lines | 93 lines | O | 21 lines in v2; 10 in PP-local `reference/create_pr_jmodot.md` and `pr_test_checklist.md`; 55 (Jmodot pairing, PR conventions) in neither. Register covers only the classification fallback. Owner A10. |
| 10 | `commands/doc_start_here_update.md` | pure · `3b8ee0d6` | A10 `afe600d`, 131 lines | 7 lines | O | Literal `DevProjects/PushinPotions/…` paths in neither; no register row. Owner A10: `adaptation.json` `vault_root` is the home. |
| 11 | `commands/merge_pr.md` | coding · `ffc2a2d8` | A10 `afe600d`, 332 lines | 82 lines | O | 5 in v2; 3 in PP-local `pr_sync.md` and `pr_pipeline.md`; 67 in neither. Register covers the accumulative-JSON rule and the `/review_prs` note only. Owner A10. |
| 12 | `commands/plan_part.md` | coding · `f4e5d92f` | A4 `26012f8`, 251 lines | 16 added, 146 removed | O | PP's shorter 85-line version was not upstreamed: 127 of its 146 removed lines remain in v2; 15 added lines in neither. Owner A4. |
| 13 | `commands/pr_ready.md` | coding · `ffc2a2d8` | A6 `4ec669e`, 74 lines | 67 lines | M | Register A6: PP's Steps 0a–0c go to PP-local `pr_ready.checks`, which S9 writes; the file does not exist yet. The old review steps (54 lines) are A6's rewrite. Closes at S9. |
| 14 | `commands/reference/plan_part_composition.md` | coding · `6cd286f7` | A4 `26012f8`, 31 lines | 6 lines | O | PP's rationalisation rows in neither; no register row. Owner A4. |
| 15 | `commands/reference/workstation_setup_land_runner.md` | pure · `ffc2a2d8` | A10 `1b3cfc2`, 37 lines | 26 lines | O | Runner label and registration checks in neither; the register's `/workstation_setup` rows cover phases, not this reference. Owner A10. |
| 16 | `commands/review_pr.md` | coding · `ffc2a2d8` | A6 `4ec669e`, 52 lines | 50 added, 51 removed | D | Register A6 drops or rewires the PP steps: 1c classify, 1d memory search, 5 regression gate, agent templates (A5). Line-level check not done. |
| 17 | `commands/sync_subsystems.md` | coding · `3b8ee0d6` | A10 `5f59322`, 59 lines | 5 lines | U | Register A10: Step 0 and Step 1 replaced by `reserved_paths:` (T7). |
| 18 | `commands/workstation_setup.md` | pure · `1302c0d1` | A10 `afe600d`, `1b3cfc2`, 267 lines | 88 lines | O | 7 in v2; 12 in PP-local `reference/workstation_setup_toolchain.md`; 62 in neither. Register covers phases only. Owner A10. |
| 19 | `gitignore.harness` | pure · `ffc2a2d8` | **B12, this lane** (residue, commit A) | 2 lines removed | U | PP removed `.claude/tests/` and its comment (`14cb8b4a4`). The exclusion ships in shared `searchignore.harness` (`ef4727a`), so v2 takes PP's bytes. The v1 test `test_baseline_sync_doctor.py` uses its own fixture, not this file. |
| 20 | `hooks/README.md` | pure · `3b8ee0d6` | deleted, B13 `6c3004b` | 35 added, 27 removed | D | Register B13 R1–R3: doctrine dropped. Its one live-hook fact moves to B14's `/v2/hook_registrations.md`, which is not written yet (open under B14, row 22–26). |
| 21 | `hooks/git_guardrails.py` | pure · `9aceeed` | S5 `c3807db` (10 added, 4 removed), i.e. v2 = base plus floor change | 385 added, 681 removed | O | 677 of the 681 lines M1 removed (advisories, the `harness_verdict` proof gate) are still in v2, so M1's relief is absent. A1 register H18 drops those checks, but no open lane writes this file: `v2-S5b` (floor rework, not merged) does not touch it. Rests on `v2-S5b`. Owner unassigned; orchestrator to name one. |
| 22 | `hooks/post_edit_dispatch.py` | pure · `9aceeed` | none: v2 == base | 1 added, 13 removed | O | 13 removed lines still wired. B14 landed two proof-test commits (`a4e8cca`, `f6a5d68`), not this rewrite. Owner B14. |
| 23 | `hooks/post_read_dispatch.py` | pure · `9aceeed` | none: v2 == base | 8 added, 55 removed | O | 55 removed lines still in v2. Owner B14. |
| 24 | `hooks/pre_edit_dispatch.py` | pure · `9aceeed` | none: v2 == base | 2 added, 23 removed | O | Still names `pattern_enforcer` at :61, a policing member M1 unwired. Owner B14. |
| 25 | `hooks/pre_read_dispatch.py` | pure · `9aceeed` | none: v2 == base | 16 added, 109 removed | O | 109 removed lines still in v2. Owner B14. |
| 26 | `hooks/prompt_dispatch.py` | pure · `9aceeed` | none: v2 == base | 1 added, 8 removed | O | 8 removed lines still in v2. Owner B14. |
| 27 | `hooks/pre_workflow_dispatch.py` | pure · `9aceeed` | B3 `96a44b4` (via `v2-B3c`), 282 lines, launch checks through `launch_check.py` | 14 added, 24 removed | U | 23 of 24 M1 removals applied. 12 of 14 M1 additions describe members the rewrite drops; superseded by the one launch-check path (B1, `launch_check.py` in v2). Checked against v2 tip: `e3adbe6` does not touch this file. |
| 28 | `reference/semantic_search.md` | coding · `ffc2a2d8` | A1 `57926fc`, 11 lines | 5 added, 4 removed | O | Routing, vault roots and the `.cs` scope are restated (register A1 R6, K7, G7). Residual: 4 PP install facts (DreB plugin version, busy-root rule) in neither. Owner A1. |
| 29 | `rules/harness_tooling.md` | pure · `9aceeed` | A1 `83a9dae`, 54 lines | 5 added, 6 removed | U | Register A1: `close growth` line dropped (H14), commit-proof rule carried (H16), `git_guardrails` quick checks dropped (H18), machine-slot rule carried (H22). |
| 30 | `scripts/validate_commands.py` | coding · `1e1cd7e0` | none: v2 == base. C2 merged (`fbfd9ac`) without touching it | 61 added, 80 removed | O | v2 still names `tools/lens_briefs.py` (:70) and `model_registry` (:461–488, the `seats.json` cut). 42 PP lines in neither. Owner C2. Forked in June, not by M1 (§1). |
| 31 | `skills/_brainstorm_shared/common.md` | coding · `ffc2a2d8` | A4 `26012f8`, 33 lines | none | U | PP's bytes are identical to the fork base (`cmp`), so there is no local change. The `forked` mark is stale; PP's lock should drop it. |
| 32 | `skills/_brainstorm_shared/design_contract.md` | coding · `6cd286f7` | A4 `26012f8`, 37 lines | 1 added, 1 removed | U | The one PP cross-link is re-pointed to `/plan`, `/drive`, `/review` by A4 (`26012f8`). |
| 33 | `skills/_brainstorm_shared/doc_conventions.md` | coding · `ffc2a2d8` | A4 `26012f8`, 86 lines | 5 added, 5 removed | O | PP path tiebreakers (PushinPotions vs Jmodot roots, game and framework rows) in neither; the draft CLAUDE.md holds only the roots. Owner A4. |
| 34 | `skills/_brainstorm_shared/roadmap_schema.md` | coding · `c7a103f6` | A4 `26012f8`, 193 lines | 34 added, 34 removed | O | PP scope values (`pp-game`, subsystem and feature examples) in neither. Owner A4. |
| 35 | `skills/architecture_brainstorm/SKILL.md` | coding · `f4e5d92f` | A4 `26012f8`, 265 lines | 38 added, 155 removed | O | PP's 98-line version not upstreamed: 146 of its removed lines remain in v2; PP's trigger lists and game examples in neither. Owner A9 (shared skills), with A4's rewire. |
| 36 | `skills/architecture_brainstorm/reference/part_authoring.md` | coding · `ffc2a2d8` | A4 `26012f8`, 151 lines (= base) | 111 added, 93 removed | O | 106 PP-added lines (Part-authoring mechanics) in neither; 91 removed lines still in v2. Owner A9. |
| 37 | `skills/architecture_philosophy/SKILL.md` | coding · `324163ce` | A9 `7aa2a28`, 95 lines | 91 added, 111 removed | O | Removals upstreamed. 89 PP-added lines (PP's project examples and rules) in neither and no register row. Owner A9. |
| 38 | `skills/architecture_philosophy/structure_rules.md` | coding · `3b8ee0d6` | A9 `7aa2a28`, 48 lines | 147 added, 83 removed | O | Register A9 sends R2, R5, R13 and the folder alias table to PP-local (H5); 134 PP lines in neither, and H5's target file is not in the PP tree. Owner A9. |
| 39 | `skills/debugging/SKILL.md` | coding · `3b8ee0d6` | A9 `c357073`, 58 lines | 23 added, 10 removed | O | Godot symptom-router line moved to PP-local `reference/symptom_router_godot.md` (1 line verified); 21 PP lines in neither. Owner A9. |
| 40 | `skills/debugging/reference/phase1-loop.md` | coding · `ffc2a2d8` | A9 `c357073`, 42 lines | 20 added, 16 removed | O | 20 PP lines (disposal and pool buckets: Godot lifecycle) in neither. Owner A9. |
| 41 | `skills/debugging/reference/phase2-reproduce.md` | coding · `ffc2a2d8` | none: v2 == base | 18 added, 16 removed | O | Godot and `JmoLogger` items in neither; A9 did not rewrite the file. Owner A9. |
| 42 | `skills/debugging/reference/phase4-hypothesise.md` | coding · `ffc2a2d8` | none: v2 == base | 13 added, 16 removed | O | 13 PP lines in neither; A9 did not rewrite the file. Owner A9. |
| 43 | `skills/debugging/reference/phase6-cleanup.md` | coding · `ffc2a2d8` | A9 `c357073`, 19 lines | 6 added, 6 removed | O | `[DIAG-` grep over `.cs` and scene cleanups in neither. Owner A9. |
| 44 | `skills/obsidian_conventions/SKILL.md` | pure · `3b8ee0d6` | A10 `afe600d`, 82 lines | 5 added, 5 removed | M | Vault roots are project values in `drafts/PushinPotions.CLAUDE.md` §Design Source (register A1 M7). |
| 45 | `skills/testing/advanced.md` | godot · `3b8ee0d6` | deleted, A9 `1eedb74` | 1 line | M | Q1 ruling: Godot half is PushinPotions-local. Register A9 H1: PP keeps its bytes at the same path. |
| 46 | `skills/testing/reference/authoring.md` | godot · `3b8ee0d6` | deleted, A9 `1eedb74` | 11 lines | M | As row 45 (register A9 H1). |
| 47 | `skills/testing/scene_runner.md` | godot · `3b8ee0d6` | deleted, A9 `1eedb74` | 5 lines | M | As row 45 (register A9 H1). |
| 48 | `skills/worklog_reference/SKILL.md` | pure · `e750aa3f` | A8 `81adbc6`, `63206e3`, 121 lines | 29 added, 39 removed | M | Register A8: PP domain rows go to `adaptation.json` `worklog_domains` (key exists in `tools/adaptation.py`, B6). PP's values are written at S9. |
| 49 | `tools/machine_slots.py` | pure · `9aceeed` | C2 `3ad8fea`, 418 lines (= PP's M1 bytes) | 6 added, 64 removed | U | M1 relief intact: no `PYTHONPATH`, `NODE_OPTIONS` or `BASH_ENV` injection. C2 removed `scripts/_slot_bootstrap/`. |

## 3. Counts and the done-condition

- **Rows:** 49, one per forked file. **Shared v2 versions:** 43 files have one at `template/.claude/<path>`. Six have none, because their owner deleted them: rows 4, 5, 20, 45, 46, 47 (deletes are named in §2).
- **Disposition:** U 9 (rows 1, 8, 17, 19, 27, 29, 31, 32, 49), M 7 (5, 13, 44, 45, 46, 47, 48), D 3 (4, 16, 20), O 30 (rows 2, 3, 6, 7, 9–12, 14, 15, 18, 21–26, 28, 30, 33–43). Total 49. Row 13 is M with its destination written at S9; row 5 is M by the Q12 move. Both are counted as resolved.
- **Done-condition** ("every forked file has exactly one v2 version and one row; no row is left open"): **not met.** Rows 1–49 each have one row, but 30 are open. This audit does not change owner files (its write set is this file plus the two residues), so the open rows go back to their owners (§5).

## 4. Rows that rest on unmerged lanes

- **`v2-S5b`:** row 21 (`git_guardrails.py`). S5b does not write this file. The V1 loss-floor redesign (P:214) names it, so an owner has to be assigned before the cutover.
- **`v2-TB3`:** no row rests on it. Its branch is already an ancestor of `b183746`, so the brief's list is out of date for TB3.
- **`v2-B3d`:** no row changes on it. Its commit `e3adbe6` is at the v2 tip, after this base, and touches none of the forked files.

## 5. Owner returns (files outside B12's write set that need a change)

- **B14:** rows 22–26, the five dispatchers. v2 holds base bytes and still wires the policing members. Apply M1's bytes first (Q3), then the Q13 shape (one dispatcher per event, members in `adaptation.json`). Also write `/v2/hook_registrations.md` (row 20's fact).
- **C2:** row 30, `scripts/validate_commands.py`. Cut `model_registry` (to `seats.json`) and `tools/lens_briefs.py`.
- **S5 / unassigned:** row 21, `hooks/git_guardrails.py`. Apply M1's bytes and keep the loss checks only, or assign it to S5b's write set.
- **A1:** row 28, `reference/semantic_search.md` (4 install facts: a home or a drop).
- **A4:** rows 12, 14, 33, 34 (PP-local rows of the brainstorm files and `/plan_part` not placed).
- **A5:** rows 6, 7 (PP-local checklist items not moved).
- **A9:** rows 35–43 (PP-local skill bodies not placed; `phase2-reproduce` and `phase4-hypothesise` not rewritten). Row 35 needs A4's rewire as well.
- **A10:** rows 2, 3, 9–11, 15, 18 (commands whose PP-local bodies are not in the register).
- **A6 / S9:** row 13 closes when S9 writes `pr_ready.checks`. Row 16 is covered by register rows; a line check is still owed.

## 6. What this audit did and did not check

- Line presence is exact after whitespace trim, for lines of 25 characters or more. A line counted as "in neither" may be reworded rather than dropped; the owner has to say which.
- Register rows are taken from `v2/carried_dropped/*.md` as written. I did not re-derive them.
- Forked files that owners rewrote from base rather than from PushinPotions' bytes are counted by their diff, not by intent.

## Progress notes (pre-compaction, unverified re-read)

Checked (summary record; re-read before citing):
- .claude/baseline.lock.json at origin/main d9f2cbe1a: 125 forked rows; intersect with appendix A.1 shared class = 49 (map says 49; plan's 39 differs).
- Nine of ten extras first forked at 54773b267 ("v2 M1 unwire the policing hooks"); scripts/validate_commands.py first forked at 4ed935825 (2026-06-11), so M1 inference fails for it.
- template/.claude/gitignore.harness equals PushinPotions bytes now; diff removes exactly `.claude/tests/` and its "# Semantic search skips gitignored files" comment; exclusion lives in template/.claude/searchignore.harness (ef4727a).
- hooks/git_guardrails.py at v2 keeps 677 of 681 M1-removed lines; S5 commit c3807db is 10 insertions, 4 deletions; v2-S5b does not touch it.
- template/.claude/hooks/{pre_edit_dispatch,post_edit,post_read,pre_read}.py and prompt_dispatch.py byte-identical to base; B14 landed only a4e8cca and f6a5d68 (tests); B14 worktree clean.
- template/.claude/scripts/validate_commands.py names tools/lens_briefs.py (:70) and model_registry (:461-488); C2 did not rewrite it.
- template/.claude/tools/machine_slots.py equals PushinPotions M1 bytes; grep for PYTHONPATH, NODE_OPTIONS, BASH_ENV found nothing.
- skills/_brainstorm_shared/common.md identical to fork base (cmp); forked mark stale.
- hooks/pre_workflow_dispatch.py rewritten by B3 (96a44b4); 23 of 24 M1 removals applied.
- Lane branches: v2-S5b ahead 4, unmerged; v2-B3d merged at tip e3adbe6; v2-TB3 already ancestor of b183746.

Not yet done: commit gitignore.harness; commit this file with "Replaces:" line; run template/.claude/tests/test_baseline_sync_doctor.py; record serial test seconds; owner returns for 30 open rows.
