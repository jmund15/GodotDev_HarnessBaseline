---
description: >-
  Auto-load when reading, writing, or editing files in the Obsidian vault
  (DevProjects/{{PROJECT_NAME}} and the sibling folders its vault taxonomy lists) — design docs, roadmaps, worklog,
  brainstorm docs, framework notes, wikilinks, heading-anchor links, or any vault file
  path. SKIP for the /doc_* documentation-folder structure (folder classification, the
  4-doc system template, domain routing) — that lives in agents/documentation_structure.md.
---

# Obsidian Vault Conventions

Universal rules for any interaction with the Obsidian vault — every command,
skill, or ad-hoc edit that touches `DevProjects/{{PROJECT_NAME}}/` or a sibling folder
`reference/vault_taxonomy.md` lists. The `/doc_*` documentation-folder structure (folder
classification, the 4-doc system template, domain routing) is a separate
concern — see [`agents/documentation_structure.md`](../../commands/agents/documentation_structure.md).

## Tooling — native-first
The vault is a normal filesystem path: `{{VAULT_ROOT}}\DevProjects\{{PROJECT_NAME}}\`. Vault files are edited with native `Read`/`Edit`/`Write`; the Obsidian MCP is retired.

| Operation | Tool |
|---|---|
| Read a vault file | `Read` on the path — synthesis-shaped reads route to `read_files` (CLAUDE.md §Tool Routing) |
| Search the vault | `Grep` (literal) / `semantic-search` (natural language) |
| List vault files | `Glob` |
| Write / overwrite / append | `Write` / `Edit` — confirmed safe on a doc open in the Obsidian app (writes propagate, no conflict prompt) |
| Delete a tracked file | `git rm` |
| Edit frontmatter / tags | `Edit` on the YAML block |
| Date-filtered search | `Grep` with `path` set to the vault directory, or `Glob` + file mtimes |

> Residual edge case: a native write to a doc with *unsaved edits open in the app* could race the editor buffer — both land on disk, so the unsaved buffer conflicts regardless of writer. In practice the agent is directed, not hand-editing the same file simultaneously.

## Vault taxonomy — live vs legacy

- **Live design surface: `<vault>/{{PROJECT_NAME}}/Claude/`.** All agent reads and writes land here. `reference/vault_taxonomy.md` maps its folders, the sibling project folders and any legacy areas.
- **Design-session state is a vault artifact, not scratch.** Each `BrainstormingDesigns/<topic>/` folder carries a `decisions.md` alongside its `ideas.md` / `arch*.md` / `roadmap.md` — the durable decision frontier (the brainstorm procedure that writes it owns the schema and append rules). It is written during the session, never deleted at doc-save, and never mirrored into `.claude/scratch/`.
- **`Claude/Research/`** — research-command artifacts, transient by design. Frontmatter carries `expires-with:` (engine/library version); stale the moment that version moves — re-run or delete, never edit in place. Durable findings promote to cold auto-memory or the design doc first.

## `Edit` — literal line-ending matching
`Edit` matches the target file's bytes **literally** — it does NOT normalize CRLF↔LF. Vault files can be inconsistent (LF vs CRLF, depending on which tool created or last saved them), so a multi-line `old_string` that works on one file may fail to match on another — no partial match, reads like a text mismatch when it's actually a separator mismatch.

- **Prefer single-line, newline-free anchors** — they're line-ending-agnostic.
- A whole-line delete must include the line terminator, so it IS line-ending-sensitive. If such an edit (or any multi-line match) fails to match, suspect the separator first: retry with the other convention (`\n` ↔ `\r\n`). Don't assume the file's convention — a wrong guess fails cleanly, so verify against the actual file.

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
- `### N.M — Title` headings: keep the ` — ` em-dash. `[[doc#1.4 — LevelPersistence × StateScope Mapping]]` resolves; `[[doc#1.4 LevelPersistence × StateScope Mapping]]` does not.
- **Parts in roadmap.md tables are NOT headings.** `[[other-roadmap#Part Name]]` will never resolve regardless of capitalization. Cross-roadmap Part references use file wikilink + prose: `[[../folder/roadmap\|folder]] § "Part Name"`. Intra-roadmap Part references use `[[#Parts\|Part Name]]` (links to the `## Parts` heading, displays the Part name).
- A single Part / claim that spans 2+ design-doc sections needs 2+ wikilinks joined by ` + ` — fabricating `#A and B` joined anchors never resolves.

## File Moves and Renames
Obsidian auto-link-update ONLY triggers through Obsidian's UI (drag-drop, right-click → Move/Rename). Programmatic moves (Bash `mv`, native `Write`) do NOT update wikilinks. For reorganization, create folders via Bash but have the user move/rename via Obsidian UI.

## One document, one mode
Two questions pick it: does the content inform **action** (doing) or **understanding** (thinking), and does it serve **learning** or **work**?

| | Learning | Work |
|---|---|---|
| **Action** | tutorial | how-to |
| **Understanding** | explanation | reference |

Don't mix modes: no reference tables inside a tutorial, no tutorial hand-holding inside reference, no arguing inside a how-to. Split and link instead. Every `write_doc` spec states the mode.

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
- `ai-worker prompts/modifier.obsidian.md` — worker-side output-affecting subset, auto-applied to vault `write_doc` calls. Lives with the ai-worker server (separate host, not in this repo — provenance/availability: `environment_bootstrap` skill); sync when either changes *and* the server is reachable
