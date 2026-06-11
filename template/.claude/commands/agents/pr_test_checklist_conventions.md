---
disable-model-invocation: true
---

# PR Test Checklist Conventions

<!-- Single source of truth for commit classification, document template, and checkbox writing guidelines. -->
<!-- Referenced by: /pr_test_checklist -->

## 1. Commit Classification Procedure

### Include (gameplay-affecting commits)

| Prefix | Condition |
|--------|-----------|
| `feat(*)` | Any new feature |
| `fix(*)` | Changes runtime behavior |
| `refactor(*)` | Changes observable behavior |

### Exclude (non-gameplay commits)

| Prefix / Pattern | Reason |
|-------------------|--------|
| `test(*)` | Automated test changes only |
| `docs(*)` | Documentation only |
| `chore(meta)` | Meta/config changes |
| `chore(tests)` | Test infrastructure |
| Commits touching ONLY `Tests/`, `.claude/`, `skills/`, `docs/` | No runtime impact |

### Verification

For each commit, run `git diff-tree --no-commit-id --name-only -r <hash>` to confirm it touches runtime files (`.cs` outside `Tests/`, `.tres`, `.tscn`).

### Domain Classification

Reference [PR Classification](pr_classification.md) for domain classification rules (Logic, Gameplay, Data, Meta, Jmodot, Mixed). Only Gameplay, Data, Jmodot, and Mixed commits produce checklist items. Pure Logic commits are covered by TDD.

## 2. Section Grouping Rules

### Scope Extraction

Parse conventional commit format: `type(scope): description`
- Extract `scope` as the grouping key
- If no scope, derive from primary file path

### Merge Heuristics

**PROJECT-CONFIG:** the stack-generic rows below apply to any Jmodot game; add your game's content-scope rows (the bracketed examples show the shape — replace them).

| Related Scopes | Merged Section |
|----------------|---------------|
| `AI`, `steering`, `movement`, `BT` | AI & Movement |
| `perception`, `sensor`, `obstacle` | Perception & Avoidance |
| `VFX`, `visual`, `animation` | Visual Effects |
| `HSM`, `state`, `transition` | State Machine |
| `UI`, `dashboard`, `debug` | UI & Debug |
| *(your entity scopes, e.g. `critter`, `enemy`)* | *\<Entity> Behavior* |
| *(your content scopes, e.g. `spell`, `trait`, `item`)* | *\<Content> System* |

### Target Range

- **Minimum:** 3 sections (very small branch)
- **Target:** 5-12 sections (typical branch)
- **Maximum:** 18 sections (large multi-feature branch)

If commit count produces fewer than 3 sections, combine into broader categories. If more than 18, merge related scopes more aggressively.

## 3. Document Template

```markdown
# Branch Gameplay Test Checklist — `{branch_name}`

> **Purpose:** Manual gameplay testing for functionality, behavior, and feel.
> Logic-domain behavior is verified by TDD — this doc covers only eyes-on gameplay.
> **Branch:** `{branch_name}` | **Scope:** {N} commits, ~{M} files
> **Date:** {YYYY-MM-DD}

---

## Pre-Test Setup
- [ ] **Load and run scene**: Open `{primary_scene}`, run it, confirm all entities spawn and are active
- [ ] **Verify data resources**: Inspector-check each new `.tres` file listed below

---

## 1. Feature Section Title

> [!info] 1-2 sentence context about what this feature does and why it matters for gameplay.

> [!note] **Test setup**: Any special setup needed (spawn objects, position entities, etc.)

### 1.1 — Subsection Title
- [ ] Observable behavior with concrete expected outcome
- [ ] Interaction check: do X, observe Y happens

### 1.2 — Another Subsection
- [ ] Check item
- [ ] Check item

---

{repeat numbered sections}

---

## Summary Matrix
| Feature | Auto Tests | Manual Test | Progress |
|---------|:-:|:-:|:-:|
| Feature 1 | TDD | Manual | |

---

## Deferred Items
1. **Item** — reason deferred (missing prerequisite, not wired, etc.)

## Known Issues
| Issue | Severity | Notes |
|-------|----------|-------|

## Changelog
| Date | Changes |
|------|---------|
| {YYYY-MM-DD} | Initial checklist — {N} commits, {summary} |
```

### Template Rules

