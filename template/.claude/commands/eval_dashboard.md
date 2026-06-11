Generate a Self-Evaluation Performance Dashboard from the session archive.

## Fast Path (preferred — use the analysis script)

A canonical analysis script lives at `.claude/tools/analyze_eval_archive.py`. It performs the deterministic stats computation: dedupe by `(title, date)` (or `session_id` once that field is populated per `/self_evaluate` v2), classify legacy entries heuristically, compute domain & skill clean rates, run the recent-vs-prior 10-session trend window, count memory-hit citations, and emit both a console report AND a structured JSON at `/tmp/eval_out/stats.json`.

**Run order on every `/eval_dashboard` invocation:**

1. `python .claude/tools/analyze_eval_archive.py 2>&1 | tail -150` — read the console output for inline review.
2. Treat `/tmp/eval_out/stats.json` as the **single source of truth** for §1 / §3 / §4 / §4b / §6 / §8.6 numbers.
3. Use Claude reasoning ONLY for §7 (Strength/Weakness narrative) and §8 (Conclusions) — these need session-context judgment that the script can't produce.

**Why this matters:** the 2026-05-03 dashboard generation took ~10 minutes of direct archive reading + manual counting. The script reduces that to ~5 seconds with arithmetic that can't drift between runs. Direct archive reading remains the audit-shape exception (per CLAUDE.md §10) for spot-checking suspect numbers — but should not be the default path for stats generation.

**If the script is missing or broken** (deleted, syntax error, archive schema change): fall back to the manual procedure below. After the fallback run, verify the script is restored and runnable before calling the dashboard complete.

**Pre-step: archive dedup audit.** The script prints `Duplicate-rate: X%` near the top of its output. If duplicate rate is non-zero AND `/self_evaluate` v2 (one-entry-per-session) has been in effect for ≥5 new entries, run a `consolidate-memory`-style cleanup pass on `self_evaluate_archive.json` BEFORE generating the dashboard — the duplication is now a data-integrity bug, not a historical artifact.

## Data Source
Read `/.claude/self_evaluate_archive.json` — contains two data formats:

### Structured entries (preferred — new format)
- `structured_entries[]` — JSON objects with explicit fields: `id`, `date`, `outcome`, `pattern`, `domains[]`, `corrections[]`, `skills_used[]`, `memory_searches`, `memory_hits[]`, `tests{}`, `key_takeaway`, `notes`
- Parse these deterministically — no heuristics needed

### Legacy entries (historical — pre-2026-02-03)
- `legacy_entries[]` — compressed prose strings from sessions #1-#38
- Parse heuristically: look for "Clean", "USER CORRECTION", "CRITICAL", dates in parentheses, pattern references
- `Self_Evaluate_Themes.patterns` — pattern A/B/C/D definitions
- `Self_Evaluate_Themes.meta_insights[]` — high-level system observations

### Merging both formats
When generating stats, combine both sources. Structured entries take priority for accuracy. For legacy entries, use best-effort classification and note any ambiguity in footnotes.

## Output
Write to Obsidian at `DevProjects/{{PROJECT_NAME}}/Claude/Meta/Self Evaluation Dashboard.md` using:
- `targetType: "filePath"`
- `targetIdentifier: "DevProjects/{{PROJECT_NAME}}/Claude/Meta/Self Evaluation Dashboard.md"`
- `modificationType: "wholeFile"`, `wholeFileMode: "overwrite"`, `createIfNeeded: true`, `overwriteIfExists: true`

## Document Structure

### Frontmatter
```yaml
---
updated: <current date>
total_sessions: <count>
clean_sessions: <count>
correction_sessions: <count>
current_streak: <count>
tags: [self-evaluate, dashboard, claude, meta]
---
```

### Section 1: Overview Stats
Quick-reference table:
| Metric | Value |
|--------|-------|
| Total Sessions | N |
| Clean (0 corrections) | N (X%) |
| Minor Corrections | N (X%) |
| Major Failures | N (X%) |
| Current Clean Streak | N |
| Longest Clean Streak | N |

### Section 2: Timeline
Mermaid `gantt` or `xychart-beta` chart showing sessions over time.
- X-axis: dates (group by week if >20 entries)
- Color/category: Clean / Correction / Failure
- Goal: visual trend of improvement over time

