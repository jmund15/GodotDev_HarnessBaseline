---
description: Update topic-folder roadmap.md — Parts table, mermaid regen, derived views, revision log. Brainstorm skills invoke; standalone for Part transitions.
---

# /update_roadmap

Single executor for `roadmap.md` edits. Source of truth for the roadmap schema: [`_brainstorm_shared/common.md`](../skills/_brainstorm_shared/common.md) §6.

**Mode: batch-propose.** All proposed edits assembled into one diff; single user approval applies everything (Parts table + Mermaid + derived views + Spawned sub-brainstorms + MVP check-mark/Status recompute + Revision Log). Iterative per-edit confirmation is NOT this command's job, and batch mode is single approval, never zero approval — the diff is the user's checkpoint against silent drift.

**Gate inheritance:** when invoked from a flow whose own human gate just approved the exact Parts list (e.g. `/design_drive` design-lock batch answers), present the diff informationally and apply WITHOUT a second approval prompt — the upstream gate was the approval; the user redirects after the fact. Standalone/user-driven invocations keep the approval prompt.

**Single executor, no alternative path.** Never create a `roadmap.md` at any depth via `write_doc` or a direct `Write` outside this command.

**Invocation contexts:**
- **From `/architecture_brainstorm`** (final step): just-saved arch design doc + the Parts the design produced.
- **From `/idea_brainstorm`** (final step): just-saved idea-bank doc + Per-Cluster Routing → Part state mapping.
- **Standalone (user-driven):** ad-hoc Part transitions (`mark complete <part>`, `transition <part> to <state>`, `rename <old> <new>`, `split <part> into <new-1>, <new-2>`, `retire <part>`).

---

## Arguments