- Use pure standard markdown — no plugin-specific syntax needed (Task Genius auto-detects checkboxes under headings)
- Callouts (`> [!info]`, `> [!note]`, `> [!warning]`) for context/setup notes ONLY — **never** put checkboxes inside callouts
- Use `---` horizontal rules between major sections for visual separation
- Pre-Test Setup is always the first section (unnumbered)
- Feature sections are numbered starting at 1
- Summary Matrix, Deferred Items, Known Issues, and Changelog are always last (unnumbered)

## 4. Checkbox Writing Guidelines

### Philosophy

This checklist is for a **designer playtesting the game**. Every checkbox should describe something you **see, feel, or interact with** in-game. If the tester can't verify it by looking at the screen or performing an action, it doesn't belong here.

### What Makes a Good Checkbox

- Describes **observable positive behavior**: what the game DOES, not what it doesn't break
- Uses concrete values from the code: "Critter pauses 1-3 seconds between wander cycles"
- Is independently verifiable in a single action or observation
- Starts with a verb: "Observe...", "Walk into...", "Cast a spell and watch..."
- Describes the expected outcome, not the implementation

### What Does NOT Belong

- **Error-absence checks**: "No errors in console" — you can obviously see errors. Don't waste a checkbox on it.
- **Internal implementation details**: "BB key `Critter_Threatened` set to true" — the tester can't see BB keys
- **Code correctness**: "Method returns SUCCESS" — that's TDD's job
- **Redundant negative checks**: "No crashes", "No warnings", "No orphaned nodes"
- **Inspector/data audits**: "Verify `_penaltyMaxWeight = 5.0` in Inspector" — put these in a `> [!note]` callout as setup context, not as checkboxes

### Good vs Bad Examples

| Bad (verbose/pointless) | Good (gameplay-focused) |
|-------------------------|------------------------|
| "No `NodeConfigurationException` errors" | "Critter spawns and begins wandering immediately" |
| "BB key `Critter_Scurried=true` set" | "After fleeing, critter returns to wandering" |
| "Console shows `CritterEntity: Ready`" | "Both critters are alive and moving on scene load" |
| "Verify `_maxTurnRateDegrees = 180.0`" | "Critter arcs into turns smoothly, full U-turn takes ~1 second" |
| "`OnCollected` event fires" | "Critter visibly grows after eating an ingredient" |
| "No `JmoLogger.Error` messages" | *(don't include — errors are self-evident)* |

### Quantity Rules

- **3-6 items per subsection** (aim for 4)
- **2-4 subsections per section** (aim for 2-3)
- If a section has only 1-2 items, merge it into a related section
- If a section has 8+ items, you're probably too granular — consolidate related checks

### Placement Rules

- Checkboxes go under `##` or `###` headers — **never inside callout blocks**
- Callouts (`> [!info]`, `> [!note]`) are for context, setup instructions, or data values the tester should be aware of — but these are reference info, not checkboxes
- Sub-items (indented bullets without checkboxes) are fine for clarification

### Categories of Checks (in priority order)

1. **Behavioral**: "Critter flees away from the wizard when threatened" — the core gameplay loop
2. **Feel/polish**: "Movement is smooth with gentle arcing turns, not jerky snapping" — subjective quality
3. **Interaction**: "Cast a spell near critter, it immediately bolts in the opposite direction" — cause and effect
4. **Visual**: "Critter grows visibly larger after consuming each ingredient" — observable state change
5. **Edge case**: "When surrounded by threats on both sides, critter enters cornered state" — boundary behavior

## 5. Stale Value Detection

When running in UPDATE mode, scan existing checkbox text for patterns that reference concrete values:

| Pattern | Example Match | How to Verify |
|---------|---------------|---------------|
| `= X.X` | `ForgetTime = 3.0` | Read the referenced `.tres` file |
| `radius X` | `radius 4.0` | Read the `.tscn` scene file |
| `weight X.X` | `_penaltyMaxWeight = 5.0` | Read the `.tscn` or `.tres` |
| `~ N units` | `~8-unit radius` | Read the export value in code or scene |
| `X seconds` | `3.0 seconds` | Read the duration export |
| `count >= N` | `count >= 4319` | Read `Tests/regression_baseline.json` for current committed baseline |

### Update Procedure

1. Extract the file path or resource name from the checkbox text
2. Read the current value from the source file
3. If changed: update the checkbox text with the new value
4. Add a changelog entry noting the stale value correction

### Never Do

- Never uncheck a checkbox that was previously checked
- Never remove a checkbox — only update its text or add new ones
- Never change the section structure of checked sections (reordering breaks mental model)