**Mermaid rules:** renderer constraints (no click links, etc.) live in the `mermaid_diagrams` skill. Diagram-specific: keep labels short (session # only); prefer `gantt` for broad Obsidian compatibility.

### Section 3: Pattern Distribution
Classify each session into the established patterns, then summarize:

> [!abstract]- Pattern Classification
> | Pattern | Description | Count | Sessions |
> |---------|-------------|-------|----------|
> | A | Discipline failure (clear docs exist) | N | #3, #5, ... |
> | B | Documentation gap (autolearn fixes) | N | #1, #7, ... |
> | C | Clean session | N | #8-#19, ... |
> | D | Tooling/Documentation mismatch | N | #21, ... |

### Section 4: Domain Performance
Analyze which domains generated the most corrections vs. clean execution:

> [!tip]- Domain Hit Rates
> | Domain | Sessions Touching | Corrections | Clean Rate |
> |--------|------------------|-------------|------------|
> | Pooling | N | N | X% |
> | Testing | N | N | X% |
> | Refactoring | N | N | X% |
> | Combat/Effects | N | N | X% |
> | UI/Animation | N | N | X% |
> | Meta/Tooling | N | N | X% |

### Section 4b: Skill Performance

The per-skill mirror of Section 4. For each skill that appeared in `skills_used[]` 3+ times across the structured archive, compute the clean rate. Structured-entries-only — legacy entries don't reliably surface `skills_used[]` and would inflate denominators with unattributed sessions.

> [!tip]- Skill Hit Rates
> | Skill | Sessions Loaded | Clean | Correction | Failure | Clean Rate | Trend |
> |-------|-----------------|-------|-----------|---------|-----------|-------|
> | architecture_philosophy | N | N | N | N | X% | ↑/↓/→ |
> | spell_authoring | N | N | N | N | X% | ↑/↓/→ |
> | testing | N | N | N | N | X% | ↑/↓/→ |
> | jmodot | N | N | N | N | X% | ↑/↓/→ |
> | autolearn | N | N | N | N | X% | ↑/↓/→ |
> | (skills with <3 loads omitted as low-signal) | | | | | | |

**Trend column:** compare the most recent ≤10 sessions' clean rate for that skill against the prior window of the same size. ↑ if recent > prior + 10pp, ↓ if recent < prior − 10pp, → otherwise. With small N this is noisy — flag the trend column with `(low N)` when fewer than 6 recent sessions touched the skill.

**Predicted-vs-measured (optional, gated on data):** if a skill's frontmatter declares `expected_clean_rate: 0.NN`, render a `Predicted` column and flag (⚠) when measured falls outside ±10pp of predicted. Skip the column entirely if no skill declares the field — don't render an empty column. The field is opt-in; new skills get a calibration period; mature skills can declare a target after 1–2 dashboard runs reveal a stable baseline.

### Section 4c: Routing Stability

**Data source:** `logs/routing_audit_stats.json` (produced by `/routing_audit aggregate`, also auto-run by `/session_end` Phase 3.5). If the file doesn't exist, render the section with a "(no audit data — `/routing_audit` hasn't been run yet)" placeholder rather than omitting — visible absence is a signal that the audit hook may not be wired.

This section answers the question Sections 4 and 4b can't: *are the routing hooks reaching the agent?* Section 4b measures whether skills carry their declared compliance, but routing decisions happen below the skill layer (per-call PostToolUse classification against §9 rules). Drift here surfaces *before* it manifests as Pattern A discipline failures in Section 4b.

> [!tip]- Routing Audit — Headline Counts
> | Metric | Value | Reading |
> |--------|-------|---------|
> | silent_misses | N | rule-warranted, nudge channel didn't reach agent — the gap to close |
> | nudged_routing_misses | N | rule-warranted, nudge fired (channel works; agent may still ignore) |
> | cue_exempt_overrides | N | legitimate K1/L6/audit-shape override (compliance, not failure) |

> [!tip]- Top-5 Silent-Miss Rules
> Read from `top_5_silent_miss_rules` array in stats JSON. Each row: rule, total in window, current week count, prior-4wk avg, trend label.
>
> | Rule | Total | This week | Prior 4-wk avg | Trend |
> |------|-------|-----------|----------------|-------|
> | (populate from stats JSON) | N | N | N.N | ±N% / new / flat |

**Trend reading:**
- `+N%` (current vs. prior-4wk) — rule is firing more often than usual; investigate whether new code-discovery patterns are tripping it.
- `-N%` — improvement; if persistent, candidate for moving the rule from advisory to hard-block, OR the rule is becoming irrelevant (stale).
- `flat` — steady-state; nothing to act on.
- `new` — rule didn't fire at all in prior window; first appearance is the diagnostic signal.

