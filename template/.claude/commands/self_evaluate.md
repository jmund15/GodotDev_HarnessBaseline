---
description: Assess session skill and memory use
---

Reflect on the session's SKILL/memory usage; archive findings for `/eval_dashboard`.

## Step 0: Recover Full Session Context

### 0a. Session digest
Run [`/autolearn`](autolearn.md) Step 0's shared digest pass, which includes its nuance recall. Inside `/session_end`, read Phase 2's pass instead. Run the pass here when Phase 2 recorded a skip or the run is standalone. This command stays read-only on long-term memory: those writes route through `/autolearn`.

### 0c. Nuance recall
Read the nuance recall output of the 0a pass.

### Then proceed to evaluation:
1. Record each rule that failed to load, to be read, or to be followed as a `rule_misses` row in §5d (`{rule, kind, evidence}`), naming the rule file that existed and the prompt or friction row that shows it.
2. Route each proposal through [`/autolearn`](autolearn.md) §Surface routing; never read every skill here.
3. **Delegation calibration** (orchestrator-tier sessions only). Did dispatched chunks land near expected grain/quality? Was anything kept inline a lower tier could've taken — or dispatched that shouldn't have been? For each **missed delegation** (inline work that met the `orchestration` §11 delegation litmus), also record: (a) the rationalization that kept it inline ("already in context", "faster to just do it", "spec felt like overhead", "scope looked smaller than it was"), and (b) whether a harness edit would have prevented the miss (a sharper litmus line, a new trigger cue, a ladder-row example) — name the file + edit shape, don't just say "improve guidance". Record a material finding: user-flagged → `corrections[]`; self-observed → prefix `key_takeaway`/`notes` with `DELEGATION:` (include the rationalization + proposed-edit fields). Recurring `DELEGATION:` findings are the `/autolearn` signal to revise the ladder (`reference/model_ladder_evidence.md`) or the grain rules (`orchestration` §11).
4. Append one escape row when the defect is in a commit made before this task's work began: `python3 .claude/tools/escape_log.py add --writer self_evaluate --sha <that commit> --should-have-caught <mandate key or none>` (`review_ledger.py keys` lists the keys; `--help` lists the rest).
5. **Spend.** Run `python3 .claude/tools/orchestration_metrics.py --session <session_id> --rollup --json .claude/scratch/self_evaluate-rollup-<session_id>.json`, which is read-only and so safe before `/session_end` Phase 3.6, and answer §5d `spend` from its rows. Name that JSON as a Phase 3 receipt `--input`. Unrated rows read `unrated`, so name waste only from `rerun`, `duplicate-share` and the ratings that exist. When the tool cannot run, write `{"unavailable": "<reason>"}` and record Phase 3 as partial.
6. UPSERT one structured entry through `.claude/tools/self_eval_archive_store.py`.

### Step 5: Archive Entry Format

> **Primary-key contract:** `.claude/tools/self_eval_archive_store.py` enforces one effective entry per `session_id`. Draft one small candidate row and publish it through the store for lookup, validation, ID assignment, locking, rotation and publication — never model-read or rewrite the whole archive. Never save self-evaluate data to auto-memory; it pollutes recall.

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
| `facts` | **Store-filled**, never authored | The store derives it from the transcript at upsert and overwrites whatever the entry carries |
| `shape`, `memory_searches`, `tests` | **Preserved** (no longer asked) | Pre-H6 fields: nothing reads them, and a re-run must carry the existing values through instead of dropping them |
| `title` | **Frozen** unless inaccurate; if updated, preserve the original in `notes` | Stable dashboard label |
| `outcome` | **Escalate-only**: `clean` → `correction` → `failure`. Never downgrade. | A correction discovered on re-run means the session was not clean; demoting hides drift. |
| `pattern` | If new pattern is more severe (A > B > C; E > A), update; else keep | Pattern A trumps Pattern C; failure-cascade E is sticky |
| `domains` | **Set-merge** (union, deduplicated) | New investigation may surface domains the first run missed |
| `corrections[]` | **Set-merge by semantic content**, not exact string. If a re-run surfaces a correction the first run missed, append. If it restates the same correction in different words, keep the original. | Avoid double-counting; preserve specificity |
| `cause_counts` | **Re-asked**: recompute from the merged `corrections`, never union the two runs' objects | The counts must sum to `len(corrections)`, so a re-run derives them afresh |
| `rule_misses[]` | **Set-merge on (`rule`, `kind`)**: keep one row per pair, the new `evidence` winning | A re-run may surface a miss the first run missed; the same rule+kind is not double-counted |
| `skills_used[]` | **Set-merge** | The union is the session's skill list |
| `memory_hits[]` | **Set-merge** | The union is the session's hit list |
| `spend` | **Last-wins**, except a `waste` entry keeps an earlier `cause`/`preventer` for the same `rows` when the new entry leaves it empty. | A re-run reads a rollup covering more of the session; the diagnosis already made survives |
| `key_takeaway` | Replace with the deepest takeaway. If both runs surface different lessons, concatenate with `// `. | Re-runs typically refine the lesson |
| `notes` | **Append** (newest at end), prefix re-run additions with `[re-eval YYYY-MM-DD]` | Audit trail of how the eval evolved |

