Phase 2 of `/session_end`. Detect corrections and recurring preferences from the session; propose minimal reversible edits to active Skills or auto-memory. Filter for quality and overfit before proposing.

## When to activate

Trigger on explicit requests:
- "autolearn", "learn from this session", "update skills from these corrections"
- "remember this pattern", "make sure you do X next time"

Do NOT activate for one-off corrections or when the user declines skill modifications.

## Step 0: Recover Context from Compaction (MANDATORY FIRST STEP)

**BEFORE signal detection**, check if this session has compacted. If compaction occurred, you MUST read transcript backups first—critical corrections are often lost to compaction.

**Step 1: Check for compaction**
Look for compaction indicators in the conversation:
- "This session is being continued from a previous conversation that ran out of context"
- References to pre-compaction summaries
- Presence of a `pre_compact.json` file with recent entries

**Step 2: Read transcript backups**
If compaction occurred, read transcript backups from `logs/transcript_backups/`:

```
logs/pre_compact.json                    # Index of all backup files with summary_path
logs/transcript_backups/*.summary.json   # PRE-PARSED summaries (PREFERRED - use these first)
logs/transcript_backups/*.jsonl          # Raw transcripts (fallback if summary missing)
```

**Workflow:**
1. Read `logs/pre_compact.json` to find recent backups for the current session ID
2. **PREFERRED:** Read the `.summary.json` file (if `summary_path` exists in pre_compact.json entry)
   - Contains pre-extracted `user_messages` with `signals` and `matched_patterns`
   - Contains `tdd_feedback_loops` (error→resolution pairs)
   - Contains `errors.resolved` and `errors.unresolved` lists
3. **FALLBACK:** If no summary, read the `.jsonl` transcript and search manually
4. Extract signals from the full session history, not just post-compaction context
5. **Nuance recall (conditional):** if this was a complex/long/pivot-heavy session, run
   [Transcript Nuance Recall](agents/transcript_nuance_recall.md) to catch implicit signals
   the regex digest misses. Candidates enter at MEDIUM confidence through the normal filter.

**Why:** Critical corrections often happen early in long sessions and get lost during compaction. The transcript backups preserve the full conversation history.

## Learning Strategy and Distinctions
**Context:** You have two long-term knowledge banks. Use them distinctively.
**A. Auto-memory (file-based, `.claude/auto-memory/`)**
*   **Trigger:** Small, context-specific facts, preferences, and gotchas.
*   **What to Store:**
    *   User preferences ("User hates `var`", "User prefers `private` fields").
    *   Environment quirks ("Windows command line needs double quotes").
    *   Specific "Gotchas" discovered during debugging.
*   **Tier choice:**
    *   **Hot** (topic file + `MEMORY.md` pointer): surprising/cross-cutting rules to surface every session.
    *   **Cold** (`archive/`, no pointer): bulk domain reference or low-frequency detail — searchable, zero passive cost.
*   **Constraint:** Do NOT store large code blocks here; link to source instead.

**B. Skills (The Handbook)**
*   **Trigger:** Rules or preferences affecting larger architectural or design decisions.
*   **What to Store:**
    *   Architectural decisions (e.g., "Ban Godot Groups").
    *   Reusable Workflows (e.g., "New Spell Checklist").
    *   Framework usage rules (`Jmodot` patterns).
*   **Action:** Triggers an update to `.claude/skills/*.md`.

**Decision Rule:**
*   Is it a **Preference** or **Fact**? -> **auto-memory** (hot topic file, or `archive/` for bulk).
*   Is it a **Rule** or **Process**? -> **SKILL update**.

## Signal detection

Scan the session for:

**Corrections** (highest value)
- "No, use X instead of Y"
- "We always do it this way"
- "Don't do X in this codebase"

**Repeated patterns** (high value)
- Same feedback given 2+ times
- Consistent naming/structure choices across multiple files

**Approvals** (supporting evidence)
- "Yes, that's right"
- "Perfect, keep doing it this way"

**Ignore:**
- Context-specific one-offs ("use X here" without "always")
- Ambiguous feedback
- Contradictory signals (ask for clarification instead)

## Signal quality filter

Before proposing any change, ask:
1. Was this correction repeated, or stated as a general rule?
2. Would this apply to future sessions, or just this task?
3. Is it specific enough to be actionable?
4. Is this **new information** I wouldn't already know?
5. **Falsifiable mechanism claim?** If the entry asserts how a tool/engine/API *behaves* ("X isolates Y", "Z is case-sensitive", "A causes B") — not a preference or observed event — it needs **direct evidence that isolated it**. A fix that changed >1 thing, or a single observation, is NOT verification. Run the 30-second falsification check now, or save only the observed fact — never the inferred mechanism. A saved mechanism claim MUST carry a `**Verified:** <the check that confirmed it>` line; a mechanism entry lacking it is a defect (greppable by the memory-claim audit).

