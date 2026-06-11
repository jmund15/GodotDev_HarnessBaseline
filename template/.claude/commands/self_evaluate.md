---
disable-model-invocation: false
---

Reflect on the session's SKILL/memory usage; archive findings for `/eval_dashboard`.

## Step 0: Recover Full Session Context

### 0a. Assess session complexity
Read `logs/pre_compact.json` and filter entries matching the current session ID. Count compactions.

**Deep read triggers** (if ANY are true, do Step 0b):
- 3+ compactions for this session ID
- Session involved large system design or architecture refactor
- Session had redesigns, go-backs, or direction changes
- User explicitly mentions the session was complex or long
- You only have post-compaction context and aren't sure what happened earlier

### 0b. Deep read: transcript summaries
For each compaction entry matching this session, find the corresponding `.summary.json`:
- Replace `.jsonl` extension with `.summary.json` in the `backup_path`
- Path: `logs/transcript_backups/transcript_<session_id_prefix>_<trigger>_<timestamp>.summary.json`

Read ALL `.summary.json` files for this session. Extract from each:
- `metadata.total_messages` / `metadata.total_tool_calls` — session scale
- `user_messages[]` where `signals` contains `"correction"` — **these are user corrections that may be invisible in post-compaction context**
- `user_messages[]` where `content_preview` contains direction changes ("simplify", "go back", "different approach", "revert", "stop")
- `user_messages[]` where `signals` contains `"instruction"` — requirements that may have been given pre-compaction

### 0c. Synthesize across summaries
For complex sessions, build a chronological timeline:
1. What was the original task/plan?
2. Where did corrections or pivots happen? (Which compaction segment?)
3. Was the same mistake repeated across segments?
4. Did corrections from early segments get applied in later segments?

**Critical:** Post-compaction context is lossy. The more compactions, the more likely that corrections and context were lost. The summaries are your ground truth for the FULL session.

### 0d. Nuance recall (conditional)
If any *Deep read trigger* (0a) fired, run [Transcript Nuance Recall](agents/transcript_nuance_recall.md)
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
5. UPSERT a **structured entry** in the self-evaluate archive at `/.claude/self_evaluate_archive.json` — see Step 5 for the strict one-entry-per-session contract.

### Step 5: Archive Entry Format

> **Primary-key contract (load-bearing):** the archive holds **exactly ONE entry per Claude Code session**. The composite primary key is `session_id` (preferred, when available) or `(title, date)` (fallback for legacy entries that pre-date the `session_id` field). If `/self_evaluate` runs more than once on the same session, you MUST edit the existing entry in-place — never append a duplicate. The 31% duplication rate observed in the 2026-02 → 2026-05 archive (per `/eval_dashboard` audit on 2026-05-03) is what this rule prevents.

#### 5a. Resolve session identity FIRST

Before drafting the entry:

1. Determine the current **session_id** — read it from the active `logs/pre_compact.json` entries (same source as Step 0a). If unavailable, fall back to the JSONL transcript filename prefix (`<session_id>_*.jsonl`). If neither is recoverable, set `session_id: null` and use `(title, date)` as the key.
2. Read `self_evaluate_archive.json`. Search `structured_entries[]` for an existing record where `session_id` matches (or where `(title, date)` matches if `session_id` is null). At most one match is expected.

#### 5b. Branch on existence

| Lookup result | Action |
|---|---|
| **No match** (first eval for this session) | APPEND a new entry per the schema below. Assign `id` = max existing id + 1. |
| **One match** (re-run on same session) | EDIT the existing entry in-place per the merge semantics below. Do NOT append a new record, do NOT change its `id`. |
| **Two+ matches** | This is a pre-existing duplication artifact. Edit the **earliest** (lowest `id`) and mark the others for cleanup in `notes` (`"DUPE_PENDING_CLEANUP: see id=N"`). Do not auto-delete; flag it for the next `consolidate-memory`-style sweep. |

#### 5c. Merge semantics (re-run case)

When editing an existing entry, fields update with these rules — do not blindly overwrite:

| Field | Merge rule | Reasoning |
|---|---|---|
| `session_id`, `id`, `date` | **Frozen** (never change) | Primary key + chronological anchor |
| `title` | **Frozen** unless wildly inaccurate; if updated, preserve the original in `notes` | Stable for `(title, date)` fallback lookup |
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
  "id": <sequential, assigned at creation, NEVER changed on re-run>,
  "title": "Brief phrase-length session title",
  "date": "YYYY-MM-DD",
  "outcome": "clean | correction | failure",
  "pattern": "A | B | C | D | E | null",
  "domains": ["pooling", "testing", "combat", "refactoring", "UI", "meta", ...],
  "corrections": [
    "Short description of each user correction (empty array if clean)"
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
- `session_id`: REQUIRED for new entries. NULL is acceptable only when the Claude Code session UUID is genuinely unrecoverable (rare).
- `outcome`: "clean" = zero user corrections. "correction" = user caught 1+ issues. "failure" = critical failure (data loss, repeated user frustration, etc.)
- `pattern`: Classify using established patterns from `Self_Evaluate_Themes.patterns` (A/B/C/D/E). **A `correction` or `failure` outcome MUST carry a non-null pattern (A/B/D/E)** — `null` is reserved for `clean` sessions. If a correction fits no existing pattern, add a new letter to `Self_Evaluate_Themes.patterns` rather than leaving it null; a null-on-correction is invisible to `/eval_dashboard`'s pattern distribution. (Clean sessions: `null` and the legacy `C` both read as "clean" — `/eval_dashboard` normalizes them.)
- `domains`: Tag ALL domains the session touched. Common values: `pooling`, `testing`, `combat`, `refactoring`, `UI`, `animation`, `meta`, `brainstorm`, `debugging`, `data-files`, `environment`, `collision`, `HSM`, `spell-effects`
- `corrections`: Be specific. "Skipped TDD for .tres edit" not "made a mistake"
- `memory_hits`: Only list memory entries that **actually informed a decision** — not every search result
- `tests.tdd_followed`: `false` if you wrote implementation before a failing test in Logic Domain
- `key_takeaway`: Compare with past entries. If the same takeaway repeats, note it as a recurring theme

**Anti-pattern: append-without-lookup.** If you reach Step 5 and immediately draft a new entry without first reading the archive and searching for the current session_id, STOP. That's exactly the failure mode that produced the 38 duplicate entries flagged on 2026-05-03. Read the archive, search for the key, then branch on 5b.

**DO NOT save self-evaluate data to auto-memory — it pollutes recall.**

**THE ULTIMATE GOAL IS MAXIMUM ADHERENCE TO INSTRUCTIONS, PROPER PROGRESSIVE DISCLOSURE (Get all information necessary, nothing more), WHILE HAVING NO CONTEXT BLOAT IN CLAUDE.md, SKILLS, AND HOOKS**