- **From a brainstorm skill (no explicit args):** the calling skill supplies context (saved doc path + Parts list). Locate the topic-folder `roadmap.md` from the saved doc's parent directory.
- **Standalone (verb-form args):** `/update_roadmap <verb> <part-name> [args]` — see the *Standalone operations* table below. Target roadmap = current working directory's `roadmap.md` (or nearest ancestor folder containing one).
- **No argument and no calling-skill context:** print usage (this Arguments section + the Standalone operations table) and exit without edits.
- **Malformed `<part-name>`** (doesn't resolve in the target roadmap): list candidate Parts and exit without edits. Don't auto-propose creating a new Part — Part creation routes through the brainstorm-skill invocation, not standalone.

---

## Procedure

Read each step's reference file when you reach that step; none of them auto-load.

1. **Locate `roadmap.md`** — read [`reference/update_roadmap_locate.md`](reference/update_roadmap_locate.md) §Locating or creating the roadmap, and §Child sub-roadmap creation when `architecture_brainstorm` Step 8 spawns a child subfolder.
2. **Resolve proposed edits from inputs** — read [`reference/update_roadmap_resolve.md`](reference/update_roadmap_resolve.md) for the idea-brainstorm routing table, the arch-brainstorm state mapping, the spawn transition to `submap-pending`, and the split/re-authoring paths.
3. **Validate the edited Parts table** — read [`reference/update_roadmap_validate.md`](reference/update_roadmap_validate.md) for the structural, Trigger-content, batch-shape, wikilink-coherence and parent-staleness checks plus their fix-recipes. Warnings surface as `⚠` markers in the Step 7 diff; the user decides apply-anyway or revise.
4. **Render the Mermaid diagram** — read [`reference/update_roadmap_render.md`](reference/update_roadmap_render.md) §Step 4 — Render Mermaid diagram. Regenerate every run; drift is silent.
5. **Compute derived views** — read [`reference/update_roadmap_render.md`](reference/update_roadmap_render.md) §Step 5 — Compute derived views and §Step 5 — MVP Checkpoints recompute. The MVP recompute mutates only check-marks and the non-terminal Status line; `/mvp_plan`'s narrative fields survive byte-for-byte.
6. **Compose the revision log entry** — read [`reference/update_roadmap_close.md`](reference/update_roadmap_close.md) §Step 6 — Compose revision log entry. One transition, one line, ≤25 words.
7. **Present the batch diff** — read [`reference/update_roadmap_close.md`](reference/update_roadmap_close.md) §Step 7 — Present batch diff for the nine items the diff must cover.
8. **Apply** — read [`reference/update_roadmap_close.md`](reference/update_roadmap_close.md) §Step 8 — Apply, including the empty-diff no-op path.
9. **Regenerate the atlas** — read [`reference/update_roadmap_close.md`](reference/update_roadmap_close.md) §Step 9 — Regenerate the atlas. Runs on every invocation that reaches Step 8, empty diff included; non-blocking.

---

## Standalone operations (user-driven)

| Command | Effect |
|---|---|
| `/update_roadmap mark complete <part>` | State → `complete`; revision log entry. |
| `/update_roadmap retire <part>` | State → `abandoned` with user-supplied reason in revision log. |
| `/update_roadmap rename <old> <new>` | Rewrite Part cell + every Deps cell + every `## MVP Checkpoints` Required-Parts wikilink referencing `<old>`. Revision log records count (Deps + MVP wikilinks separately). |
| `/update_roadmap split <part> into <new-1>, <new-2>` | Replace Part with N new Parts. Prompt for Pos values + Deps redistribution. **If any `## MVP Checkpoints` Required-Parts line references the split Part, prompt per-MVP for which child(ren) inherit the reference** and rewrite in the same batch — 1→N is ambiguous so it can't auto-rewrite the way `rename` does, but it must not silently orphan the MVP ref (the Step 3 coherence validator is only the reactive backstop). |
| `/update_roadmap transition <part> to <state>` | Direct State change. Prompt for reason → revision log. |
| `/update_roadmap promote <part> to <pos>` | Move from un-sequenced to sequenced at given Pos. |
| `/update_roadmap demote <part>` | Move from sequenced to un-sequenced (Pos → `—`). |

All standalone ops route through Steps 3-9 (validate, regen, present diff, apply, regenerate atlas).

### Standalone error handling

| Error condition | Behavior |
|---|---|
| Verb not recognized (e.g., `/update_roadmap whatevs Foo`) | List the valid verbs from the operations table above; exit without edits. |
| `<part-name>` doesn't resolve in the target roadmap | List candidate Parts; exit without edits. Don't auto-propose creating a new Part — Part creation routes through brainstorm-skill invocation. |
| `transition <part> to <state>` with invalid `<state>` | List the 11 valid States from [common.md](../skills/_brainstorm_shared/common.md) §6.3; exit without edits. |
| `rename <old> <new>` where `<new>` collides with an existing Part name | Refuse — silent rename would corrupt Deps references on the collision target. Exit with the conflict named. User resolves by picking a non-colliding name or `retire`-ing the existing Part first. |
| `split <part> into <new-1>` (only one child supplied) | Refuse — split produces 2+ children by definition. (Use `rename` for 1→1.) Exit. |
| `split <part> into ...` where any new name collides with an existing Part | Refuse — same name-collision rationale as `rename`. Exit with the conflict named. |
| Target roadmap doesn't exist (no `roadmap.md` in CWD or any ancestor) | Per Step 1, propose creating one from the `common.md` §6 schema (user confirmation required) before applying the standalone op. |

---

## MCP-offline policy

`roadmap.md` is a vault file edited with native `Read`/`Edit`/`Write`. See [`_brainstorm_shared/common.md`](../skills/_brainstorm_shared/common.md) §3.

---

## Cross-references

- [`_brainstorm_shared/common.md`](../skills/_brainstorm_shared/common.md) — roadmap.md schema (source of truth). §6 is the schema; §6.10 is canonical for the Step 3 Trigger validators; §6.4 + §6.5 specify the heading anchors Step 4 / Step 5 replace; §6.8 governs cross-folder dep resolution; §5.1 is the spawn-placement convention informing Step 7's Spawned sub-brainstorms updates; §1.2 is stale-roadmap remediation (the caller re-runs arch Step 5 before handing the Part list here).
- [`architecture_brainstorm/SKILL.md`](../skills/architecture_brainstorm/SKILL.md) — invokes this command as its final step
- [`idea_brainstorm/SKILL.md`](../skills/idea_brainstorm/SKILL.md) — invokes this command as its final step
- [`/roadmap_atlas`](roadmap_atlas.md) — the generated cross-roadmap dashboard Step 9 regenerates (`common.md` §6.13)
- `obsidian_conventions` skill — Obsidian wikilink/anchor conventions for `Source`-column links
