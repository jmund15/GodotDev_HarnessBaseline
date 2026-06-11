---
disable-model-invocation: true
---

Audit and compress `.claude/CLAUDE.md` when over the 200-line target.

Over-long always-loaded files reduce instruction-following adherence (per [Anthropic guidance](https://code.claude.com/docs/llms.txt)) — this is a correctness gate, not just a cost lever.

**Universal principles** live in the [`instruction_quality` skill](../skills/instruction_quality/SKILL.md) — single source of truth shared with `/instruction_audit`. CLAUDE.md-specific extensions (compression workflow, inaugural-run don'ts) live below.

## When to invoke

- `wc -l .claude/CLAUDE.md` returns >220 (some headroom over 200; don't churn at the boundary).
- After major harness additions that visibly bloated CLAUDE.md.
- Periodic hygiene (quarterly is reasonable; the harness moves fast enough that drift is real).

## Procedure

### Phase A — Measure (no edits)

1. Run `wc -l .claude/CLAUDE.md` (PowerShell: `(Get-Content .claude/CLAUDE.md).Count`). ⚠ NEVER `Measure-Object -Line` — it skips blank lines and undercounts by ~20%, enough to flip this phase's verdict.
2. **≤200 lines:** Report "no action needed" and exit. Do not run audit just because the user invoked the command — `/claudemd_compact` is a no-op when CLAUDE.md is already in spec.
3. **201–220 lines:** Advisory only. Report current count + headline targets but do NOT proceed. *"Approaching cap; track but no urgency"* unless user overrides with `--force`.
4. **>220 lines:** Proceed to Phase B.

### Phase B — Audit (read-only, three Explore agents in parallel)

Dispatch all three in a single message. Each returns a focused report; do NOT pre-read CLAUDE.md sections yourself before delegating — that defeats the cost model. An agent's EMPTY search result is INCONCLUSIVE, not evidence of "UNIQUE" / "no conflict" — re-run that specific search yourself before accepting a no-finding verdict (`gotcha_grep_glob_miss_tracked_files`).

**Agent 1 — Path-scope candidates.** For each top-level section of CLAUDE.md, classify by path-scope axis:
- Universal (every interaction)
- C# only (`**/*.cs`)
- Godot data (`.tscn`/`.tres`/`.godot`)
- Tests only (`Tests/**`)
- Cloud sessions only (env-conditional, NOT path)
- Submodule (`Jmodot/**`, `.gitmodules`)
- Specific external command/skill

Report: section heading → path-scope category → line count → migration verdict (HELPS / NO-OP / WRONG-MECHANISM).

**Agent 2 — Duplication scan.** Cross-reference each CLAUDE.md section against:
- `.claude/skills/**/SKILL.md` (existing canonical content)
- `.claude/commands/**/*.md` (existing recipes)
- Auto-memory file-based entries (`MEMORY.md` index + `feedback_*.md`)

Report: DUP / PARTIAL / UNIQUE per section, with file path evidence. PARTIAL means *the canonical home exists elsewhere; CLAUDE.md should reference, not restate.*

**Agent 3 — Internal-conflict scan.** Look for pairs of statements within CLAUDE.md that recommend opposing actions on the same decision. Specifically:
- Tables vs prose (does the table's "avoid" column contradict prose elsewhere?)
- Negative-reinforcement bullets vs exception clauses (single-decision-with-3-overrides shape)
- Skill cross-references vs inline restatement (does CLAUDE.md tell Claude to use a skill while also reproducing the skill's content?)

The 2026-05-03 inaugural run found a §2-forbidden-keyword-list vs Proactive-Context-Loading-table conflict on `refactor` and `MCP`. Pattern: structured data vs prose drift. Report each finding with quoted text from both sides.

### Phase C — Plan (use Plan Mode)

Synthesize agent findings into a phased plan at `.claude/plans/<YYYY-MM-DD>-claudemd-compact.md`:

1. **Path-scope extractions** to `.claude/rules/*.md` with `paths:` frontmatter (see canon below).
2. **Duplication eliminations** — convert restated content to one-line cross-references.
3. **Conflict resolutions** — pick the single source of truth, update the loser to point at it.
4. **Section compressions** — target lines per section, with rationale.

Each phase shippable independently. Estimate line savings per phase. Goal: land ≤200 with some headroom.

### Phase D — Execute (after user approval via ExitPlanMode)

Apply edits in dependency order:
1. Create new rule files first (so cross-references resolve when CLAUDE.md updates).
2. Replace migrated sections with one-line pointers.
3. Compress remaining sections.
4. Resolve internal conflicts last (so the resolved version reflects post-compression structure).

### Phase E — Verify

1. `wc -l .claude/CLAUDE.md` — confirm ≤200.
2. **Path-scope sanity:** open a representative file from each new rule's `paths:` glob via Read in a fresh session, run `/memory`, confirm the rule appears in the loaded list. If a rule doesn't load, the YAML frontmatter likely failed to parse — common cause is inline-vs-list-form syntax mismatch with the loader.
3. **Internal-conflict regression check:** re-run Agent 3's conflict scan against the new state. Should report zero findings.
4. Capture in autolearn: what was extracted, what duplications were eliminated, what conflicts were resolved.

## Frontmatter format for rule files

YAML list-form preferred (more readable + easier to extend with comments):

```yaml
---
paths:
  - "**/*.cs"
  - "**/*.csproj"
---
```

Inline form `paths: ["**/*.cs"]` also valid but discouraged for >1 path.

A rule WITHOUT `paths:` loads always — equivalent to inline CLAUDE.md content for context-loading purposes. Use only when the content benefits from file-level separation but has no clean path-trigger (rare; usually means the content shouldn't be in rules at all).

## Don'ts (failure modes the inaugural run identified)

- **Don't compress the Proactive Context Loading table.** Dense lookup tables are correct at the format level — Claude scans them efficiently. Compressing prose is fine; compressing tables loses information per byte.
- **Don't move Core Principles 1–8 out of CLAUDE.md.** Those are the always-loaded orienting principles. Path-scoping or skill-extraction would defeat the purpose.
- **Don't extract `Build & Test Commands` to a separate rule file.** The three load-bearing rules (`--filter`, `timeout=600000`, `--no-build`) need to be visible to *every* test-adjacent action. Inline or skill cross-reference; not path-scoped.
- **Don't touch the path-scoped rule files** to hit the line target. The 200-line cap is for CLAUDE.md. Rule files load conditionally — their size is amortized across only the sessions that load them.
- **Don't skip Phase B to save time.** The compression IS the audit; bypassing the read-only agent scans means you're guessing where the bloat is.

## Inaugural-run reference data (2026-05-03)

For calibration if you're unsure whether your current proposed cut is reasonable:

- Original: 358 lines → Final: 192 lines (−166, 46% reduction).
- Biggest single compression: Hybrid TDD section (27→5 lines) by deferring full procedure to the testing skill. Pattern: *when CLAUDE.md drifts from "decision gate" toward "reference manual," it's compressible.*
- Path-scope extractions saved ~46 raw lines but only ~32 net (cross-reference replacements take 1–2 lines each).