Only propose changes that pass all five.

### Anti-pattern: Redesign vs Execute
When a procedure fails, diagnose execution before redesigning. Check:
1. Did I actually follow all steps of the existing procedure?
2. Does the infrastructure (hooks, logs, summaries) exist and work?
3. Is the failure in the procedure itself, or in my adherence to it?

Only redesign if the procedure is fundamentally flawed. If execution skipped a step, the fix is emphasis/clarity on that step, not a rewrite.

### Anti-pattern: Overfit-to-Specific

A captured rule that names a specific file path, PR number, spell name, commit SHA, or session date is overfit. The rule will not survive being re-read in 6 months when those details are stale. **Rewrite as a principle, or revert.**

The concrete details still have value as **evidence** — keep them in the `Signal:` line of the proposal (and as `Concrete:` / `Source:` / `Why:` lines on the resulting memory entry), where they prove the principle was observed in the wild. They do not belong in the principle itself.

**Litmus test before saving:**
1. Does the rule name a specific file, function, PR, spell, or commit?
2. Could a reader from another project apply this rule without that name?
3. If the answer is "no, only {{PROJECT_NAME}}'s `<thing>`" — rewrite or skip.

**Worked example.** A correction "don't run `/regression_gate` after touching `burn_effect.tres`" is overfit (`burn_effect.tres` is one file). Rewrite as "treat `.tres` files that affect Logic Domain behavior as code for the regression gate" — applies to every future `.tres`, and the original `burn_effect.tres` incident becomes the `Signal:` evidence line.

**Why this matters here.** The `MEMORY.md` index auto-loads into every session and is capped (~200 lines). Special-case rules accumulate faster than they age out, and each one that names a specific thing instead of a class of things is a future search miss — a semantic-search for "data file" or "Logic Domain" should surface the rule; a title that only says `burn_effect.tres` won't.

This same gate is applied retroactively to existing entries by `/memory_audit`'s overfit lens (lens 2). (`anthropic-skills:consolidate-memory` handles the orthogonal dedup / durable-vs-dated / index-pruning surface — it does not apply this gate.)

### What counts as "new information"

**Worth capturing:**
- Project-specific conventions ("we use `NodeExts` here")
- Custom component/utility locations ("buttons are in `res://components/ui`")
- Team preferences that differ from defaults
- Domain-specific terminology or patterns
- Non-obvious architectural decisions
- Integrations and quirks specific to this stack

