---
description: >-
  Auto-load when reading, writing, or editing files in the Obsidian vault
  (DevProjects/{{PROJECT_NAME}} or DevProjects/Jmodot) — design docs, roadmaps, worklog,
  brainstorm docs, framework notes, wikilinks, heading-anchor links, or any vault file
  path. SKIP for the /doc_* documentation-folder structure (folder classification, the
  4-doc system template, domain routing) — that lives in agents/documentation_structure.md.
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
| Date-filtered search | `obsidian_global_search` (`query` required; `searchInPath`, `modified_since`/`modified_until`, `caseSensitive`, `useRegex` — verified against the live schema 2026-08-07), or native `Glob` + file mtimes |

MCP tool names verified against the live server 2026-08-06: `obsidian_read_note` / `obsidian_update_note` / `obsidian_search_replace` / `obsidian_global_search` / `obsidian_list_notes` / `obsidian_delete_note` / `obsidian_manage_frontmatter` / `obsidian_manage_tags`. The 2026-07-era names (`obsidian_get_note` / `obsidian_write_note` / `obsidian_patch_note` / `obsidian_replace_in_note` / `obsidian_search_notes`) are dead — the mapping has now inverted across TWO server swaps, so treat tool-name liveness as swap-prone: check the session's actual tool listing before scripting MCP calls, and prefer native tools (which never swap) for anything they cover. Obsidian MCP being offline does **not** block native read/write/search/list — there is no "abort if MCP offline" gate for native vault work. Only the frontmatter/tag tools depend on the MCP.

> Residual edge case: a native write to a doc with *unsaved edits open in the app* could race the editor buffer — but `obsidian_update_note` has no real advantage there (both land on disk; the unsaved buffer conflicts either way). In practice the agent is directed, not hand-editing the same file simultaneously.

## Vault taxonomy — live vs legacy (as of 2026-07-04)

- **Live design surface: `<vault>/{{PROJECT_NAME}}/Claude/`** (Documentation/, BrainstormingDesigns/, Planning/, TODO/, Design/, Meta/, Archived/, …) and `<vault>/Jmodot/Claude/`. All agent reads and writes land here.
- **Legacy (human-era — root position ≠ canon):** vault-root `Spell Architecture/`, `Planning/`, `Documentation/`, `Spell Details/`, `Brainstorming/`, `TODO/` predate the `Claude/` convention and are unmaintained. `Spell Architecture/`'s formula docs (`Spell Formulas.md`, `Synergy Rules.md`, `Trait Definitions.md`) are **0 bytes** — the CLAUDE.md "do not invent formulas; read from vault" rule therefore resolves to its ask-the-user branch; there is no populated formula doc to read.
- The current design bible is the repo skill `game_vision`, not a vault doc — vault searches for "vision" find only the deprecated PvP-era doc under `Claude/Archived/`.
- **Design-session state is a vault artifact, not scratch.** Each `BrainstormingDesigns/<topic>/` folder carries a `decisions.md` alongside its `ideas.md` / `arch*.md` / `roadmap.md` — the durable decision frontier (schema + append rules: `_brainstorm_shared/common.md` §8). It is written during the session, never deleted at doc-save, and never mirrored into `.claude/scratch/`.
- **`Claude/Research/`** — `/research` artifacts, transient by design. Frontmatter carries `expires-with:` (engine/library version); stale the moment that version moves — re-run or delete, never edit in place. Durable findings promote to cold auto-memory or the design doc first.

## `obsidian_search_replace` — literal line-ending matching
`obsidian_search_replace` (default literal mode) matches the target file's bytes **literally** — it does NOT normalize CRLF↔LF. Vault files can be inconsistent (LF vs CRLF, depending on which tool created or last saved them), so a multi-line `search` that works on one file may silently report 0 replacements on another — no error, reads like a text mismatch when it's actually a separator mismatch. *(Gotcha observed under the tool's 2026-07-era name `obsidian_replace_in_note`; literal-byte behavior carries across the rename.)*

- **Prefer single-line, newline-free anchors** — they're line-ending-agnostic.
- A whole-line delete must include the line terminator, so it IS line-ending-sensitive. If such a delete (or any multi-line match) returns `0` replacements, suspect the separator first: retry with the other convention (`\n` ↔ `\r\n`), or pass `flexibleWhitespace: true` (any whitespace run in `search` matches any whitespace in the body — sidesteps the separator question; literal mode only). Don't assume the file's convention — a wrong guess 0-hits cleanly, so verify against the actual file.

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
- `ai-worker prompts/modifier.obsidian.md` — worker-side output-affecting subset, auto-applied to vault `write_doc` calls. Lives with the ai-worker server (separate host, not in this repo — provenance/availability: `environment_bootstrap` skill); sync when either changes *and* the server is reachable
