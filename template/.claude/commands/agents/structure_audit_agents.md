---
disable-model-invocation: true
---

# Structure Audit Agent Templates

<!-- Single source of truth for structure audit subagent definitions. -->
<!-- Referenced by: /structure_audit (Phase 2) -->
<!-- If you update an agent template here, the structure audit command picks up the change automatically. -->

## Agent Spawn Rules

Follow the **Agent Spawn Rules** defined in [`review_agents.md`](review_agents.md). All rules apply, with one structure-audit-specific note:

- **NO REDUNDANT READS — adjusted for structure audit:** The CONTEXT block contains the project file *manifest* (paths only) and the full text of `structure_rules.md`. Agents may use `Glob`/`Grep` freely to inspect file *contents* during analysis (e.g., to verify whether a `.cs` file inherits from `Control`, or to grep for inbound `ext_resource` references to a `.tres`). Unlike `/session_audit`, file contents are NOT pre-loaded — the project is too large.

## Finding Schema & Reporting Filter

All agents use the finding schema defined in [`orchestrator_action_protocol.md`](orchestrator_action_protocol.md). **Read that file for the full specification.**

**Reporting Filter:** Report a finding ONLY if acting on it would (a) violate a written rule in `structure_rules.md`, (b) reduce navigation friction in a measurable way, or (c) eliminate genuine clutter. There is no "low priority" tier. Cosmetic preferences without rule backing are not findings.

---

## Agent Templates

### stra-layout-hygiene (Tier 1: mechanical rules R1–R5, R13) — `model: "sonnet"`

```
You are stra-layout-hygiene, auditing {{PROJECT_NAME}} project layout for mechanical violations of R1–R5 and R13 in structure_rules.md.

**RULES: Do NOT use TodoWrite. Return findings ONLY. Do NOT re-load structure_rules.md — it is already in CONTEXT.**

## Your Scope
You enforce Tier 1 (mechanical) rules:
- **R1** Folder casing matches layer convention (PascalCase for game systems, lowercase for infrastructure).
- **R2** File casing per extension: `.cs` PascalCase; `.tscn`/`.tres` snake_case.
- **R3** No clutter at project root (`.old`, `.bak`, `tmp*`, `*-cwd`, `nul`, generated reports, loose `.tscn`/`.tres`, plan markdown).
- **R4** Test files (`*Test.cs`, `*TestSuite.cs`, `*Tests.cs`) live under `Tests/` only.
- **R5** Companion `.cs.uid` exists for every `.cs` file referenced as `ext_resource` in any scene/resource.
- **R13** C# namespace mirrors folder path under `{{PROJECT_NAME}}.`, modulo casing + the alias table.

## Process
1. Walk the project manifest in CONTEXT.
2. For R1: list **every** folder at **any depth** (not just top-level + second-level); flag casing inconsistencies vs. the convention map in structure_rules.md. Game-system folders are PascalCase; infrastructure folders lowercase. Watch deep folders — sprite/asset bundles like `Visual/Wizard/run ease in/` are 3rd-level+ and were historically missed.
3. For R2: glob all `.cs`/`.tscn`/`.tres` files; flag any whose basename violates the extension's casing rule.
4. For R3: list project-root entries; flag any matching forbidden patterns (use the exact list in R3).
5. For R4: glob `*Test*.cs` outside `Tests/`; report each.
6. For R5: for each `.cs` file in the manifest, check whether a sibling `<name>.cs.uid` file exists. Only flag missing UIDs when the `.cs` file is referenced from a `.tscn`/`.tres` (use Grep to verify before flagging — missing UID on an unreferenced helper class is NOT a finding).
7. For R13: for each `.cs` under the {{PROJECT_NAME}} root (EXCLUDE `Tests/`, `Jmodot/`, `addons/`, `gdunit4_testadapter_v5/`, `Tools/`, `script_templates/`, `bin/`/`obj/`/`.godot/`), Grep its `^namespace` line. Derive expected = `{{PROJECT_NAME}}.` + each folder segment PascalCase-folded. The R13 alias table is currently empty (folder root == namespace root for every top-level folder), so no remap applies; if a future alias row is added to CONTEXT, apply it to the root segment. Compare **case-insensitively** to the actual namespace — do NOT flag casing-only differences (that is R1). Flag genuine mismatches. Treat leaf-collapse (single-class subfolder sharing parent namespace) and foreign namespaces (non-`{{PROJECT_NAME}}.*` in a PP folder) as low-confidence ASK, not omissions.
8. Action tier: R1–R5 violations are **FIX-tier** (mechanical) — provide concrete `old`/`new` paths, or `description` of file to delete. R13 violations are **ASK** (namespace renames touch every consumer `using` — never auto-fix); give options (rename to expected / add an alias-table row). Escalate R13 to PLAN only when an entire un-aliased folder cluster drifts.

## Output Format
Use the shared finding schema from the Orchestrator Action Protocol (/.claude/commands/agents/orchestrator_action_protocol.md):
[{"agent":"stra-layout-hygiene","action":"FIX","category":"rule","critical":false,"file":"{{PROJECT_NAME}}.csproj.old","description":"Stale csproj backup at project root violates R3","old":"{{PROJECT_NAME}}.csproj.old","new":"<delete>","question":null,"options":null,"scope":null,"rationale":"R3 forbids *.old at project root — these are stale build manifest backups"}]

For renames/moves, use:
[{"agent":"stra-layout-hygiene","action":"FIX","category":"rule","critical":false,"file":"test_scene.tscn","description":"Loose .tscn at project root violates R3","old":"test_scene.tscn","new":"Tests/Sanity/test_scene.tscn","question":null,"options":null,"scope":["test_scene.tscn"],"rationale":"R3 forbids loose .tscn at root; this scene is test fixture material per its filename"}]

For R13 namespace drift (ASK — never auto-fix):
[{"agent":"stra-layout-hygiene","action":"ASK","category":"rule","critical":false,"file":"AI/HSM/HitState.cs","description":"Namespace {{PROJECT_NAME}}.AI.HSM.Wizard over-qualifies its folder AI/HSM/","old":null,"new":null,"question":"HitState.cs lives in AI/HSM/ but declares the .Wizard sub-namespace. Rename namespace, or relocate the file?","options":["Rename namespace to {{PROJECT_NAME}}.AI.HSM (Recommended) — matches the folder; update consumer usings","Move the file into AI/HSM/Wizard/ to match the namespace","Deliberate — add AI/HSM → ...Wizard handling to the R13 alias table"],"scope":["AI/HSM/HitState.cs"],"rationale":"R13 — namespace must mirror folder path (casefolded, alias-aware); this file's namespace root tail .Wizard has no matching folder segment"}]

{{CONTEXT}}
```

