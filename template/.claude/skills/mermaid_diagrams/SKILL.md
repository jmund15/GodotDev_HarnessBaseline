---
description: >-
  Auto-load when authoring, editing, or reviewing a mermaid diagram, or when
  working on a command/skill that generates one. Triggers: "mermaid", "diagram",
  "classDef", "graph TD", "graph LR", "stateDiagram", "flowchart", "render a
  diagram", roadmap/memory-graph/dashboard diagram regen. Canonical home for
  renderer constraints, the house palette, and diagram-type selection. SKIP for
  non-mermaid visuals (ASCII art, image files) and pure-prose docs.
---

# Mermaid Diagram Conventions

Canonical house style for every mermaid diagram in the project — Obsidian-vault docs, GitHub-rendered markdown, and command/skill-generated diagrams. Any command that emits mermaid **cites this skill** instead of re-encoding conventions inline; this file is the single source of truth.

## Two authoring modes — different rules

| Mode | What it is | What governs it |
|---|---|---|
| **Generated** | A command renders the diagram deterministically from structured data and never hand-edits it (`/update_roadmap` from the Parts table, `/memory_graph` from the knowledge graph, `/eval_dashboard` from the archive). | Determinism outranks source readability. Short slug IDs (`P1`, `P2a`) are fine — no human maintains the source. The generator owns node/edge derivation; this skill owns the palette, direction, and renderer rules it must emit. |
| **Hand-authored** | An agent writes the diagram inline while producing a doc — architecture system diagram, NPC behavior flow, retrospective decision tree. | The full *House style* checklist below applies: self-explanatory node IDs, subgraph discipline, spacing config. |

## Renderer constraints — Obsidian + GitHub

Hard rules for both renderers:

- **Never emit `click ... href` directives.** Obsidian ignores them entirely. For navigation, place a heading + wikilink *outside* the diagram. The `class NodeID internal-link` node-class trick works in Obsidian only — use sparingly, never in a diagram that also targets GitHub.
- **Color via `classDef`, not theme directives.** Obsidian strips/overrides `%%{init}%%` theming inconsistently. `classDef` is the portable styling channel.
- **In-block frontmatter config** (`layout`, `nodeSpacing`, `rankSpacing`) is honored by both Obsidian and GitHub — safe to emit and to rely on for legibility.
- **Never start a node label with `N. ` / `N) ` / `- ` / `* ` / `# ` / `> `.** Mermaid v10+ runs labels through a CommonMark parser; leading list-markers and ATX-headings render as "Unsupported markdown: list" / "Unsupported markdown: heading" warnings and the label falls back to raw text. Position/numbering is encoded by graph rank (`graph TD` topology), not by prefixing the label. If a Pos must appear in-label, use `N: Name` or `[N] Name` or `Pos N — Name` (em-dash-separated). Generated diagrams (e.g. `/update_roadmap` Mermaid regen) are particularly prone to this when porting a Pos column straight into the label slot.
- Heading anchors elsewhere in the doc use literal heading text, never kebab-slugs — see the `obsidian_conventions` skill.

## Diagram-type selection

| Need | Type | Project examples |
|---|---|---|
| Dependency, structure, data flow | `flowchart` / `graph` | roadmap Parts graph, architecture system diagram |
| State machine, lifecycle, behavior phases | `stateDiagram-v2` | Wizard HSM, NPC behavior flow, dev phases |
| Time-ordered interaction between actors | `sequenceDiagram` | spell-cast pipeline, signal causality, runtime flow |
| Type relationships / composition | `classDiagram` | Component / Blackboard / BehaviorTree structure |
| Data-model entities and relations | `erDiagram` | Trait → Synergy → Spell Instance |
| Genuine time-axis schedule | `gantt` | only when the X-axis is literally time — not for status tables |

Wrong-type smell: reaching for `gantt` to show pass/fail status, or `flowchart` for what is really a state machine.

## House style

