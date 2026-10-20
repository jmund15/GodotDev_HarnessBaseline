---
disable-model-invocation: true
---

# `/update_roadmap` Step 1 — locate or create `roadmap.md`

Read at Step 1 of [`update_roadmap.md`](../update_roadmap.md). Not auto-loaded.

## Locating or creating the roadmap

Search the current topic folder (parent of just-saved design doc, OR current working dir for standalone).

- **Found:** load it. Proceed to Step 2.
- **Not found:** create one from the schema in `roadmap_schema.md §6`. Default frontmatter from the calling skill's frontmatter (or user-provided for standalone). Initial Parts populated from the calling brainstorm's outputs. Proceed to Step 2.

## Child sub-roadmap creation

Applies when invoked from the `architecture_brainstorm` skill's Doc-and-review Step 8 procedure *Multi-roadmap case*, invocation 2, for a child-subfolder spawn. The file path is `<parent-folder>/<child-slug>/roadmap.md` (NEW file). Frontmatter populated per roadmap_schema.md §6.1 + sub-roadmap conventions:

| Field | Source |
|---|---|
| `created` | today |
| `topic` | child topic slug (from brainstorm invocation) |
| `scope-level` | inherited from parent-roadmap frontmatter |
| `parent-roadmap` | `../<parent-folder>/roadmap.md` (relative path) |
| `status` | `active` |
| `last_revised` | today |
| `parent-composite-part` *(optional)* | `"<parent-Part name>" (submap-pending on parent)` — human-legible linkage |
| `brainstorm-source` *(optional)* | **Default `./arch.md` / `./ideas.md`** — the submap's own design doc is co-located in THIS child subfolder (doc_conventions.md §5.1). Use `../arch-<slug>.md` ONLY when the submap decomposes a pre-existing parent-folder cluster doc that legitimately stays in the parent (e.g. `arch-level-definition.md`). Prefer co-location; a `../` pointer with a freshly-authored submap doc is the mis-save signal. |

Initial Parts populated from the `architecture_brainstorm` skill's Part-authoring coverage checklist. Cross-roadmap deps preserved with `(parent) <Part>` prefix or `[[../<parent>/roadmap#Part]]` wikilink (per roadmap_schema.md §6.8 lazy-resolution policy).

If parent dependencies make the child roadmap's boundaries hard to scan from its Parts table alone, also author the `## Cross-roadmap dependencies` section (roadmap_triggers_mvp.md §6.12) in this same batch. Omit it when the Parts table makes the boundaries easy to scan.

The single-executor prohibition the entrypoint states is enforced by the `architecture_brainstorm` skill's Doc-and-review roadmap-save guardrail + the symmetric [`idea_brainstorm/SKILL.md`](../../skills/idea_brainstorm/SKILL.md) Step 5 guardrail. The skill's Doc-and-review Step 6 procedure saves the design doc only; this command creates the sub-roadmap.
