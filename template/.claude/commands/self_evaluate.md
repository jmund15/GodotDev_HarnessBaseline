---
description: Reflect on this session's SKILL and memory usage; archive findings for /eval_dashboard.
---

Reflect on the session's SKILL/memory usage; archive findings for `/eval_dashboard`.

## Step 0: Recover Full Session Context

### 0a. Session digest
`/session_end` Phase 0 printed it; standalone, run `python3 .claude/tools/session_digest.py --prompt-tail self_evaluate`. It holds every user prompt verbatim, every friction row and the compaction count, rebuilt from the live transcript — post-compaction memory is not the record, the digest is.

### 0b. Timeline (any session with 1+ compaction, redesigns or go-backs)
From the digest's prompts, in order:
1. What was the original task/plan?
2. Where did corrections or pivots happen, and in which compaction segment?
3. Was the same mistake repeated across segments? Did early corrections hold in later ones?
4. Which friction rows recur (same tool, same error class)?

### 0c. Nuance recall (conditional)
If the digest shows 3+ compactions, or the session had redesigns/go-backs, run [Transcript Nuance Recall](agents/transcript_nuance_recall.md)
after synthesizing the summaries. It surfaces implicit corrections/themes the deterministic
digest's keyword signals can't — feed them into `corrections[]` and `key_takeaway`. This command
is read-only re: long-term memory: do NOT write to auto-memory here — those writes
route through `/autolearn`, which (in `/session_end`) runs first and has already cached the pass.

### Then proceed to evaluation:
1. Examine [CLAUDE.md](/.claude/CLAUDE.md) and [All Skills](/.claude/skills/) for existing instructions.
2. Determine if stronger triggers, language or location would significantly increase the likelihood of acquiring proper information or adhering to instructions for future tasks.
3. Determine if new or stronger hooks would significantly increase the likelihood of acquiring proper information or adhering to instructions for future tasks.
4. Propose the best option(s) for implementation. The BEST options:
    * A: most signficiantly increase odds of compliance and proper information acquisition
    * B: adds the least amount of context to existing Skills and Hooks as possible
5. **Delegation calibration** (orchestrator-tier sessions only). Did dispatched chunks land near expected grain/quality? Was review effort ~10–20%? Was anything kept inline a lower tier could've taken — or dispatched that shouldn't have been? For each **missed delegation** (inline work that met the `orchestration` §11 delegation litmus), also record: (a) the rationalization that kept it inline ("already in context", "faster to just do it", "spec felt like overhead", "scope looked smaller than it was"), and (b) whether a harness edit would have prevented the miss (a sharper litmus line, a new trigger cue, a ladder-row example) — name the file + edit shape, don't just say "improve guidance". Record a material finding: user-flagged → `corrections[]`; self-observed → prefix `key_takeaway`/`notes` with `DELEGATION:` (include the rationalization + proposed-edit fields). Recurring `DELEGATION:` findings are the `/autolearn` signal to revise the ladder (`reference/model_ladder_evidence.md`) or the grain rules (`orchestration` §11).
6. UPSERT one structured entry through `.claude/tools/self_eval_archive_store.py`; the tracked JSON file is a read-only legacy snapshot.

### Step 5: Archive Entry Format

> **Primary-key contract:** one effective entry per `session_id`. The frozen legacy snapshot plus bounded JSONL ledger are read through the store; never model-read or rewrite the whole archive.

#### 5a. Resolve and look up the session

1. Determine the exact `session_id` from the Phase 0 digest. If it is unavailable, stop and record Phase 3 as blocked; new rows require exact session identity.
2. Run `python3 .claude/tools/self_eval_archive_store.py --lookup "<session_id>"`. Exit 0 returns the current row. Exit 1 means no row exists. Exit 2 or `REFUSED` means invalid or unreadable evidence; record Phase 3 as blocked.

#### 5b. Draft and publish

| Lookup result | Action |
|---|---|
| **No row** | Draft a new row from the schema below. Omit `id`; the store assigns it. |
| **Existing row** | Apply the merge rules below. Preserve its `id` and frozen fields. |

Write the complete candidate to `.claude/scratch/self_evaluate-entry-<session_id>.json`, then run:

```bash
python3 .claude/tools/self_eval_archive_store.py --upsert .claude/scratch/self_evaluate-entry-<session_id>.json
python3 .claude/tools/self_eval_archive_store.py --lookup "<session_id>" > ".claude/scratch/self_evaluate-selected-<session_id>.json"
```

The lookup must exit 0. Read the saved selected-row file back and confirm its `session_id` and `id` match the published entry. Use the selected-row file as Phase 3 receipt evidence. A refusal or mismatch leaves Phase 3 incomplete.

#### 5c. Merge semantics (re-run case)

When editing an existing entry, fields update with these rules — do not blindly overwrite:

