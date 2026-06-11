---
disable-model-invocation: true
---

Generate a visual, readable graph of the file-based auto-memory store (`.claude/auto-memory/`) and its `[[wikilink]]` relations, exported to Obsidian.

## Source

Scan `.claude/auto-memory/**/*.md` (skip `MEMORY.md` itself and any `.search-index/`). For each topic file read the frontmatter (`name`, `description`, `metadata.type`, `metadata.tier`) and extract `[[wikilink]]` references from the body — these are the graph edges. **Hot tier** = files referenced by a `MEMORY.md` pointer; **cold tier** = files under `archive/` (migrated MCP buckets carry `metadata.tier: cold`).

## Instructions

1. **Gather** the memory files and their wikilink edges. A deterministic pass: `Grep -o "\[\[[^]]+\]\]"` per file for edges, plus a frontmatter read for `name`/`type`/`tier`. An unresolved `[[name]]` (no matching file) is a valid edge to a not-yet-written memory — render it as a leaf node, don't drop it.

2. **Update the Obsidian document** at `DevProjects/{{PROJECT_NAME}}/Claude/Meta/Memory Knowledge Graph.md` with:

   ### Frontmatter
   ```yaml
   ---
   updated: <current date>
   memories: <count>
   links: <wikilink edge count>
   tags: [memory, knowledge-graph, claude]
   ---
   ```

   ### Graph Section
   - Mermaid `graph LR` diagram. Renderer constraints, palette, and styling discipline: see the `mermaid_diagrams` skill.
   - Cluster with subgraphs by `metadata.type` (user / feedback / project / reference), or by hot vs cold tier.
   - Sanitize node IDs (replace spaces/special chars with underscores); use display labels `NodeID["name"]`.
   - One edge per `[[wikilink]]`. Style nodes by type using the canonical palette:
     ```
     classDef feedback fill:#bbdefb,stroke:#1e88e5
     classDef gotcha   fill:#ffcdd2,stroke:#e53935
     classDef project  fill:#c8e6c9,stroke:#43a047
     ```

   ### Memories Section
   For EACH memory, a heading (for anchor linking) + collapsible callout:
   ```markdown
   #### memory-slug
   > [!info]- Details
   > **Type:** <type> · **Tier:** hot|cold #tag-for-type
   > **Links:** [[other-slug-1]], [[other-slug-2]]
   >
   > <description>
   ```

   The heading creates the anchor (`#memory-slug`) the Mermaid nodes click to; the `[[wikilinks]]` reference other memory headings in the same document.

   ### Relations Table
   | From | → | To |
   |------|---|-----|
   | [[#slug1]] | links | [[#slug2]] |

3. **Write the file** using `mcp__obsidian__obsidian_update_note` (`targetType: filePath`, `wholeFile`/`overwrite`, `createIfNeeded: true`). Native `Write` to the vault path is also acceptable — Obsidian MCP is optional for this.

4. **Confirm** the update with memory/link counts.
