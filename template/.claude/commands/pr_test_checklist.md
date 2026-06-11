---
disable-model-invocation: false
allowed-tools: Bash(git log:*), Bash(git diff:*), Bash(git diff-tree:*), Bash(git branch:*), Bash(git rev-parse:*), Bash(git show:*), Bash(git -C Jmodot *), Bash(wc *), Glob, Grep, Read, mcp__obsidian__obsidian_list_notes, mcp__obsidian__obsidian_read_note, mcp__obsidian__obsidian_update_note, mcp__obsidian__obsidian_global_search
description: "Generate or update a PR playtest checklist in Obsidian"
---

Generate or update a **PR Gameplay Test Checklist** in Obsidian from the current branch's commits. The checklist targets manual playtesting — observable behavior, feel, and visual verification. Logic-domain correctness is covered by TDD and excluded.

**Prerequisite:** The [Task Genius](https://github.com/taskgenius/taskgenius-plugin) Obsidian community plugin must be installed with **heading progress bars** and **reading mode** enabled for per-section progress tracking.

## Arguments

`$ARGUMENTS` — optional display title override.
- `/pr_test_checklist` — auto-derive title from branch name
- `/pr_test_checklist "Critter System"` — use "Critter System" as the document title

## References

- [PR Test Checklist Conventions](agents/pr_test_checklist_conventions.md) — commit classification, document template, checkbox writing guidelines, stale value detection
- `obsidian_conventions` skill — MCP connectivity, vault paths, writing rules (auto-loads on vault work)
- [PR Classification](agents/pr_classification.md) — domain classification for commit filtering

Read the two file-based reference docs before proceeding; the `obsidian_conventions` skill auto-loads.

---

## Phase 1: Setup

### 1.1 Detect Branch

```bash
git branch --show-current
```

**Abort** if on `main`. This command only works on feature branches.

### 1.2 Derive Filename

Strip the `claude/` prefix from the branch name, replace `/` with `-`.

| Branch | Filename |
|--------|----------|
| `claude/critter-scurry-ingredients-bVx4D` | `critter-scurry-ingredients-bVx4D` |
| `claude/spell-fire-rework-Abc12` | `spell-fire-rework-Abc12` |
| `feature/new-ui` | `feature-new-ui` |

### 1.3 Determine Mode

```
Glob("*", path="{{VAULT_ROOT}}/DevProjects/{{PROJECT_NAME}}/Claude/TODO/PRTesting")
```

- File `{filename}.md` exists → **UPDATE mode** (read existing doc first)
- File doesn't exist → **CREATE mode**

### 1.4 Gather Branch Metadata

```bash
git log main..HEAD --oneline | wc -l          # commit count
git diff main..HEAD --stat | tail -1           # files changed, LOC summary
```

---

## Phase 2: Commit Analysis

### 2a. Gather Commits

**CREATE mode:**
```bash
git log main..HEAD --format="%h|%s|%ai"
```

**UPDATE mode:** Same command, but filter to commits **after** the last changelog date in the existing document. Parse the Changelog table's most recent date entry.

### 2b. Classify and Filter

Apply the classification rules from [PR Test Checklist Conventions](agents/pr_test_checklist_conventions.md) Section 1.

For each commit:
1. Parse the conventional commit prefix (`feat`, `fix`, `refactor`, `test`, `chore`, `docs`)
2. Run `git diff-tree --no-commit-id --name-only -r <hash>` to get touched files
3. **Include** if it has a gameplay-affecting prefix AND touches runtime files
4. **Exclude** if it only touches `Tests/`, `.claude/`, `skills/`, `docs/`, or is `test(*)`, `docs(*)`, `chore(meta)`, `chore(tests)`

### 2c. Include Jmodot Submodule Commits

Check if the diff shows a Jmodot pointer change:
```bash
git diff main..HEAD -- Jmodot
```

If the Jmodot pointer changed, get the old and new SHAs and analyze:
```bash
git -C Jmodot log <old_sha>..<new_sha> --format="%h|%s|%ai"
```

Include Jmodot `feat`/`fix` commits that add gameplay-facing APIs: BT actions, steering considerations, sensors, movement strategies, perception components. Exclude pure test/refactor/internal commits.

### 2d. Group into Sections

1. Extract scope from `type(scope): description`
2. Group by scope using merge heuristics from [Conventions](agents/pr_test_checklist_conventions.md) Section 2
3. Derive designer-friendly section titles (e.g., `AI` + `steering` commits → "AI & Steering Behavior")
4. Target 5-12 sections; merge or split to stay in range

### 2e. Read Source Files for Context

For each section, read the primary `.cs`, `.tres`, `.tscn` files to extract:
- `[Export]` properties and their current values
- Observable runtime behavior (log messages, state transitions)
- Concrete values for checkbox items (radii, weights, durations, thresholds)
- Scene structure (what nodes exist, what resources are assigned)

**This is critical.** Checkbox items must describe what the player/tester **sees and feels** in-game, using real values from the code — not implementation details, error-absence checks, or internal state.

---

## Phase 3: Generate or Update

### CREATE Mode

Build the full document using the template from [Conventions](agents/pr_test_checklist_conventions.md) Section 3:

1. **Title + metadata block** — branch name (or `$ARGUMENTS` override), commit count, file count, date
2. **Pre-Test Setup** — scene loading, `.tres` data resource verification (list each new resource file with expected key values), console clean check
3. **Numbered feature sections** — one per grouped scope. Each section has:
   - `> [!info]` callout with 1-2 sentence context
   - `> [!note]` callout for test setup if needed
   - `###` subsections with concrete checkbox items
4. **Summary Matrix** — per-feature row with Auto Tests column, Manual Test column, Progress column
5. **Deferred Items** — features that can't be tested yet (missing prerequisites, not wired)
6. **Known Issues** — pre-existing failures or oddities
7. **Changelog** — initial entry with date and summary

### UPDATE Mode

1. **Read existing document** via `obsidian_read_note`
2. **Parse structure:** Extract sections, subsections, and checkbox states (`- [x]` vs `- [ ]`)
3. **Generate new sections** for commits not yet covered
4. **Merge rules:**
   - **NEVER uncheck** a previously checked box (`- [x]` stays `- [x]`)
   - **NEVER remove** existing sections or checkboxes
   - Append new sections before the Summary Matrix
   - If a new commit modifies behavior already covered by an existing section, add new checkbox items to that section rather than creating a duplicate section
5. **Stale value detection:** Apply rules from [Conventions](agents/pr_test_checklist_conventions.md) Section 5. Read current source files, compare values in checkbox text, update if changed
6. **Update metadata:** Date, scope (commit count, file count)
7. **Regenerate Summary Matrix** to include new sections
8. **Append changelog entry** with today's date and summary of changes (new sections added, stale values corrected)

---

## Phase 4: Write and Report

### Write to Obsidian

```
obsidian_update_note:
  targetType: "filePath"
  targetIdentifier: "DevProjects/{{PROJECT_NAME}}/Claude/TODO/PRTesting/{filename}.md"
  modificationType: "wholeFile"
  wholeFileMode: "overwrite"
  createIfNeeded: true
  overwriteIfExists: true
```

### Report to Terminal

**CREATE mode:**
```
PR Test Checklist: CREATE
Branch:   {branch_name}
File:     DevProjects/{{PROJECT_NAME}}/Claude/TODO/PRTesting/{filename}.md
Sections: {N} test sections, {M} total checkboxes
Commits:  {C} gameplay-affecting commits analyzed ({T} total on branch)

Prerequisite: Install "Task Genius" plugin with heading progress bars + reading mode enabled.
```

**UPDATE mode:**
```
PR Test Checklist: UPDATE
Branch:   {branch_name}
File:     DevProjects/{{PROJECT_NAME}}/Claude/TODO/PRTesting/{filename}.md
Sections: {N} test sections, {M} total checkboxes
New:      {X} new commits, {Y} new sections added
Stale:    {Z} values corrected

Preserved all existing checkbox states.
```

---

## Rules

- Works for ANY branch
- **Idempotent** — running twice without new commits produces the same document
- **Never unchecks** a checkbox on update
- **Skips Logic-domain** behavior (TDD's job) — no checkboxes for pure code correctness
- Focuses on gameplay/feel/visual/behavioral verification only
- Follows the `obsidian_conventions` skill for vault tooling (native-first)
- Pure standard markdown — no plugin-specific syntax in generated docs (Task Genius auto-detects checkboxes)
- Callouts for context only — **never put checkboxes inside callouts**