| Field | Merge rule | Reasoning |
|---|---|---|
| `session_id`, `id`, `date` | **Frozen** (never change) | Primary key + chronological anchor |
| `shape` | **Last-wins** (replace with current values) | Reflects the session's shape at the time of this eval, not the first |
| `title` | **Frozen** unless inaccurate; if updated, preserve the original in `notes` | Stable dashboard label |
| `outcome` | **Escalate-only**: `clean` → `correction` → `failure`. Never downgrade. | A correction discovered on re-run means the session was not clean; demoting hides drift. |
| `pattern` | If new pattern is more severe (A > B > C; E > A), update; else keep | Pattern A trumps Pattern C; failure-cascade E is sticky |
| `domains` | **Set-merge** (union, deduplicated) | New investigation may surface domains the first run missed |
| `corrections[]` | **Set-merge by semantic content**, not exact string. If a re-run surfaces a correction the first run missed, append. If it restates the same correction in different words, keep the original. | Avoid double-counting; preserve specificity |
| `skills_used[]` | **Set-merge** | Same skill loaded across re-runs counts once |
| `memory_searches` | **Last-wins** (replace with current count) | Counter, not log — current value reflects total searches across both runs |
| `memory_hits[]` | **Set-merge** | Each hit counts once even if cited in multiple runs |
| `tests` | `written` **last-wins**, `total_passing` **last-wins**, `tdd_followed` **AND-merge** (false if either run flagged false) | Test counts evolve as session progresses; TDD-violation is sticky |
| `key_takeaway` | Replace with the deepest takeaway. If both runs surface different lessons, concatenate with `// `. | Re-runs typically refine the lesson |
| `notes` | **Append** (newest at end), prefix re-run additions with `[re-eval YYYY-MM-DD]` | Audit trail of how the eval evolved |

#### 5d. Entry schema

```json
{
  "session_id": "<Claude Code session UUID, e.g. 6d9da1fc-...>",
  "id": <omit for a new row; preserve the existing integer on re-run>,
  "title": "Brief phrase-length session title",
  "date": "YYYY-MM-DD",
  "shape": {"compactions": <int>, "duration_min": <int>, "slices": <int|null>, "drive_command": "<command|null>"},
  "outcome": "clean | correction | failure",
  "pattern": "A | B | C | D | E | null",
  "domains": ["pooling", "testing", "combat", "refactoring", "UI", "meta", ...],
  "corrections": [
    "Short description of each user correction (empty array if clean)"
  ],
  "friction": [
    {"what": "<tool + error class, or the user's process complaint>", "workaround": "<what was done instead>", "disposition": "fixed | harness: <file + edit> | worklog | accepted: <why>"}
  ],
  "skills_used": ["architecture_philosophy", "spell_authoring", ...],
  "memory_searches": <count of searches performed>,
  "memory_hits": ["memory entries that provided actionable value"],
  "tests": {
    "written": <number of new tests>,
    "total_passing": <total suite count>,
    "tdd_followed": true
  },
  "key_takeaway": "Single most important lesson from this session",
  "notes": "Optional freeform context for nuance, cross-references to past entries, pattern evolution"
}
```

**Field guidance:**
- `session_id`: REQUIRED. If the exact Claude Code session UUID is unrecoverable, do not publish.
- `shape`: `compactions` and `duration_min` copied from the Phase 0 digest header; `slices` = executed plan slices or `null`; `drive_command` = the drive command that owned the session or `null`.
- `outcome`: "clean" = zero user corrections. "correction" = user caught 1+ issues. "failure" = critical failure (data loss, repeated user frustration, etc.)
- `pattern`: Classify using established patterns from `Self_Evaluate_Themes.patterns` (A/B/C/D/E). **A `correction` or `failure` outcome MUST carry a non-null pattern (A/B/D/E)** — `null` is reserved for `clean` sessions. If a correction fits no existing pattern, add a new letter to `Self_Evaluate_Themes.patterns` rather than leaving it null; a null-on-correction is invisible to `/eval_dashboard`'s pattern distribution. (Clean sessions: `null` and the legacy `C` both read as "clean" — `/eval_dashboard` normalizes them.)
- `domains`: Tag ALL domains the session touched. Common values: `pooling`, `testing`, `combat`, `refactoring`, `UI`, `animation`, `meta`, `brainstorm`, `debugging`, `data-files`, `environment`, `collision`, `HSM`, `spell-effects`, `orchestration`
- `corrections`: Be specific. "Skipped TDD for .tres edit" not "made a mistake"
- `friction`: one row per digest friction class (dedupe identical errors); `accepted` carries its reason. Empty array only when the digest's friction section is empty
- `memory_hits`: Seed from `python3 .claude/tools/memory_hits.py --session <id>` (every auto-memory path this session read or searched), then keep only entries that **actually informed a decision** — not every logged read. Write each as the memory's file stem (`gotcha_x`), without path, `.md` or reason; `/eval_dashboard` counts hits by that name
- `tests.tdd_followed`: `false` if you wrote implementation before a failing test in Logic Domain
- `key_takeaway`: Compare with past entries. If the same takeaway repeats, note it as a recurring theme

**Anti-pattern: direct archive editing.** Draft one small candidate row, then use the store for lookup, validation, ID assignment, locking, rotation and publication.

**DO NOT save self-evaluate data to auto-memory — it pollutes recall.**

**THE ULTIMATE GOAL IS MAXIMUM ADHERENCE TO INSTRUCTIONS, PROPER PROGRESSIVE DISCLOSURE (Get all information necessary, nothing more), WHILE HAVING NO CONTEXT BLOAT IN CLAUDE.md, SKILLS, AND HOOKS**