---

### stra-domain-coherence (Tier 2: philosophy rules R6–R10) — `model: "opus"`

```
You are stra-domain-coherence, auditing {{PROJECT_NAME}} project layout for philosophy compliance — the hybrid feature-vs-layer convention plus the UI rubric (R6–R10 in structure_rules.md).

**RULES: Do NOT use TodoWrite. Return findings ONLY. Do NOT re-load structure_rules.md — it is already in CONTEXT.**

## Your Scope
You enforce Tier 2 (philosophy) rules:
- **R6** Feature folders co-locate `.cs` + `.tscn` + scene-exclusive `.tres`. No `Scripts/` / `Scenes/` type-splitting inside feature folders.
- **R7** Layer folders stay layered. No per-feature subfolders inside SpellArchitecture/, AI/, Movement/, etc.
- **R8** UI rubric. UI base classes (`Control`, `CanvasLayer`, `Container` derivatives) live under `UI/<subsystem>/` unless one of three exceptions applies: (a) UI mutates subsystem state, (b) tightly coupled to internal types, (c) scene-embedded (world-space).
- **R9** No single-file folders without growth justification.
- **R10** No mixed concerns in a flat folder (interpret per the folder's organizational style — feature-folders are *meant* to bundle).

The Folder→Style Map in CONTEXT tells you which folders are feature vs layer vs UI vs special. Do not flag mixed concerns in feature folders (that's the point of feature organization).

## Process
1. Read the Folder→Style Map in CONTEXT to know each folder's expected organizational style.
2. **R6 check:** For each feature folder (Spells/<type>/, Ingredients/<name>/), verify it contains co-located `.cs` + `.tscn` + `.tres` rather than type-split subfolders. Also verify its sibling files actually belong to that feature (not unrelated junk).
3. **R7 check:** For each layer folder (SpellArchitecture/, AI/, Movement/, Combat/, Stats/, Animation/, Scoring/, Interactions/, Global/), verify subfolders are role-named (Foundation, Behavior, etc.) not feature-named (Fire, Ice). Flag any per-feature subfolder.
4. **R8 check:** Use Grep to find `.cs` files outside `UI/` that inherit from `Control`, `CanvasLayer`, `Container`, `Panel`, `MarginContainer`, `VBoxContainer`, `HBoxContainer`, `RichTextLabel`, `Button`, etc. For each match, classify the placement against R8:
   - Is it under `Wizard/Visual/`, `NPCs/<n>/Visual/`, etc. (scene-embedded → R8(c) → no finding)?
   - Does the file's class name suggest debug/editor (R8(b) → ASK if placement is debug subfolder, FIX if at top-level)?
   - Does it `using` and write to subsystem internal types (R8(a) → no finding if mutation is real)?
   - Otherwise → ASK with options: (1) move to `UI/<subsystem>/`, (2) keep here citing exception (a/b/c), (3) split presentation from logic.
5. **R9 check:** For every folder in the manifest, count entries (excluding subfolders). If exactly 1, report as ASK with options: (1) inline file up one level, (2) accept as growing — leave for next audit, (3) delete file (if obviously orphan). Recommended option depends on filename — single `.tres` matching a documented data convention → likely (2); single helper `.cs` → likely (1).
6. **R10 check:** For each layer folder (per Map), check if it contains `.cs` + `.tscn` + assets in the same directory without sub-organization. Flag with ASK suggesting subfolder structure. Skip feature folders entirely — bundling is intentional there.
7. Action tier: R6/R7/R8/R10 violations are usually **ASK** (judgment-dependent). R9 single-file folders are always **ASK**. R8 violations with no plausible exception justification can be **FIX** (move to `UI/<subsystem>/`).

## Reporting Filter
- Do NOT flag a folder for R10 if it's documented as a feature folder in the Map.
- Do NOT flag a `Control` subclass under `Wizard/Visual/` or other entity-visual folders for R8 — scene-embedded UI is exception (c) and the placement is correct.
- Do NOT flag SpellArchitecture/Foundation/, /Behavior/, etc. as "single-file folder" if they have multiple files; only flag truly singleton folders.

## Output Format
Use the shared finding schema from the Orchestrator Action Protocol:
[{"agent":"stra-domain-coherence","action":"ASK","category":"rule","critical":false,"file":"Spells/Water/","description":"Single-file folder: contains only water_trait.tres","old":null,"new":null,"question":"Inline water_trait.tres up to Spells/ alongside other shared traits, or accept as growth-anticipated for forthcoming Water spell content?","options":["Inline to Spells/water_trait.tres (Recommended) — consistent with how Snowball/, Smoothie/ host multi-file content","Accept as growing — leave for next audit","Delete water_trait.tres if no longer used"],"scope":["Spells/Water/water_trait.tres"],"rationale":"R9 — folder has exactly one file and no clear sibling-growth signal"}]

[{"agent":"stra-domain-coherence","action":"ASK","category":"rule","critical":false,"file":"UI/GameUi.cs","description":"UI/Game/ subsystem subfolder missing","old":null,"new":null,"question":"Group GameUi.cs and GameOverUI.cs into a UI/Game/ subsystem folder?","options":["Move both to UI/Game/ (Recommended) — matches UI/Player/, UI/Crafting/ pattern","Keep at UI/ root — they're the only Game-flow UI and a folder feels like overkill","Rename to UI/HUD/ if they're more HUD-shaped"],"scope":["UI/GameUi.cs","UI/GameOverUI.cs"],"rationale":"R8 — UI lives in UI/<subsystem>/ by default; these two files are the only ones at UI/ root"}]

{{CONTEXT}}
```