#### 5d. Entry schema

```json
{
  "session_id": "<Claude Code session UUID, e.g. 6d9da1fc-...>",
  "id": <omit for a new row; preserve the existing integer on re-run>,
  "title": "Brief phrase-length session title",
  "date": "YYYY-MM-DD",
  "outcome": "clean | correction | failure",
  "pattern": "A | B | C | D | E | null",
  "domains": ["pooling", "testing", "combat", "refactoring", "UI", "meta", ...],
  "corrections": [
    "Short description of each user correction (empty array if clean)"
  ],
  "cause_counts": {"<pattern letter>": <count, the counts sum to len(corrections)>},
  "rule_misses": [
    {"rule": "<repo-relative path that exists>", "kind": "not-loaded | not-read | not-followed", "evidence": "<digest evidence id U<n>/F<n>, or a short owner quote>"}
  ],
  "friction": [
    {"what": "<tool + error class, or the user's process complaint>", "workaround": "<what was done instead>", "disposition": "fixed | harness: <file + edit> | worklog: <exact title> | accepted: <guard-working | outside | owner-held | one-off>: <why>"}
  ],
  "skills_used": ["architecture_philosophy", "spell_authoring", ...],
  "memory_hits": ["memory entries that provided actionable value"],
  "spend": {
    "rollup": ".claude/scratch/self_evaluate-rollup-<session_id>.json",
    "total": {"list": <T.list.total>, "wall_min": <int>, "split": {"orchestrator": <min>, "dispatches": <min>, "proofs": <min>, "owner": <min>}, "rows": ["T"]},
    "waste": [{"rows": ["X", "D<n>"], "class": "rerun | discarded | rework | duplicate-share | idle-rewrite | bookkeeping", "list": <number>, "min": <number>, "cause": "<why it happened>", "preventer": "<the rule or tool that would have prevented it, or none known>"}],
    "costliest": {"rows": ["D<n>"], "list": <number>, "bought": "<what that spend produced>"},
    "change": {"text": "<the one change that cuts spend or wall time at equal quality, or none citing the row that shows no slack>", "rows": ["O"]}
  },
  "key_takeaway": "Single most important lesson from this session",
  "notes": "Optional freeform context for nuance, cross-references to past entries, pattern evolution"
}
```

