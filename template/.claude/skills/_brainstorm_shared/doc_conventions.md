# Doc path, frontmatter and spawn placement (§5)

> Detail body of the brainstorming shared surface, read at the step named in [`common.md`](common.md).
> Section numbers are stable across the split; a § cited here that is not in this file is mapped to its file by the `common.md` index.

---

## §5. Doc-path & frontmatter conventions (generic — both skills)

**Path tiebreaker:** the project-versus-Jmodot rule is CLAUDE.md §3 *Obsidian (The Design Source)*. The destination table below is local detail.

| Topic shape | Destination |
|---|---|
| {{PROJECT_NAME}}-game-specific (content rules, project mechanics, game design) | `DevProjects/{{PROJECT_NAME}}/Claude/BrainstormingDesigns/YYYY-MM-DD-<topic>/` |
| Jmodot-framework-general (framework design, reusable architecture, cross-game patterns) | `DevProjects/Jmodot/Claude/BrainstormingDesigns/YYYY-MM-DD-<topic>/` |

**Folder-per-topic convention (always-folder default):** Every brainstorm topic is a folder. Folder name: `YYYY-MM-DD-<kebab-case-topic>/` (date = topic inception). Promoting a flat file later costs more than starting with a folder.

**Files inside a topic folder:**

| Filename | Produced by | When |
|---|---|---|
| `ideas.md` | `/idea_brainstorm` | Topic started with ideation phase |
| `arch-<cluster-slug>.md` | `/architecture_brainstorm` | One per cluster from upstream `ideas.md` (per Per-Cluster Routing) |
| `arch.md` | `/architecture_brainstorm` | Direct-arch invocation (no upstream ideation — mature domain or user-direct framing) |
| `arch-<cluster-slug>-followup-N.md` | `/architecture_brainstorm` | Follow-up arch session ripened from an earlier `arch-pending` Part |
| `arch-<hybrid-slug>.md` | `/architecture_brainstorm` | Cross-cluster hybrid from `ideas.md` Cross-Pollination section ripening |
| `decisions.md` | any design session on the topic (§8) | From the first classified fork; durable, never deleted |

**Frontmatter template (per skill — minor variations):**

```yaml
---
created: YYYY-MM-DD
author: Claude [Model]
status: <skill-specific status>
topic: <one-line description>
scope: project-game | jmodot-framework
phase: idea_brainstorm | architecture_brainstorm
derived_from: ../ideas.md               # arch docs only — points to upstream ideas.md or external source
cluster: <cluster-slug>                 # arch-<cluster>.md only — names the upstream cluster
---
```

Status taxonomy per skill:
- `idea_brainstorm`: `ideation-active` → `ideation-complete`
- `architecture_brainstorm`: `active-brainstorming` → `brainstorming-complete`

Doc status is binary by design — `complete` doesn't imply *every* Part is `plan-pending`. Per-Part fork state (`arch-pending` / `idea-pending` mixed with `plan-pending`) lives in `roadmap.md`'s State column (§6.3); duplicating it as a status suffix here would create drift across two surfaces.

**Revising a saved doc:** When feedback invalidates a saved section, rewrite in-place in the main body — don't append a "v1.1" footer that leaves the wrong recommendation at the top. Reasoning trail goes in a Revision History footer at the END; bump `status: <phase>-complete-vN.M`. **Tool choice:** use `Edit` when existing sections can be changed without moving unrelated content; use a full rewrite when the document's structure or most of its body must change. Preserve the approved design as one continuous read. See `feedback_doc_revision_in_place.md`.

**Status / revision-history sync:** Bump `status:` frontmatter and Revision History footer in the SAME edit. Frontmatter is source of truth; footer mirrors.

### §5.1 Spawn-placement convention (when a brainstorm spawns a child)

When this brainstorm spawns a follow-up — sub-brainstorm, arch revisit, sibling concern — three placement outcomes. Choose at Step 1 of the calling skill (existing-doc check identifies the placement question; this rule resolves it).

| Outcome | Placement | Roadmap consequence |
|---|---|---|
| **Sub-component** of parent topic (audience = parent only) | Same folder, sibling file (`arch-<slug>.md`, `arch-<slug>-followup-N.md`) | Same `roadmap.md`; new Part(s) added |
| **Deeper scope** within parent (own internal sequence; audience confined) | Child subfolder `<parent>/<sub-slug>/` holding BOTH the design doc (`arch.md` / `ideas.md`) AND its own `roadmap.md`. **The design doc saves INTO the subfolder — never the parent topic folder**. | Parent's decomposed Part transitions to `submap-pending` (§6.3); cross-references via `Spawned sub-brainstorms` section |
| **Cross-cutting concern** (audience = more than one unrelated parent topic) | Sibling folder at same depth (`YYYY-MM-DD-<sibling-slug>/`) with own `roadmap.md` | Parent roadmap references via cross-folder path |

**Three-criterion test — outcome chosen by WHICH criteria hold (not "all three"):**

Criterion 3 is the **submap-vs-sibling distinguisher**, NOT a gate the submap outcome must pass.

1. **Brainstorm-shaped scope** — the sub-topic warrants its own focused brainstorm session (a distinct set of design forks, multiple clusters, or live competing approaches), not just an extension paragraph in the parent.
2. **Plausibly independent maturity progression** — the sub-topic's Parts CAN mature on a different timeline than the parent. Doesn't need to actually diverge; just must be able to.
3. **Reusable artifact** — the resulting design doc(s) are likely to be referenced by sources beyond the immediate parent brainstorm.

| Outcome | Criterion 1 (brainstorm-shaped) | Criterion 2 (independent maturity) | Criterion 3 (reusable beyond parent) |
|---|---|---|---|
| **Sub-component** (same-folder sibling, no new roadmap) | ✗ (just an extension) OR — | — | — |
| **Deeper scope** (child subfolder + own roadmap → submap-pending) | ✓ | ✓ | **✗** (audience confined to parent — see table row above) |
| **Cross-cutting concern** (sibling folder + own roadmap) | ✓ | ✓ | ✓ |

**Read the table left-to-right:** Criterion 1 fails (or the topic doesn't need its own session) → outcome 1 (same-folder, no new roadmap). Criteria 1+2 pass but 3 fails (parent-confined audience) → outcome 2 (submap). All three pass → outcome 3 (sibling folder).

**Common gotchas:**
- ❌ **Failing the submap outcome against criterion 3.** A submap's defining property is parent-confined audience — criterion 3 is *expected* to fail. Citing "only serves the parent" as a submap rejection is misapplying the sibling-folder gate.
- ❌ **Over-spawning subfolders for sub-component forks** that fail criterion 1 (just an extension paragraph). Those belong as same-folder siblings.
- ❌ **Promoting a submap to sibling-folder because the design doc feels reusable.** Reusability requires concrete *anticipated consumers* outside the parent topic, not just doc generality.

**Important — child roadmap.md ownership:** for *deeper-scope* outcomes, `/update_roadmap` creates the child subfolder's `roadmap.md` on its second invocation; [`update_roadmap.md`](../../commands/update_roadmap.md) owns the single-executor rule.