- **Direction.** Match the reader's dominant scan. `TD` is the safe default (dependency graphs, top-down architecture). `LR` only for wide relationship webs or left-to-right timelines. One direction per diagram.
- **Node IDs.** Hand-authored: self-explanatory (`WebPortal`, `SpellFactory`) — never single letters (`A`, `B`). Generated: short slugs are fine.
- **Node text vs edge labels.** Node text = states/actions/entities. Edge labels = conditions/transitions (`|valid|`, `|on death|`). Never duplicate the same fact in both. Keep edge labels to 1–3 words.
- **Subgraphs.** Group by logical concern; 5–7 nodes per subgraph max; declare nodes first, then nest. Subgraphs are how you draw a real system without spaghetti.
- **Spacing.** For hand-authored flowcharts, emit the config block:
  ```
  ---
  config:
    flowchart: { nodeSpacing: 50, rankSpacing: 65 }
  ---
  ```
  Cramped short-label nodes are the #1 "unpolished" smell — spacing fixes it faster than color.
- **Layout engine.** Default `dagre`. For >~20 nodes or heavy branching, request `layout: elk` — Obsidian and GitHub both render it; the improvement (fewer edge crossings) shows only on large/branchy graphs, not small ones.
- **Split threshold.** >20–25 nodes → split into multiple diagrams (by happy-path/error, or by system boundary). Two focused diagrams beat one dense one.

## The canonical palette

Every `classDef` sets **both `fill` and `stroke`** (stroke = a darker shade of the fill hue). `stroke-dasharray` is reserved for the *tentative/inactive* semantic — never decorative. Max ~7 classes per diagram. Consumers map their domain vocabulary onto these roles; they do **not** invent fresh hues.

| Role | `fill` | `stroke` | Semantic |
|---|---|---|---|
| `primary` | `#d4c5f9` | `#6a4fb8` | active / in-progress / current |
| `success` | `#c8e6c9` | `#43a047` | done / ready / positive |
| `pending` | `#ffe0b2` | `#fb8c00` | awaiting / blocked |
| `alert` | `#ffcdd2` | `#e53935` | error / gotcha / regret |
| `info` | `#bbdefb` | `#1e88e5` | external / reference |
| `neutral` | `#eceff1` | `#90a4ae` | unclassified / default |
| `muted` | `#e0e0e0` | `#9e9e9e` + `stroke-dasharray: 4 3` | abandoned / deferred / tentative |
| `user` | `#f8bbd0` | `#c2185b` | `user-owned` roadmap Parts (user-domain execution) |

Light fills + dark strokes stay legible in both Obsidian themes. If a fill ever goes dark, set `color:#fff` explicitly so text survives.

**Domain mappings** (same hues, domain-specific class names):
- *Roadmap Parts* — `plan`→primary, `arch`→pending, `idea`→info, `workshop`→neutral, `complete`→success, `rework`→pending + dasharray, `abandoned`→muted, `user`→user.
- *Memory graph* — `gotcha`→alert, `pattern`→success, `preference`→info.

## Anti-patterns

| Rationalization | Reality |
|---|---|
| "I'll just re-state the no-click-links rule inline" | That re-encoding is exactly the drift this skill exists to kill. Cite the skill. |
| "Single-letter node IDs are quicker to type" | Only in generated diagrams. Hand-authored `A[Component]` is unmaintainable — the #1 documented mistake. |
| "`classDef` with `fill` only is fine" | Fill-only nodes float without definition. Stroke is mandatory. |
| "A `gantt` chart will make the status table look richer" | `gantt` encodes *time*. Status is not time. Wrong type = misleading diagram. |
| "Big diagram, but splitting it is effort" | >25 nodes is unreadable regardless of effort saved. Split by boundary. |
| "I'll style each node a different color for variety" | Color is semantic, not decorative. ≤7 classes, each meaning something. |
| "I'll prefix node labels with `1. Foo`, `2. Bar` to mirror the Parts table" | The CommonMark parser sees `1. ` as an ordered-list item and emits "Unsupported markdown: list". Position is in the graph rank already. If a Pos must appear in-label, use `N: Name` / `[N] Name` / `Pos N — Name`. |

## Cross-references

- `obsidian_conventions` skill — Obsidian vault rules; cites this skill for mermaid specifics.
- `_brainstorm_shared/common.md §6.4` — roadmap mermaid schema; emits the roadmap domain mapping above.
- `instruction_quality` skill §3 — the single-source-of-truth principle this skill operationalizes for diagrams.
- `ai-worker prompts/modifier.mermaid.md` — worker-side output-affecting subset, auto-applied to vault `write_doc` calls; keep in sync when either changes.