**Field guidance:**
- `session_id`: REQUIRED.
- `facts`: store-filled, never authored. `.claude/tools/self_eval_archive_store.py` derives it from the session's transcript at upsert and overwrites whatever the entry carries: `driver` (the `(model, effort)` pair with the most assistant turns, plus `driver_mixed` when the session spans more than one), `owner_prompts` (the digest's owner rows), `duration_min` (the digest header's whole minutes). A session whose transcript is gone records `{"unavailable": "<reason>"}`; `--refresh-facts <session_id>` re-derives an existing row.
- `outcome`: "clean" = zero user corrections. "correction" = user caught 1+ issues. "failure" = critical failure (data loss, repeated user frustration, etc.)
- `pattern`: Classify using established patterns from `Self_Evaluate_Themes.patterns` (A/B/C/D/E). **A `correction` or `failure` outcome MUST carry a non-null pattern (A/B/D/E)** — `null` is reserved for `clean` sessions. If a correction fits no existing pattern, add a new letter to `Self_Evaluate_Themes.patterns` rather than leaving it null; a null-on-correction appears in `/eval_dashboard`'s `none` bucket instead of a useful cause class. (Clean sessions: `null` and the legacy `C` both read as "clean" — `/eval_dashboard` normalizes them.)
- `domains`: Tag ALL domains the session touched, lower-case: the project's memory domain names (`reference/memory_domains.md`) plus process tags such as `meta`, `brainstorm`, `refactoring`, `environment` and `orchestration`. Reuse a tag the archive already carries before coining a new one; `/eval_dashboard` groups by exact tag.
- `corrections`: Be specific. "Skipped TDD for .tres edit" not "made a mistake"
- `cause_counts`: one count per pattern letter across `corrections`, summing to `len(corrections)` (omit when there are no corrections). A cause `A` (discipline) needs a `not-followed`/`not-read` `rule_misses` row: the store prints a WARN when it is missing. `pattern` stays your own single-letter judgment; the store never derives it.
- `rule_misses`: 0-3 rows, one per rule that existed and was not used. `rule` is a repo-relative path that exists at upsert (`.claude/CLAUDE.md` is valid, with an optional `§<heading>`); the store prints a WARN when the path is not under the repo root. `evidence` is required: a digest evidence id (`U<n>`/`F<n>`) or a short owner quote — a row with blank evidence is refused. Old rows keep no such list.
- `friction`: one row per digest friction class (dedupe identical errors); `accepted: <class>: <why>` names the class that explains why it was accepted — `guard-working` (a guard, gate, policy or precondition did its job), `outside` (an outside service, environment or transient contention), `owner-held`, or `one-off`. A classless `accepted:` is allowed on old rows and warns on new ones. Empty array only when the digest's friction section is empty. A `failure` or pattern `E` row names a resolving fix sha in `notes`, or carries a `worklog: <exact title>` friction disposition matching an open item in `.claude/worklog-titles.md`. `accepted: <class>: <why>` does not excuse such a row. When the cause is outside the harness (client bug, provider outage), the worklog item is a recheck item ("recheck when X is fixed").
- `memory_hits`: Seed from `python3 .claude/tools/memory_hits.py --session <id>` (supported Read, worker-bundle and semantic-search events in the currently retained log; a partial seed, not a session census), then keep only entries that **actually informed a decision** — not every logged read. Write each as the memory's file stem (`gotcha_x`), without path, `.md` or reason; `/eval_dashboard` counts hits by that name
- `spend`: required on new rows (`hooks/self_eval_archive_guard.py` `SPEND_MIN_ID`). `total.list` is the rollup's `T.list.total`, a weight and never dollars spent. Every `rows` value is a non-empty list of rollup row ids: `O`, `P`, `T`, `X` or `D<n>`, optionally with a field (`D29.waste`). `waste` is empty when `X`, `O.idle_rewrites` and `O.bookkeeping` show none.
- `codified_recurrences` (optional): record `{lessonId, claimId, failureKey, evidence, priorLanding, behaviorChanged}` when observed failure evidence matches a codified lesson's failure signature. Set `behaviorChanged` true/false only when the same reproduction distinguishes pre-/post-landing behavior; otherwise use null and name the missing evidence. Fingerprints identify versions, not behavioral equivalence. `analyze_eval_archive.py` displays false as `text-only fix recurred`; supply `repairKind: reach-only` for a legitimate reach-only repair. Preserve these rows through upsert; leave unmatched observations unclassified.
- `key_takeaway`: Compare with past entries. If the same takeaway repeats, note it as a recurring theme

**THE ULTIMATE GOAL IS MAXIMUM ADHERENCE TO INSTRUCTIONS, PROPER PROGRESSIVE DISCLOSURE (Get all information necessary, nothing more), WHILE HAVING NO CONTEXT BLOAT IN CLAUDE.md, SKILLS, AND HOOKS**
