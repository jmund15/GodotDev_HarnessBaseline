---
disable-model-invocation: true
---

# PR Classification

Single source for domain, type, and label classification. Used by PR review, creation, merge, and test
checklists.

## Domain classification

Classify what the PR enables, not its file extensions. Read the project's `Domain Split` and subsystem
registry before classifying paths.

| Domain | Signal | User testing? |
|---|---|---|
| **Logic** | behavior assigned to the project's Logic domain | No; automated tests suffice |
| **Gameplay** | player-visible or player-felt behavior assigned to the Gameplay domain | Yes for subjective feel |
| **Data** | authored data only, with no new runtime behavior | No; validate the data |
| **Meta** | harness, docs, build, or repository-only changes | No |
| **Framework** | reusable-framework change or submodule pointer | Depends on the changed behavior |
| **Mixed** | more than one runtime domain | Yes if any subjective Gameplay behavior changed |

A tested source file is not Logic by default. Collision response, interaction behavior, animation, UI,
and other player-facing changes remain Gameplay when the project domain split says so.

If a changed path has no subsystem owner, classify its behavior and report registry drift. Do not add a
new hardcoded path table here.

## Type classification

Use the PR title, or the majority conventional-commit prefix when the title lacks one.

| Prefix | Label |
|---|---|
| `feat` | `feature` |
| `fix` | `fix` |
| `refactor` | `refactor` |
| `chore` | `chore` |
| `test` | `test` |

## Label colors

| Label | Color |
|---|---|
| `meta` | `#808080` |
| `logic` | `#0075ca` |
| `gameplay` | `#a2eeef` |
| `data` | `#d4c5f9` |
| `framework` | `#5319e7` |
| `feature` | `#0e8a16` |
| `fix` | `#d73a4a` |
| `refactor` | `#fbca04` |
| `chore` | `#ededed` |
| `test` | `#bfd4f2` |

## Applying labels

```bash
gh label create "<label>" --color "<hex>" --description "<desc>" 2>/dev/null || true
gh pr edit <N> --add-label "<label1>,<label2>"
```

Labels are additive. Never remove an existing label during classification.