**Cross-link:** trend deltas here should triangulate with the most recent `/routing_battery` results. If continuous-audit silent-miss spikes but the last battery still passes, the doctrine is intact and the spike is task-specific (a sprint of unfamiliar code-discovery). If both the audit AND battery show the same rule degrading, doctrine drift is real and the rule needs a CLAUDE.md negative-framing addition or skill update.

**No-data state:** if `silent_misses == 0` and `cue_exempt_overrides == 0`, either (a) all routing is compliant (excellent), (b) no routing-classifiable calls happened in window (fresh project, recent rotation), or (c) audit hook isn't firing. Disambiguate via `total_entries` field — if zero entries, suspect (c) and check `settings.json` for `routing_audit.py` wire-up.

### Section 5: Key Corrections Log
Chronological table of sessions with corrections:

> [!warning]- Correction History
> | # | Date | Session | What Happened | Root Cause | Pattern |
> |---|------|---------|---------------|------------|---------|
> | 3 | 2026-01-14 | Status Effect Stacking | Skipped RED step | Discipline | A |
> | ... | ... | ... | ... | ... | ... |

### Section 6: Most Valuable Rules & Skills
Analyze the archive to identify which rules, skills, and memory entities were cited most often as either:
- **Preventing issues** (proactive value) — e.g., "Memory search for 'pool' surfaced needed gotchas"
- **Catching issues** (reactive value) — e.g., "User caught .tres TDD gap"

> [!success]- High-Value Rules & Skills
> | Rule/Skill/Entity | Times Cited | Preventive | Reactive | Notes |
> |-------------------|-------------|------------|----------|-------|
> | Memory search at session start | N | N | N | ... |
> | Refactoring_Verification_Pattern | N | N | N | ... |
> | ... | ... | ... | ... | ... |

### Section 7: Strength & Weakness Profile
Based on all data, characterize what task types, scales, and scopes the agent excels at vs. struggles with:

> [!example]- Agent Strengths
> Bullet list of task types, system scales, and workflows where the agent consistently performs well. Include evidence (session #s).

> [!danger]- Agent Weaknesses
> Bullet list of task types, scopes, or situations where corrections cluster. Include evidence and whether the weakness is improving, stable, or recurring.

### Section 8: Conclusions & Recommendations
**This is the most important section.** Synthesize ALL of the above into actionable insights:

1. **Pipeline Health:** Is the design pipeline (CLAUDE.md + Skills + Memory + Hooks) working? What's the trend?
2. **Recommended Changes:** Based on correction patterns, are there specific rules, skills, or hooks that should be added, modified, or removed? Be specific — cite the evidence.
3. **Diminishing Returns Check:** Are there areas where more documentation/hooks would NOT help (Pattern A — discipline gaps)? Call these out to prevent context bloat.
4. **Scale & Scope Insights:** What size/complexity of tasks does the agent handle best? Where does it break down? Should session structure change for certain task types?
5. **Next Milestone:** What would the next meaningful improvement look like? Define it concretely (e.g., "15 consecutive clean sessions across all domains" or "zero Pattern A failures for 2 weeks").
6. **Skill Drift Watchlist:** which skills' clean rates dropped in the most recent window (Section 4b ↓ trend, ideally ≥10pp drop)? These are the candidates for the next `/autolearn` pass — observed regressions, not vibes. Cite the sessions that drove the drop. If no skill is drifting, say so explicitly so the absence is visible rather than implicit.

## Execution Notes
- **Structured entries:** Use fields directly — `outcome`, `pattern`, `domains`, etc. No parsing needed.
- **Legacy entries:** Parse heuristically for: date, session number, clean/correction/failure, domains touched, patterns referenced
  - Entries like "#8-#19 ARCHIVED" count as individual clean sessions (12 sessions)
  - Entries with "USER CORRECTION" or "CRITICAL" = correction/failure
  - Entries with "Zero corrections" or "Clean" = clean
  - Use judgment for ambiguous entries — explain classification in a footnote if needed
- **Structured data enriches Sections 4 & 6:** `domains[]`, `skills_used[]`, `memory_hits[]`, and `tests{}` fields enable precise Domain Performance and Most Valuable Rules analysis. Legacy entries require inference for these sections.
- Obsidian callout style per user preference: `> [!type]- Descriptive Title` (NOT generic "Details")
