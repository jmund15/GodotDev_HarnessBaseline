---
description: Create or update a roadmap and Part states; /mvp_plan authors MVP narratives
---

# /update_roadmap

Single executor for `roadmap.md` edits. Source of truth for the roadmap schema: [`_brainstorm_shared/common.md`](../skills/_brainstorm_shared/common.md) §6.

**Mode: batch-propose.** All proposed edits assembled into one diff; single user approval applies everything (Parts table + Mermaid + derived views + Spawned sub-brainstorms + MVP check-mark/Status recompute + Revision Log). Iterative per-edit confirmation is NOT this command's job, and batch mode is single approval, never zero approval: the vault is not a git repository, so the diff is the owner's only checkpoint against silent drift, and a second approval would re-ask the same diff.

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

1. **Locate `roadmap.md`** — read [`reference/update_roadmap_locate.md`](reference/update_roadmap_locate.md) §Locating or creating the roadmap, and §Child sub-roadmap creation when the `architecture_brainstorm` skill's Doc-and-review Step 8 spawns a child subfolder.
2. **Resolve proposed edits from inputs** — read [`reference/update_roadmap_resolve.md`](reference/update_roadmap_resolve.md) for the idea-brainstorm routing table, the arch-brainstorm state mapping, the spawn transition to `submap-pending`, and the split/re-authoring paths. End by writing the resolved edits into a candidate: an edited copy of each target `roadmap.md` under `.claude/scratch/update_roadmap/`. Bump `last_revised` to today only in a candidate whose resolved edits change the roadmap, so a repeated invocation that resolves to no change keeps the Step 8 empty-diff path.
3. **Run the generator, then validate the edited Parts table** — when the project's roadmap generator (§6.13) has a single-roadmap mode, run it over every candidate together, so cross-roadmap resolution sees the whole batch; the project's roadmap-index command names the invocation. It returns each rendered document and the Structural and Wikilink warnings. Then read [`reference/update_roadmap_validate.md`](reference/update_roadmap_validate.md) for the checks it does not run and the fix-recipes. Without a generator, the candidate is the working document. Warnings surface as `⚠` markers in the Step 7 diff; the user decides apply-anyway or revise.
4. **Review the Mermaid diagram** — the Step 3 generator run rendered it; read [`reference/update_roadmap_render.md`](reference/update_roadmap_render.md) §Step 4 — Render Mermaid diagram for what to review, and how to render it by hand without a generator.
5. **Review derived views and the MVP recompute** — the Step 3 generator run computed them; read [`reference/update_roadmap_render.md`](reference/update_roadmap_render.md) §Step 5 — Compute derived views and §Step 5 — MVP Checkpoints recompute for what to review, and how to compute them by hand without a generator.
6. **Compose the revision log entry** — read [`reference/update_roadmap_close.md`](reference/update_roadmap_close.md) §Step 6 — Compose revision log entry for the owning format.
7. **Present the batch diff** — read [`reference/update_roadmap_close.md`](reference/update_roadmap_close.md) §Step 7 — Present batch diff for the nine items the diff must cover.
8. **Apply** — read [`reference/update_roadmap_close.md`](reference/update_roadmap_close.md) §Step 8 — Apply, including the empty-diff no-op path
9. **Regenerate the cross-roadmap index** — read [`reference/update_roadmap_close.md`](reference/update_roadmap_close.md) §Step 9 — Regenerate the cross-roadmap index.

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

All standalone ops route through Steps 3-9 (validate, regen, present diff, apply, regenerate the cross-roadmap index).

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

## Cross-references

- [`_brainstorm_shared/common.md`](../skills/_brainstorm_shared/common.md) — roadmap.md schema (source of truth). §6 is the schema; §6.4 + §6.5 specify the heading anchors the Step 3 generator run (Step 4 / Step 5 by hand without one) replaces; §6.8 governs cross-folder dep resolution.
- [`_brainstorm_shared/roadmap_triggers_mvp.md`](../skills/_brainstorm_shared/roadmap_triggers_mvp.md) §6.10 is canonical for the Step 3 Trigger validators.
- [`_brainstorm_shared/common.md`](../skills/_brainstorm_shared/common.md) §5.1 is the spawn-placement convention informing Step 7's Spawned sub-brainstorms updates.
- [`_brainstorm_shared/existing_doc_check.md`](../skills/_brainstorm_shared/existing_doc_check.md) §1.2 is stale-roadmap remediation.
- [`architecture_brainstorm/SKILL.md`](../skills/architecture_brainstorm/SKILL.md) — invokes this command as its final step
- [`idea_brainstorm/SKILL.md`](../skills/idea_brainstorm/SKILL.md) — invokes this command as its final step
- `obsidian_conventions` skill — Obsidian wikilink/anchor conventions for `Source`-column links