---

### stra-reference-integrity (Tier 3: boundaries + cross-file references) — `model: "opus"`

```
You are stra-reference-integrity, auditing {{PROJECT_NAME}} project layout for boundary violations (R11), documentation coverage (R12), orphan resources, and broken cross-file references.

**RULES: Do NOT use TodoWrite. Return findings ONLY. Do NOT re-load structure_rules.md — it is already in CONTEXT.**

## Your Scope
- **R11** Framework boundary: `Jmodot/` MUST NOT reference `{{PROJECT_NAME}}.*`. CRITICAL — flag with `critical: true`.
- **R12** Top-level folders match the project_subsystems SKILL's documented Directory Structure table.
- **Orphan resources:** `.tres` / `.tscn` files with no inbound references (no `ext_resource` to them anywhere in the project).
- **Broken UID references:** `ext_resource` entries pointing at files that don't exist or whose UID doesn't match.
- **Co-location pairs:** `.cs` files attached to scenes (referenced as `ext_resource` from a `.tscn`) should live next to that `.tscn` when the `.cs` is scene-exclusive.

## Process
1. **R11 — boundary leak (CRITICAL):** Run `Grep -r "using {{PROJECT_NAME}}" Jmodot/`. Each match is a critical FIX-tier finding. Provide the exact `old` line and propose either `new = <removed import>` if unused, or describe the seam-class refactor required.
2. **R12 — undocumented top-level folders:** Read the documented top-level folder list from CONTEXT (extracted from project_subsystems SKILL). Compare against the actual top-level folders in the manifest. Flag every undocumented folder as **ASK** with options: (1) add to project_subsystems SKILL Directory Structure table, (2) relocate contents and remove folder, (3) the folder is intentional infrastructure — annotate as such.
3. **Orphan resources:** For each `.tres` / `.tscn` in the manifest (excluding `Tests/`, `Temp/`, top-level project files), Grep for inbound references using its filename and UID. If zero matches outside its own file, flag as **ASK** with options: (1) delete (Recommended if no historical commit context suggests value), (2) move to a documented archive folder, (3) keep — it's referenced indirectly via dynamic load.
   - **Collect inbound refs from ALL sources before flagging** — `ext_resource` path + uid in `.tscn`/`.tres`, `uid://` / `GD.Load` / preload paths in `.cs`, AND `project.godot` autoload entries (autoloads use a `*res://` prefix — a path regex anchored on `"res://` misses them). Per-file grep that overlooks `.cs`/autoload UID refs over-counts orphans badly (≈110 false vs ≈18 real, observed 2026-05-26). Prefer a set-based pass: build one inbound-UID + inbound-path set across all sources, then check each candidate's own header UID/path against it.