**NOT worth capturing (I already know this):**
- General best practices (DRY, separation of concerns)
- Language/framework conventions (.NET and Godot basics)
- Common library usage (standard C# classes, typical Godot patterns)
- Standard accessibility guidelines

If I'd give the same advice to any project, it doesn't belong in a skill.

## Rules & Constraints for ALL Additions

- Stay within the **brevity budget** below — bloat compounds across compactions.
- Never delete existing rules without explicit instruction
- Prefer additive changes over rewrites, unless you notice redundancy.
- One concept per change (easy to revert)
- Preserve existing file structure and tone
- When uncertain, downgrade to MEDIUM confidence and ask

### Brevity budget (hard targets, not aspirations)

The `MEMORY.md` index auto-loads into every session (first 200 lines / 25KB), and a hot topic file is read whenever its index line is followed — bloat there is paid repeatedly. Write at the budget *first*; do not write long and "trim later" — the trim never comes. Cold `archive/` files are search-only, so length matters far less there.

| Where | Target | Hard cap |
|---|---|---|
| MEMORY.md index entry | ≤ 120 chars | 150 |
| Hot topic file body (rule + Why + How) | as short as the rule allows | ~500 chars |
| Cold archive file body | as long as the reference genuinely needs | n/a |

A budget-violation on a hot entry signals *split into two principles* (or demote bulk detail to `archive/`), not "allow more characters." If a hot rule + concrete genuinely needs > 500 chars, it's probably two rules.

### Anti-patterns that produced the 2026-04-30 compaction debt

Don't write observations that look like any of these. Each was a real entry the compaction had to rewrite or delete:

- **Stacked errata.** "CORRECTION 2026-04-30 (supersedes obs #1 + #3): ..." — *consume* the correction by editing obs 1+3 in place; never leave both versions.
- **Incremental followups.** "EXPANDED PATTERN (2026-04-26 followup): the int-Export bug ALSO bites bool fields ..." — *merge* into one general rule covering all variants the day the second variant lands.
- **Narrative timelines in the headline.** "BT-TREE-RESTART HOT LOOP gotcha (Phase 1e.3 B9.3 playtest, 2026-04-21): ..." — phase IDs, dates, commits belong on a single `Concrete:` line at the bottom, not spliced through the rule prose.
- **Restatement loop.** "RULE: ... RECOMMENDATION: ... LESSON: ... RULE: ..." stating the same principle four ways. Pick the strongest sentence; delete the rest.
- **Multi-paragraph observations.** A gotcha is one paragraph + (optional) one `Concrete:` line. If you find yourself writing "Furthermore," or starting a second paragraph, you have two observations.
- **Inline implementation tour.** "Root cause is in src/metrics/symbol-match.ts where COLUMN_WEIGHTS aggregation can only amplify existing scoring ..." — code-internal reasoning belongs in source comments or commit messages, not memory. The memory entry is the *consumer-facing rule*.
- **Symptom + Detection + Fix + Recovery + Prevention sections.** This is documentation, not a memory observation. If a gotcha needs all five, it's a runbook — write it as a cold `archive/` memory file or a skill section, then a one-line `MEMORY.md` pointer if it warrants hot-tier surfacing.
- **"Discovered 2026-04-25 in PR #59 — wizard.tscn line 2110 ...".** PR # / line number / file path in the rule itself is overfit (existing Anti-pattern: Overfit-to-Specific). Move to `Concrete:`.

Litmus before saving: *would a future-me searching for this rule benefit from any of those extra words, or would they just have to skim past them?* If the latter, cut.

## Mapping signals to Memory

If the signal maps to a **Preference** or **Fact**, add to **auto-memory** (`.claude/auto-memory/`).

**Step 1: Create or update a topic file**
- If the signal extends a preexisting topic file, append to that file's body.
- If it's a new concept, create a new `<slug>.md` topic file (frontmatter: `name`, `description`, `metadata.type` of user|feedback|project|reference).
- **Hot tier** (surfaced every session): add a one-line pointer to `MEMORY.md` in the *same turn*. **Cold/bulk** reference: place under `archive/`, no pointer.

**Step 2: Link related memories**
- Cross-link related topic files with `[[other-file-slug]]` wikilinks in the body.
- A `[[name]]` that doesn't resolve yet is fine — it marks a future memory worth writing.
- Wikilinks improve discoverability — they connect a found memory to its neighbors.

**Step 3: Catalog the failure mode (if applicable)**
When the new entity codifies a **recurring failure pattern** (regression class, bug shape, mistake-shape with detection signal), propose a corresponding entry in [`checklists/known_failure_modes.md`](checklists/known_failure_modes.md). The catalog is the **detection-pattern layer** that critic agents (`/plan_check`'s `plc-memory-alignment`, `/session_audit --include-failure-history`) load on demand.

Trigger: the new entity describes a *bug class* (not a *preference* or *fact*). Litmus — would another future occurrence of this class be catchable by a grep regex, LSP query, or structural pattern? If yes, the catalog entry adds value.

Format per entry (5 lines, see `known_failure_modes.md` header for full spec):
```
### <failure name>
**Incident**: <PR # / date / one-line summary>
**Detection**: <grep regex | LSP query | structural signal>
**Memory**: auto-memory file `<filename>.md`
**Catches you when**: <one-line scenario where this fires>
```

If no concrete detection signal exists, the entry isn't ready — defer to the next pass when the pattern surfaces a second time and the signal becomes clearer. Catalog entries are append-only; manual sweeps are not part of normal autolearn flow.

## Proposing memory changes

For each proposed edit, provide a human readable format of the memory additions and updates.

Present HIGH confidence changes first.

### Review flow

Always present changes for review before applying. Format:

```
## autolearning memory summary

Detected [N] durable preferences from this session to update auto-memory.

### HIGH confidence (recommended to apply)
- [change 1]
- [change 2]

### MEDIUM confidence (review carefully)
- [change 3]

Apply high confidence changes? [y/n/selective]
```

Wait for explicit approval before updating memory.

## Mapping signals to Skills

If the signal maps to a **Rule** or **Process**, justifies a **SKILL update**. Skills updates have a larger impact than memory additions, so we have a longer workflow here.

Match each signal to the Skill that was active and relevant during the session:

- If the signal relates to a Skill that was used, update that Skill's `SKILL.md`
- If 3+ related signals don't fit any active Skill, propose a new Skill
- Ignore signals that don't map to any Skill used in the session

### Proposing SKILL changes

For each proposed edit, provide:

```
File: path/to/SKILL.md
Section: [existing section or "new section: X"]
Confidence: HIGH | MEDIUM

Signal: "[exact user quote or paraphrase]"

Current text (if modifying):
> existing content

Proposed text:
> updated content

Rationale: [one sentence]
```

Group proposals by file. Present HIGH confidence changes first.

### Review flow

Always present changes for review before applying. Format:

```
## autolearning skill summary

Detected [N] durable rules from this session.

### HIGH confidence (recommended to apply)
- [change 1]
- [change 2]

### MEDIUM confidence (review carefully)
- [change 3]

Apply high confidence changes? [y/n/selective]
```

Wait for explicit approval before editing any file.

### Applying changes

When approved:
1. Edit the target file with minimal, focused changes
2. If git is available, commit with message: `chore(autolearn): [brief description]`
3. Report what was changed