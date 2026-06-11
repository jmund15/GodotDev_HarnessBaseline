---
disable-model-invocation: true
---

Reconcile `Documentation/Start Here.md` against the actual folder structure.

## When This Runs
- Called by `/doc_full` as Phase 4 (after all sub-agents complete and Quick Reference is written)
- Can be called standalone at any time via `/doc_start_here_update`

## Phase 1 — Gather State

### 1a. Vault tooling
Documentation reads/writes use native `Read`/`Write`/`Edit`/`Grep`/`Glob` on the vault path — see the `obsidian_conventions` skill. No MCP-connectivity gate.

### 1b. List all system folders
Use `obsidian_list_notes` on `DevProjects/{{PROJECT_NAME}}/Claude/Documentation/` with recursion depth 2.
Build the **authoritative system list** by classifying each top-level folder:

**Folder classification** — apply the Folder Classification rules from `agents/documentation_structure.md` to categorize each subfolder as archived, structural, domain, or system. Then:
- **System folder** — contains doc-template `.md` files (Quick Reference, Architecture, Designer Usage, etc.) directly. System path = `{SystemName}`.
- **Domain folder** — contains subfolders that are themselves system folders (and optionally a `_Hub.md`). Each subfolder's system path = `{DomainFolder}/{SystemName}`.

Both flat systems and domain-nested systems are included in the unified system list. Each entry tracks its **system path** (used for doc reads and wikilinks) and **display name** (the leaf folder name, used in table rows).

### 1c. Read Start Here
Use `obsidian_read_note` to read `DevProjects/{{PROJECT_NAME}}/Claude/Documentation/Start Here.md`.

### 1d. Compute the diff
Compare folder names against all Obsidian `[[wikilinks]]` in Start Here to identify:

- **New systems** — folders not referenced anywhere in Start Here
- **Stale entries** — links in Start Here where the target folder no longer exists
- **Entry point drift** — systems already in Start Here that link to a `Design Document`
  but now have a `Quick Reference.md` available (check by listing the folder contents)

**If the diff is empty** (nothing new, nothing stale, no drift), report "Start Here is already
up to date." and **exit without rewriting the file**.

---

## Phase 2 — Classify New Systems

For each new system identified in Phase 1d:

### 2a. Read the best available doc
Use `obsidian_read_note` to read (in priority order), using the system path from Phase 1b:
1. `{SystemPath}/Quick Reference.md`
2. `{SystemPath}/Architecture.md`
3. `{SystemPath}/Designer Usage.md` or `{SystemPath}/Usage.md`
4. `{SystemPath}/Design Document.md`

Where `{SystemPath}` is either `{SystemName}` (flat) or `{DomainFolder}/{SystemName}` (nested).

### 2b. Classify domain
Use the System Overview text and Related Systems callout to classify into one of these domains. **PROJECT-CONFIG:** the first row is your game's central content pipeline — replace it (and add rows) for your content taxonomy; the rest are stack-generic.

| Domain | Signals |
|--------|---------|
| **\<Core Content Pipeline>** | *(project-specific — your game's central content lifecycle, e.g. spell/projectile/item spawn → collision → reaction → propagation)* |
| **Environment** | world objects, destructibles, item/resource drops, contact effects, environment |
| **Visual Effects** | rendering, particles, animation, trails, sprites, VFX, mesh, fragment, cloud |
| **Combat & Stats** | damage, knockback, status effects, stat management, hitbox, hurtbox, combat |
| **AI** | decision-making, steering, formation, behavior, navigation, pathfinding |
| **Infrastructure** | pooling, shared utilities, cross-cutting framework, Jmodot core, submodule |

### 2c. Ask if ambiguous
If a system could plausibly fit two domains, ask the user:
> "I'm placing `{SystemName}` in **{DomainA}** because [one-sentence reason]. Does that
> sound right, or should it go in **{DomainB}**?"

Do NOT silently pick and proceed when genuinely ambiguous.

### 2d. Determine available docs
For each new system, note which of these files exist in the folder:
- `Quick Reference.md` — entry point for By Domain table
- `Architecture.md` — warrants a Developer row in By Role
- `Designer Usage.md` or `Usage.md` — warrants a Designer row in By Role
- Any `.md` with "Design" in the name — fallback entry point if no Quick Reference

---

## Phase 3 — Update Start Here

Apply all changes in memory, then write the full updated file in a single
`obsidian_update_note` call (wholeFileMode: "overwrite"). Never make partial writes.

### Change rules

**Adding a new system to By Domain:**
- Place in the correct domain section table
- For domain-nested systems, the domain folder name determines the domain — no classification needed
- Entry point: `[[{SystemPath}/Quick Reference|{DisplayName}]]` if QR exists, else `[[{SystemPath}/{DocFileName}|Design Document]]`
- Description: derive from the System Overview callout (1 sentence)
- Display name: the leaf folder name (derive from the Quick Reference `# Title — Quick Reference` heading if available, else use folder name)

**Adding a Designer row (By Role — "I'm a Designer"):**
- Only if `Designer Usage.md` or `Usage.md` exists
- Write a task-oriented row: `| {Action phrase: "Configure X" or "Set up Y"} | [[{SystemPath}/Designer Usage|{DisplayName}]] |`

**Adding a Developer row (By Role — "I'm a Developer"):**
- Only if `Architecture.md` exists
- Write a topic row: `| {Topic: "{DisplayName} internals" or "How {DisplayName} works"} | [[{SystemPath}/Architecture|{DisplayName}]] |`

**Path notation:** `{SystemPath}` is the full vault-relative path from Documentation/ (either `{SystemName}` for flat or `{DomainFolder}/{SystemName}` for nested). `{DisplayName}` is always the leaf system name. Wikilinks always include a display alias to keep table rows readable regardless of nesting depth.

**Updating entry point drift:**
- Replace the `Design Document` link with `Quick Reference` in the By Domain table row
- Leave By Role tables unchanged (they link to specific doc types)

**Removing a stale entry:**
- Remove the row from every table it appears in
- Preserve all other rows exactly

**Preserve exactly:**
- The Welcome callout (static — never modify)
- The Reading Order section (static — never modify)
- All existing rows that have no changes (preserve wikilinks and display aliases exactly)
- The Archived Documentation table (append-only — never remove rows)
- All section headers and ordering

---

## Constraints
- Write the complete updated file in one `obsidian_update_note` call — never patch line by line
- If diff is empty, do NOT rewrite the file (avoids spurious modification timestamps)
- Never invent descriptions — derive them from the Quick Reference System Overview
- Never change the section order in Start Here
- Archived table is append-only — stale system removal does NOT touch archived entries
- Keep this lightweight — no sub-agents needed. Total tool calls: 3-10.
- Supports both flat (`Documentation/{SystemName}/`) and domain-nested (`Documentation/{DomainFolder}/{SystemName}/`) folder structures. Domain-nested systems inherit their domain from the parent folder name.