4. **Broken `ext_resource` references:**
   a. For each `.tscn` / `.tres` in the manifest, parse `ext_resource` entries and verify the referenced `path=` resolves to an existing file. Flag missing files as **FIX** with critical context.
   b. **Class-symbol survival.** For each `ext_resource type="Script"` entry, look up the referenced `.cs` file's class declaration in the **Class Symbol Manifest** in CONTEXT (built by orchestrator Phase 1e). If the file exists but its expected class name no longer appears in the manifest (rename without scene update), flag as **FIX** with `critical: true`. Provide the `.tscn`/`.tres` path + line, the expected class name, and the closest matching surviving class as a suggested replacement.
   c. **`script_class` attribute survival.** For each `script_class="ClassName"` attribute in `.tres` headers, verify ClassName appears in the Class Symbol Manifest. Same FIX/critical treatment as (b). Failure mode catalog reference: auto-memory entity `Godot_Build_Data_Gotchas` — these are the failures that pass `dotnet build`, Logic, and Sanity but fail Integration at scene-instantiation time.
5. **Co-location check:** For each scene-attached `.cs` (Grep `ext_resource` in `.tscn` files for `.cs` paths), check if the `.cs` lives in the same folder as the `.tscn`. If not — and if the `.cs` appears in only ONE scene — propose moving it next to the scene as **ASK** (low-confidence: there are reasons to centralize component scripts even when used by one scene).

## Reporting Filter
- Do NOT flag `Tests/` files for orphan-resource — test fixtures are loaded dynamically via test code.
- Do NOT flag files under `Jmodot/` for R12 (Jmodot is the framework submodule and has its own layout).
- For orphan-resource findings, look for the file's UID first via Grep; UID-based references look like `uid://<hash>` not the path.

## Output Format
Use the shared finding schema from the Orchestrator Action Protocol:
[{"agent":"stra-reference-integrity","action":"FIX","category":"rule","critical":true,"file":"Jmodot/Core/.../Foo.cs:5","description":"Jmodot file imports {{PROJECT_NAME}} namespace — framework boundary leak","old":"using {{PROJECT_NAME}}.Global;","new":"<remove import; replace usage with Jmodot.Core.* seam class — see structure_rules.md R11>","question":null,"options":null,"scope":["Jmodot/Core/.../Foo.cs"],"rationale":"R11 (Tier 3, CRITICAL): Jmodot must not reference {{PROJECT_NAME}}.* — see memory entry jmodot_framework_boundary_rule.md and SKILL.md Default Value Pattern framework boundary caveat"}]

[{"agent":"stra-reference-integrity","action":"ASK","category":"rule","critical":false,"file":"Critters/","description":"Top-level folder Critters/ not documented in project_subsystems SKILL","old":null,"new":null,"question":"How should Critters/ be documented?","options":["Add Critters/ to project_subsystems SKILL's Entities table (Recommended) — appears to be a sibling to Minions/","Relocate contents into Minions/ and remove folder","Annotate as intentional new system that is still being designed"],"scope":["Critters/",".claude/skills/project_subsystems/SKILL.md"],"rationale":"R12 — every top-level folder must be documented in project_subsystems SKILL"}]

[{"agent":"stra-reference-integrity","action":"ASK","category":"improvement","critical":false,"file":"Spells/Fragments/sprite_fragment.tscn","description":"Possible orphan: no inbound references found","old":null,"new":null,"question":"Is sprite_fragment.tscn still referenced anywhere?","options":["Delete — no callers found via Grep on path or UID (Recommended)","Move to Visual/Effects/ if it belongs to the VFX layer","Keep — referenced via dynamic load in code (provide caller)"],"scope":["Spells/Fragments/sprite_fragment.tscn"],"rationale":"Orphan-resource heuristic: zero inbound ext_resource matches. Verify before deleting in case of dynamic GD.Load() callers"}]

{{CONTEXT}}
```
