---
disable-model-invocation: true
---

# `/update_roadmap` Step 1 — locate or create `roadmap.md`

Read at Step 1 of [`update_roadmap.md`](../update_roadmap.md). Not auto-loaded.

## Locating or creating the roadmap

Search the current topic folder (parent of just-saved design doc, OR current working dir for standalone).

- **Found:** load it. Proceed to Step 2.
- **Not found:** propose creating one from the schema in `common.md §6`. Default frontmatter from the calling skill's frontmatter (or user-provided for standalone). Initial Parts populated from the calling brainstorm's outputs. Proceed to Step 2 after confirmation.

## Child sub-roadmap creation

Applies when invoked from `architecture_brainstorm` Step 8 *Multi-roadmap case* invocation 2 for a child-subfolder spawn. The file path is `<parent-folder>/<child-slug>/roadmap.md` (NEW file). Frontmatter populated per common.md §6.1 + sub-roadmap conventions:

| Field | Source |
|---|---|
| `created` | today |
| `topic` | child topic slug (from brainstorm invocation) |
| `scope-level` | inherited from parent-roadmap frontmatter |
| `parent-roadmap` | `../<parent-folder>/roadmap.md` (relative path) |
| `status` | `active` |
| `last_revised` | today |
| `parent-composite-part` *(optional)* | `"<parent-Part name>" (submap-pending on parent)` — human-legible linkage |
| `brainstorm-source` *(optional)* | **Default `./arch.md` / `./ideas.md`** — the submap's own design doc is co-located in THIS child subfolder (the deeper-scope fresh-brainstorm case per common.md §5.1; the brainstorm Step 6/5 saves it here). Use `../arch-<slug>.md` ONLY when the submap decomposes a pre-existing parent-folder cluster doc that legitimately stays in the parent (e.g. `arch-level-definition.md`). Prefer co-location; a `../` pointer with a freshly-authored submap doc is the mis-save signal. |

Initial Parts populated from architecture_brainstorm Step 5 output. Cross-roadmap deps preserved with `(parent) <Part>` prefix or `[[../<parent>/roadmap#Part]]` wikilink (per common.md §6.8 lazy-resolution policy).

If 2+ Parts have cross-roadmap deps, ALSO prompt the user to author the `## Cross-roadmap dependencies` section (common.md §6.12) in this same batch — it's optional schema but high-value for sub-roadmap readers. Default: include the section; user can decline.

The single-executor prohibition the entrypoint states is enforced by [`architecture_brainstorm/SKILL.md`](../../skills/architecture_brainstorm/SKILL.md) Step 6 *Roadmap.md is NOT saved here* guardrail + the symmetric [`idea_brainstorm/SKILL.md`](../../skills/idea_brainstorm/SKILL.md) Step 5 guardrail. Step 6 of `architecture_brainstorm` saves the design doc only; this command creates the sub-roadmap.
