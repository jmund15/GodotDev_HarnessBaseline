---
description: >-
  Auto-load when reading, writing, or editing files in the Obsidian vault
  (DevProjects/{{PROJECT_NAME}} or DevProjects/Jmodot) — design docs, roadmaps,
  worklog, brainstorm docs, framework notes. Triggers: "Obsidian", "vault doc",
  "wikilink", "write to the vault", heading-anchor links, vault file paths.
  SKIP for the /doc_* documentation-folder structure (folder classification,
  the 4-doc system template, domain routing) — that lives in
  agents/documentation_structure.md.
---

# Obsidian Vault Conventions

Universal rules for any interaction with the Obsidian vault — every command,
skill, or ad-hoc edit that touches `DevProjects/{{PROJECT_NAME}}/` or
`DevProjects/Jmodot/`. The `/doc_*` documentation-folder structure (folder
classification, the 4-doc system template, domain routing) is a separate
concern — see [`agents/documentation_structure.md`](../../commands/agents/documentation_structure.md).

## Tooling — native-first
The vault is a normal filesystem path: `{{VAULT_ROOT}}\DevProjects\{{PROJECT_NAME}}\` (and `...\Jmodot\`). Native tools are the default:

| Operation | Tool |
|---|---|
| Read a vault file | `Read` on the path — synthesis-shaped reads route to `read_files` (CLAUDE.md §9) |
| Search the vault | `Grep` (literal) / `semantic-search` (natural language) |
| List vault files | `Glob` |
| Write / overwrite / append | `Write` / `Edit` — confirmed safe on a doc open in the Obsidian app (writes propagate, no conflict prompt) |
| Delete a tracked file | `git rm` |
| Edit frontmatter / tags | `obsidian_manage_frontmatter` / `obsidian_manage_tags` — the one place the MCP earns its cost (structured YAML; native `Edit` is fiddlier) |
| Date-filtered search (`modified_since`) | `obsidian_global_search` — niche |

Obsidian MCP being offline does **not** block native read/write/search/list — there is no "abort if MCP offline" gate for native vault work. Only the frontmatter/tag tools depend on the MCP.

> Residual edge case: a native write to a doc with *unsaved edits open in the app* could race the editor buffer — but `obsidian_update_note` has no real advantage there (both land on disk; the unsaved buffer conflicts either way). In practice the agent is directed, not hand-editing the same file simultaneously.

## `obsidian_search_replace` — literal line-ending matching
`obsidian_search_replace` matches the target file's bytes **literally** — it does NOT normalize CRLF↔LF. Vault files can be inconsistent (LF vs CRLF, depending on which tool created or last saved them), so a multi-line `search` that works on one file may silently return `totalReplacementsMade: 0` on another — no error, reads like a text mismatch when it's actually a separator mismatch.

- **Prefer single-line, newline-free anchors** — they're line-ending-agnostic.
- A whole-line delete must include the line terminator, so it IS line-ending-sensitive. If such a delete (or any multi-line match) returns `0` replacements, suspect the separator first: retry with the other convention (`\n` ↔ `\r\n`). Don't assume the file's convention — a wrong guess 0-hits cleanly, so verify against the actual file.

## Wikilinks & Heading Anchors
All cross-doc references **MUST** be wikilinks — never plain text, bold, or inline code.

- **Inline body references:** `[[../OtherDoc|Display Name]]`
- **Sibling / intra-doc links:** `[[Architecture]]`, `[[arch]]` — Obsidian resolves by note name; add path segments (`[[folder/note]]`) when the bare name is ambiguous vault-wide.
- Use paths relative to the current doc's location.

**Heading-anchor links** (targeting a specific `##`/`###` heading, not just a file):
- Anchor text is the **literal heading text**, verbatim — Obsidian does NOT use GitHub-style kebab-case slugs. `[[doc#Session 1 — Foo]]` resolves; `[[doc#session-1-foo]]` does not.
- Inside a markdown **table cell**, escape the alias pipe as `\|` — an unescaped `|` is parsed as a column separator and breaks the table: `[[doc#Heading\|display]]`.
- Headings containing `/` resolve fine in wikilinks; they break in URL-encoded markdown-style links (`/` → `%2F`, unresolvable). Another reason wikilinks are mandatory.

**Common verbatim pitfalls** — all three fail SILENTLY (anchor falls through to file-top, no error):
- `## Section N — Title` headings: keep BOTH the `Section ` prefix AND the ` — ` em-dash. `[[doc#Section 6 — Migration Plan]]` resolves; `[[doc#6 Migration Plan]]` does not.
- `### N.M — Title` headings: keep the ` — ` em-dash. `[[doc#1.4 — EncounterPersistence × BBScope Mapping]]` resolves; `[[doc#1.4 EncounterPersistence × BBScope Mapping]]` does not.
- **Parts in roadmap.md tables are NOT headings.** `[[other-roadmap#Part Name]]` will never resolve regardless of capitalization. Cross-roadmap Part references use file wikilink + prose: `[[../folder/roadmap\|folder]] § "Part Name"` (see `_brainstorm_shared/common.md` §6.8). Intra-roadmap Part references use `[[#Parts\|Part Name]]` (links to the `## Parts` heading, displays the Part name).
- A single Part / claim that spans 2+ design-doc sections needs 2+ wikilinks joined by ` + ` — fabricating `#A and B` joined anchors never resolves.

## File Moves and Renames
Obsidian auto-link-update ONLY triggers through Obsidian's UI (drag-drop, right-click → Move/Rename). Programmatic moves (Bash `mv`, native `Write`, or MCP ops) do NOT update wikilinks. For reorganization, create folders via Bash but have the user move/rename via Obsidian UI.

## Formatting
- Use `> [!type]- Collapsible Title` for subsections within `##` categories.
- Keep examples concrete — "set ProjectileCount to 3" not "configure the count property".
- No screenshots. Focus on textual descriptions.
- Search Obsidian first — do not guess file paths.
- Do not invent formulas. Read them from the vault. If missing, ask the user.

## Cross-references
- [`agents/documentation_structure.md`](../../commands/agents/documentation_structure.md) — `/doc_*` documentation-folder structure: folder classification, the 4-doc system template, domain routing, Related Systems callouts
- `mermaid_diagrams` skill — mermaid conventions for any diagram emitted into a vault doc
- CLAUDE.md §3 *Obsidian (The Design Source)* — always-loaded summary of this convention
- `ai-worker prompts/modifier.obsidian.md` — worker-side output-affecting subset, auto-applied to vault `write_doc` calls; keep in sync when either changes
