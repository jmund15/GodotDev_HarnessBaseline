---
disable-model-invocation: true
---

# `/update_roadmap` Steps 6–9 — revision log, diff, apply, atlas

Read at Steps 6 through 9 of [`update_roadmap.md`](../update_roadmap.md). Not auto-loaded.

## Step 6 — Compose revision log entry

**One line per transition — one statement, ≤25 words. A hard cap, not a soft target.** A second sentence, or a third `;`-joined clause, means you're writing a postmortem — not the *navigational audit trail* this is. Record WHAT transitioned + the single WHY a reader needs to reconstruct current state; everything else lives where it's authoritative — git, commit message, plan/arch doc, Parts table, derived views.

**Keep (load-bearing):** the transition itself (Part + state change, or rename/split/spawn/retire) plus AT MOST ONE qualifier — a commit ref OR a ≤6-word why, never both, never a what-shipped manifest. Cross-roadmap/text-only edits: the Part + the one value that changed.

**Cut (derivable elsewhere — never in the log):** test/gate counts (`Logic X / Integration Y`), file-by-file shipped manifests, plan divergences/deviations, `session_audit` findings, auto-memory pins, "Mermaid reclassed `:::complete`" (mechanical — always happens), unblock-cascade narration ("now Currently-ready" — the derived views already show readiness), multi-sentence design rationale (belongs in the arch doc).

**Format:** `- YYYY-MM-DD — <Part>: <old-state> → <new-state> (<commit-ref OR ≤6-word why>).`

**Idiomatic forms:**
- `- 2026-05-13 — Initial roadmap from arch.md. 7 Parts sequenced (Pos 1–7), 12 un-sequenced.`
- `- 2026-05-15 — <Part>: <old-state> → <new-state> (<reason>).`
- `- 2026-05-15 — <Part>: plan-pending → complete (<commit-ref>).`
- `- 2026-05-15 — Spawned `<child-doc>` from Part <name> (<same-folder | child-subfolder | sibling-folder> per §5.1).`
- `- 2026-05-15 — Renamed Part `<old>` → `<new>` (N Deps + M MVP wikilinks rewritten).`
- `- 2026-05-15 — Split Part `<old>` into `<new-1>`, `<new-2>`. Deps redistributed.`

Append to the `## Revision Log` section.

| Rationalization | Reality |
|---|---|
| "Skip the revision log this time — nothing important changed" | Revision log is the audit trail. Every State transition deserves a line; the reader uses it to reconstruct WHY the roadmap is in its current state. |
| "Combine multiple Part transitions into one revision log line" | One transition = one line. Aggregating loses the timestamped audit trail. Exception: a single brainstorm session legitimately produces N transitions; those can share a date but each gets its own line. |
| "This completion was a big effort — the log should capture all of it (tests shipped, files touched, audit findings, deviations)" | The log is a navigational trail, not a postmortem. Ship-detail is derivable from git, the commit message, and the plan/arch doc — duplicating it here is the exact bloat that makes logs eat half the doc. |

## Step 7 — Present batch diff

Show the user the proposed diff covering:
1. Frontmatter changes (only `last_revised` typically)
2. Parts table updates (additions, transitions, splits, renames)
3. Mermaid block regeneration
4. Currently-Ready / Blocked view refresh
5. Spawned sub-brainstorms section updates (if new spawn this session)
6. MVP recompute (if a `## MVP Checkpoints` section exists): changed check-marks + Status transitions. **Surface a playtest prompt** for any MVP that flipped to `🧪 Ready for playtest` this run — name it, and name any remaining `user-owned` Required Part the user must finish first (e.g. *"MVP-2 ready for playtest, except user-owned Part: Room scene art"*). Surface any `✅ Verified`→downgrade regression flag.
7. New Revision Log line(s)
8. Validator warnings from Step 3 (if any)
9. Atlas regeneration (Step 9) — always listed, so the single approval covers it

The user can redirect ("revise Mermaid", "split this transition into two log lines", "skip the spawn entry — it's the same folder") — adjust diff, re-present.

## Step 8 — Apply

If the batch diff (Step 7) is empty — proposed edits reduce to no changes — report *"No roadmap changes needed"* and skip to Step 9 without prompting for approval.

Otherwise: `Edit` (or `Write` if creating) `roadmap.md` with the approved diff. Bump `last_revised: YYYY-MM-DD` in frontmatter to today.

## Step 9 — Regenerate the cross-roadmap index

When the project generates a cross-roadmap index (`_brainstorm_shared/roadmap_triggers_mvp.md` §6.13), run its generator in render mode; the project's own roadmap-index command names it. No index → skip this step.

Runs on **every invocation that reaches Step 8, the empty-diff path included** — the index can be stale
from roadmap edits made outside this command, so the no-op case is exactly the one a "only when Step 8
applied" gate would wrongly skip.

**Non-blocking.** A generator failure warns with its named error and never rolls back the roadmap edit;
the index is a derived vault document that its next run rebuilds.
Generated docs live outside the git checkout and are never committed. Report any Health finding the
generator surfaces for the roadmap just edited.
