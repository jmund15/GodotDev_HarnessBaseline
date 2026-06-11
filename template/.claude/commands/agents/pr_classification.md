---
disable-model-invocation: true
---

# PR Classification

<!-- Single source of truth for domain classification, type classification, and label application. -->
<!-- Referenced by: /review_pr, /create_pr, /merge_pr -->

## Domain Classification

Classify based on **what the PR enables in the game**, not what file types were changed.

**Key question:** Does this PR add or modify player-facing behavior — something a player would *see*, *feel*, or *interact with differently*? If yes → Gameplay or Mixed.

| Domain | Signals | User Testing? |
|--------|---------|---------------|
| **Logic** | `Source/SpellArchitecture/`, `Source/Synergies/`, `Source/Inventory/`, `Tests/Logic/` | No — automated tests sufficient |
| **Gameplay** | Scenes (`.tscn`), `Source/Wizard/`, `Source/VFX/`, `Source/UI/`, `Tests/Integration/`, `Tests/Sanity/` | **Yes** — subjective feel |
| **Data** | `.tres` or `.tscn` files only | No — automated tests sufficient |
| **Meta** | `.claude/`, `skills/`, `commands/`, `.gitignore`, `CLAUDE.md` | No — not runtime code |
| **Jmodot** | Submodule pointer change | Depends on what changed |
| **Mixed** | Logic + Gameplay in same PR | **Yes** — if any Gameplay changes |

> **Common mistake:** A PR with well-tested `.cs` files (e.g., new collision response, new spell behavior) is NOT "Logic" just because it has unit tests. If the feature introduces new **game behavior**, it is **Gameplay** regardless of test coverage. Automated tests validate *correctness*; user testing validates *feel*.

## Type Classification

Derive from conventional commit prefix (in PR title or majority of commits):

| Prefix | Label |
|--------|-------|
| `feat` | `feature` |
| `fix` | `fix` |
| `refactor` | `refactor` |
| `chore` | `chore` |
| `test` | `test` |

## Label Colors

| Label | Color |
|-------|-------|
| `meta` | `#808080` |
| `logic` | `#0075ca` |
| `gameplay` | `#a2eeef` |
| `data` | `#d4c5f9` |
| `feature` | `#0e8a16` |
| `fix` | `#d73a4a` |
| `refactor` | `#fbca04` |
| `chore` | `#ededed` |
| `test` | `#bfd4f2` |

## Applying Labels

```bash
# Create labels if they don't exist (silent fail if already exists)
gh label create "<label>" --color "<hex>" --description "<desc>" 2>/dev/null || true
# Apply labels
gh pr edit <N> --add-label "<label1>,<label2>"
```

**Labels are additive** — never remove existing labels, only add based on classification.
