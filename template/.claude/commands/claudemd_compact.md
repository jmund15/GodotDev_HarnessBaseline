---
description: Audit and compress CLAUDE.md when it exceeds the always-loaded byte budget.
disable-model-invocation: true
---

Audit and compress `.claude/CLAUDE.md` when over the M1 byte budget (28–32 KB band; Phase B above 35 KB).

Over-long always-loaded files reduce instruction-following adherence (per [Anthropic guidance](https://code.claude.com/docs/llms.txt)) — this is a correctness gate, not just a cost lever.

**Universal principles** live in the [`instruction_quality` skill](../skills/instruction_quality/SKILL.md) — single source of truth shared with `/instruction_audit`. CLAUDE.md-specific extensions (the standard below, compression workflow, inaugural-run don'ts) live here.

## The Standard

What earns a place in CLAUDE.md, and how deeply it may explain itself. Apply at audit time and when judging any proposed addition.

Gaps at M2–M4, M7, M8 are intentional, not renumbered away: the labels are stable citation anchors (`reference/memory_domains.md` cites M5 by number) and closing the gaps would silently repoint every existing citation at the wrong rule.

- **M1 — Budget bytes, not lines.** Band **28–32 KB** (30 KB centre), advisory 32–35 KB, Phase B above 35 KB. The gate mechanism (`wc -c`, not line count) is `instruction_quality` §5 **A1**.

  The band is **derived, not borrowed**: apply the `instruction_quality` §5 **A2** admission test section by section to THIS project's CLAUDE.md and measure what survives — that number is the band's centre. An aspirational figure copied from a smaller file just gets missed every run and then ignored — re-derive it when the retained set changes materially, rather than treating it as fixed.
- The surface-independent rules that used to sit at M2/M3/M4/M7 — admission ordering, enforcement-tier-sets-depth, never-restate-a-SessionStart-injection, and the density census+delta method — now live at `instruction_quality` §5 **A2–A5**. This file states only what is genuinely CLAUDE.md-specific below.
- **M5 — Never name which skill to load.** Skill descriptions are auto-injected with full trigger and SKIP text, so a "load skill X for domain Y" mapping is already in context. CLAUDE.md may carry only what a description structurally cannot — a memory search term, a cross-domain sequencing rule.
- **M6 — Exempt: identity and refusal.** The author's-voice framing section, Core Principles, and the rationalizations-to-refuse table are never compressed, path-scoped, or extracted. The first two are the orienting frame every other rule is read against; the third fires under user pressure, which no trigger models.

## When to invoke

- `wc -c .claude/CLAUDE.md` returns >35,000 (M1's band is 28–32 KB; don't churn at the boundary).
- After major harness additions that visibly bloated CLAUDE.md.
- Periodic hygiene (quarterly is reasonable; the harness moves fast enough that drift is real).

## Procedure

### Phase A — Measure (no edits)

1. Run `wc -c .claude/CLAUDE.md` (PowerShell: `(Get-Item .claude/CLAUDE.md).Length`). Bytes are the gate per M1; report `wc -l` alongside as a secondary readout only. ⚠ NEVER `Measure-Object -Line` for the line readout — it skips blank lines and undercounts by ~20%.
2. **≤32,000 B:** Report "no action needed" and exit. Do not audit just because the user invoked the command — `/claudemd_compact` is a no-op when CLAUDE.md is already in spec.
3. **32,000–35,000 B:** Advisory only. Report the current size + headline targets but do NOT proceed. *"Approaching cap; track but no urgency"* unless the user overrides with `--force`.
4. **>35,000 B:** Proceed to Phase B.
5. **Emit the census regardless of verdict**, per `instruction_quality` §5 **A5**'s method (per-section bytes sorted descending, plus each section's byte delta) — this file's delta anchor is the *previous compaction commit*. This is the artifact that makes density drift visible; a size verdict without it hides the sections that actually grew.

```bash
awk '/^#{2,4} /{if(h)printf "%6d B  %s\n",b,h; h=$0; b=0} {b+=length($0)+1} END{printf "%6d B  %s\n",b,h}' .claude/CLAUDE.md | sort -rn
```

### Phase B — Audit (read-only, four audit agents in parallel via `dispatch.js` with `agentType: 'Explore'` + explicit model/effort pins — these lenses read and report rather than apply project doctrine, so the scout dispatch fits and costs about half; `orchestration` §5)

Dispatch all four in a single message. **Do NOT pass `args.spillDir`** — `Explore` has no `Write` tool, so a spilling scout job silently returns its deliverable inline and the bounded-return contract is lost without an error.

**Brief every lens with the §Don'ts list below.** Omitting it in the 2026-08-19 run produced two confidently-wrong extraction proposals that §Don'ts already pre-refutes — a briefing gap that reads as a lens error. Each returns a focused report; do NOT pre-read CLAUDE.md sections yourself before delegating — that defeats the cost model. An agent's EMPTY search result is INCONCLUSIVE, not evidence of "UNIQUE" / "no conflict" — re-run that specific search yourself before accepting a no-finding verdict (`gotcha_grep_glob_miss_tracked_files`).

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

**Agent 4 — Admission test.** The only lens that asks whether a section belongs in an always-loaded file at all. Agents 1–3 return UNIQUE / WRONG-MECHANISM / no-conflict for content that is one-of-a-kind, not path-scopable, and self-consistent — and none of those verdicts answers `instruction_quality` §5 **A2**. Without this lens the panel is structurally blind to the largest remaining class.

Its question is ordering, not conditionality: *when is this decision made, and does the proposed home load strictly before that moment?* Same-moment (a stub announcing a `paths:`-scoped rule) ⇒ redundant. Strictly-after (a fallback needed the instant a tool turns out to be missing) ⇒ earns its place. Richest territory is whatever Agents 1–3 marked UNIQUE or WRONG-MECHANISM. Hunt: content triggered by an explicitly-invoked command; provenance and retired-approach history; reference detail (extension lists, index paths, measurements) inside a section whose *rule* content is pre-trigger. Verdicts: `EARNS-IT` / `MOVE` / `COMPRESS` / `CUT`, each with exact current and replacement text, plus the proposal it is least confident in.

Pin this lens `high` — it is open discovery with no supplied inventory, unlike Agents 1–3.

**Re-read any text an agent quotes as "exact current text" before applying it.** Measured 2026-08-19: a lens's quoted block omitted a whole sentence present in the file. `Edit`'s exact-match failure is the backstop; a looser apply deletes content silently.

### Phase C — Plan

Synthesize agent findings into a phased plan at `.claude/plans/<YYYY-MM-DD>-claudemd-compact.md`:

1. **Path-scope extractions** to `.claude/rules/*.md` with `paths:` frontmatter (see canon below).
2. **Duplication eliminations** — convert restated content to one-line cross-references.
3. **Conflict resolutions** — pick the single source of truth, update the loser to point at it.
4. **Section compressions** — target lines per section, with rationale.

Each phase shippable independently. Estimate BYTE savings per phase, measured against the live file (M1 / `instruction_quality` §5 A5's census) — never estimated by eye. Goal: land inside the 28–32 KB band. When the measured cuts cannot reach it, say so with the arithmetic and treat the band as the standing target across runs, rather than inventing thinner-evidenced cuts to close the gap in one pass.

### Phase D — Execute (after the user approves the plan file)

Apply edits in dependency order:
1. Create new rule files first (so cross-references resolve when CLAUDE.md updates).
2. Replace migrated sections with one-line pointers.
3. Compress remaining sections.
4. Resolve internal conflicts last (so the resolved version reflects post-compression structure).

### Phase E — Verify

1. `wc -c .claude/CLAUDE.md` — confirm the band, and re-run the `instruction_quality` §5 **A5** census (previous compaction commit as delta anchor) to confirm no section over 3 KB survives outside the M6 exemptions.
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

- **Don't compress a lookup table's FORMAT — but do re-derive whether each column still describes a live mechanism.** Dense tables scan efficiently and lose information per byte when squeezed. That protects the shape, not the content: a memory-recall table's "avoid" column may describe a keyword-matching mechanism long after recall moved to semantic search, where the failure it warns about cannot occur. A column whose mechanism is gone is dead weight in a live format. Ask per column, not per table.
- **Don't move Core Principles out of CLAUDE.md.** They are the orienting frame every other rule is read against; path-scoping or skill-extraction defeats the purpose (M6).
- **Don't extract `Build & Test Commands` to a separate rule file.** The three load-bearing rules (`--filter`, `timeout=600000`, `--no-build`) need to be visible to *every* test-adjacent action. Inline or skill cross-reference; not path-scoped.
- **Don't touch the path-scoped rule files** to hit the budget. The byte band is for CLAUDE.md. Rule files load conditionally — their size is amortized across only the sessions that load them.
- **Don't skip Phase B to save time.** The compression IS the audit; bypassing the read-only agent scans means you're guessing where the bloat is.

## Inaugural-run reference data (2026-05-03)

For calibration if you're unsure whether your current proposed cut is reasonable:

- Original: 358 lines → Final: 192 lines (−166, 46% reduction).
- Biggest single compression: Hybrid TDD section (27→5 lines) by deferring full procedure to the testing skill. Pattern: *when CLAUDE.md drifts from "decision gate" toward "reference manual," it's compressible.*
- Path-scope extractions saved ~46 raw lines but only ~32 net (cross-reference replacements take 1–2 lines each).
