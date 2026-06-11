---
disable-model-invocation: true
---

# Documentation Structure — `/doc_*` Framework

<!-- Folder-structure rules for the /doc_* documentation-command family. -->
<!-- Referenced by: doc_before_writing, doc_full, doc_audit_fix, doc_architecture_audit, doc_start_here_update -->

> **Universal Obsidian vault conventions** — native-first tooling, vault
> read/write constraints, wikilink & heading-anchor mechanics, formatting —
> live in the `obsidian_conventions` skill (auto-loads on any vault work).
> This doc covers ONLY the `Documentation/` folder structure the `/doc_*`
> commands operate on.

## Documentation Root
- **Root:** `DevProjects/{{PROJECT_NAME}}/Claude/Documentation/`
- **Filesystem path (when Bash needed):** `{{VAULT_ROOT}}\DevProjects\{{PROJECT_NAME}}\Claude\Documentation\`

## Folder Classification

When doc commands scan the Documentation folder, classify each item by content:

1. **Archived** — name contains `(Archived)` → skip (preserved in Archived table)
2. **Structural** — `Claude/`, `Prototypes/`, `Start Here.md` → skip (not systems or domains)
3. **Entity-doc folder** — contains per-entity `.md` files (not system-template docs) and may contain subfolders of the same kind. Example: `NPC/` with individual NPC docs and a `BuildingBlocks/` subfolder. These follow `/doc_npc` conventions, not the 4-doc system template. Skip template compliance checks (S1-S4).
4. **Domain folder** — contains subfolders that are themselves system folders (may also contain `_Hub.md`)
5. **System folder** — contains doc-template files directly: `Quick Reference.md`, `Architecture.md`, `Designer Usage.md`, `Retrospective.md`, or any `.md` with "Design Document" in the filename
6. **Everything else** — ignored

Classification is content-based. Folders transition between types automatically as contents change (e.g., adding system subfolders to an empty folder makes it a domain folder). No per-command exclusion lists.

## Folder Naming Convention
Applies to `Documentation/` system and domain folders. (Brainstorm topic folders use a different `YYYY-MM-DD-<kebab-topic>/` convention — see `_brainstorm_shared/common.md §5`.)

- **PascalCase, no spaces:** `SpellReaction/` not `Spell Reaction/`
- **Descriptive compound noun** — the name should pass the scan test: "Can someone unfamiliar with the codebase guess what this folder documents?"
- **No uniform suffix** — don't append "System" to everything. Add a qualifier word only when the bare name is ambiguous (e.g., `EntityPhysics` not `Physics`, but `Explosion` is fine alone)
- **No domain prefix** if inside a domain parent: `Steering/` inside `AI/`, not `AISteering/`
- **No implementation details** in names: `WaveSpell/` not `WaveSpellOverhaul/`, `CritterAI/` not `HSM-BT Critter AI/`
- Normalize user input before creating: `"Affinity System"` → `Affinity`

## Domain Folder Routing
New system docs go inside their domain parent folder (read Start Here "By Domain" to determine domain):
- **Domain exists:** `Documentation/{Domain}/{SystemName}/`
- **No domain match:** `Documentation/{SystemName}/` (top-level, flag for user review)

## Related Systems Callouts
Cross-references between system docs are wikilinks (mechanics — literal-text anchors, table-cell pipe escaping — are in the `obsidian_conventions` skill). The doc-template-specific patterns:

- **Related Systems entries:** `> - [[../OtherSystem/Quick Reference|Display Name]] — relationship`
- **Intra-system links:** `[[Architecture]]`, `[[Designer Usage]]`, `[[Retrospective]]`
- Use paths relative to the current doc's location.

## Bidirectional Links
When writing a Related Systems callout that references system B, check if B's QR links back. If not, add a return link. All cross-references must be bidirectional.

## Mermaid Diagrams
Mermaid conventions — renderer constraints (no click links), the canonical palette, diagram-type selection, generated-vs-hand-authored rules — live in the `mermaid_diagrams` skill. Any doc command that emits a mermaid block cites that skill; do not re-encode mermaid rules here.
