---
disable-model-invocation: true
---

Create or append to a Development Retrospective in Obsidian.

## Audience
Future Claude agents and curious humans who want to understand the development history — what was considered, what was decided, how it went, and what was learned.

## Context Tiers — What You Have Determines What You Write
This command produces different output depending on the quality of available development history. **Do NOT fabricate retrospective content.**

### Tier 1: Rich Session Context (Full Retrospective)
**Sources available:** Current conversation context AND/OR transcript backups (`.summary.json`)
**Output:** Full narrative entry with all sections — options considered, Claude's lean vs user's preference, development journey, TDD highlights, plan vs outcome analysis.
**When:** Called at end of a session where this system was actively developed.

### Tier 2: Reconstructed History (Concise Summary)
**Sources available:** Only git history and/or auto-memory entities — no session transcripts.
**Output:** A condensed entry explicitly marked as `> [!warning] Reconstructed from git history and memory — not a first-person session account.` Includes: date range from commits, scope of changes, architectural decisions from memory, key commits. Omits subjective sections (Claude's lean, development journey narrative, hiccups).
**When:** Called via `/doc_full` on a preexisting system, or retrospecting on work done in a previous session without transcript backups.

### Tier 3: No History (Abort)
**Sources available:** None of the above contain meaningful development history for the system.
**Output:** Inform the user: "No development history found for {SystemName}. The retrospective command should be run at the end of a session where this system was actively developed, or after the system has meaningful git history." Then abort.

### Source Check Order
Check these sources to determine your tier:
1. **Current conversation context** (Tier 1 if system was worked on this session)
2. **Transcript backups** (`logs/transcript_backups/*.summary.json` via `logs/pre_compact.json` index) (Tier 1)
3. **auto-memory files** related to the system (semantic-search with system-relevant keywords) (Tier 2)
4. **Git history** (commits, diffs touching system files) (Tier 2)

If sources 1 or 2 have content → Tier 1. If only 3 or 4 → Tier 2. If none → Tier 3.

## Recovering Session Context

### Step 1: Check for compaction
Look for compaction indicators:
- "This session is being continued from a previous conversation" in context
- Entries in `logs/pre_compact.json` matching the current session ID

### Step 2: Read transcript backups (if compacted)
```
logs/pre_compact.json                    # Index of all backup files
logs/transcript_backups/*.summary.json   # Pre-parsed summaries (PREFERRED)
logs/transcript_backups/*.jsonl          # Raw transcripts (FALLBACK)
```

**From `.summary.json` files, extract:**
- `user_messages[]` where `signals` contains `"correction"` — pivots and direction changes
- `user_messages[]` where `signals` contains `"instruction"` — requirements given
- `tdd_feedback_loops` — error→resolution pairs
- `errors.resolved` and `errors.unresolved`
- `metadata.total_messages` / `metadata.total_tool_calls` — session scale

### Step 3: Supplement with git history
- `git log --oneline` for commits related to the system
- `git diff` for understanding scope of changes

### Step 4: Check auto-memory
- semantic-search with system name and related keywords
- Look for architectural decisions, gotchas, or preferences saved during development

## Before Writing

Follow steps 1-2 from [Doc Before Writing](agents/doc_before_writing.md). Target doc path: `{SystemName}/Retrospective.md`.

**Step 3 (Retrospective-specific):** Check for existing doc via `obsidian_read_note`. If it exists, you are APPENDING a new timestamped entry — do NOT modify prior entries. Read the existing doc to avoid repeating information. If it doesn't exist, create from scratch with the document header and first entry.

## Document Structure

Place at: `DevProjects/{{PROJECT_NAME}}/Claude/Documentation/{SystemName}/Retrospective.md`

```markdown
# {SystemName} — Development Retrospective

> [!abstract] About This Document
> A chronological development journal capturing the lifecycle of {SystemName} from the perspective of the Claude agent. Each entry documents a session's problem, decisions, journey, and lessons.

## Table of Contents
- [[#Entry: YYYY-MM-DD — {Session Scope}]]
- ... (one link per entry, newest last)

---

## Entry: YYYY-MM-DD — {Brief Session Scope}

### Problem / Use Case
What prompted this work? What was the user trying to achieve?
What was the state of the system before this session?

### Solution Exploration

> [!question]- Options Considered
> Exhaustive list of solution approaches that had merit:
> 1. **{Option A}** — Description. Pros: ... Cons: ...
> 2. **{Option B}** — Description. Pros: ... Cons: ...
> 3. **{Option C}** — ...
>
> **Claude's initial lean:** {which option and why}
> **User's preference:** {what the user wanted and why}
>
> **Decision tree** — include when 3+ options had real branching; the edge labels carry the *why-rejected* that a prose list can't show. Skip for a simple 2-option pick. Follow the `mermaid_diagrams` skill.
> ```mermaid
> flowchart LR
>     Problem --> OptionA[Option A]
>     Problem --> OptionB[Option B]
>     Problem --> OptionC[Option C]
>     OptionA -->|fails constraint X| Rejected
>     OptionC -->|fails constraint Y| Rejected
>     OptionB -->|meets all constraints| Chosen
> ```

### Decision & Rationale

> [!info]- Final Decision
> What was chosen, and the definitive reasoning behind it.
> If Claude and user disagreed, document how alignment was reached.

### Development Journey

> [!example]- Plan of Attack
> What was the strategy going in? Why this approach?
> Did the plan change during development? If so, what triggered the change?

> [!bug]- Hiccups & Issues
> Problems encountered during implementation.
> How each was diagnosed and resolved.
> What would have prevented it in hindsight?

> [!success]- Key TDD Tests
> The most important and impactful tests written during development.
> What behavioral contracts did they establish?
> Which tests caught real bugs vs. which confirmed expected behavior?

### Final Product vs Initial Plan

> [!question]- Differences & Evolution
> Were there meaningful differences between the initial plan and final result?
> If so: what changed, why, and what insight led to the improvement?
> If plan held: what about the planning phase made it accurate?

### Key Takeaway
> Single most important lesson from this session. Be specific and actionable.

---
```

### Tier 2 Template (Reconstructed History)
When only git/memory sources are available, use this condensed format instead:

```markdown
## Entry: YYYY-MM-DD — {System Name} (Reconstructed)

> [!warning] Reconstructed from git history and memory — not a first-person session account.

### Overview
- **Date range:** {earliest commit} to {latest commit}
- **Scope:** Brief description of what was built or changed.
- **Key commits:**
  - `{hash}` — {message}
  - `{hash}` — {message}

### Architectural Decisions (from Memory)

> [!info]- {Decision Title}
> What was decided and any rationale captured in memory entities.

### Summary
Concise factual summary of the system's development based on available evidence.
What was built, in what order, and what the final state is.

---
```

## Update Behavior — APPEND ONLY
- **New doc:** Write the document header (title, abstract, ToC) and the first entry (Tier 1 or Tier 2 format based on available context).
- **Existing doc:** Read the full document. Append a new `## Entry: YYYY-MM-DD` section at the bottom. Update the Table of Contents to include the new entry. **NEVER modify prior entries** — they are historical records. Use the appropriate tier template based on current context quality.
- **Quick Reference:** If `Quick Reference.md` exists in the same folder, update the Retrospective entry in its Document Index to reflect the latest entry date.

## Tone & Honesty
- Write in first person as the Claude agent. Be candid.
- Be honest about mistakes, wrong assumptions, and things that didn't go as planned.
- Give credit to user corrections and pivots — document THEIR reasoning too.
- Don't sanitize the journey — the messiness is valuable context for future agents.
- Specific > generic. "The Strategy pattern avoided a 12-case switch" > "Good patterns were used."

## Formatting Rules
- Use `> [!type]- Collapsible Title` for all subsections within `##` entries. Do NOT use `###` headers inside entries except for the fixed section headers (Problem, Solution Exploration, etc.).
- Callout types: `question` (options/rationale), `info` (decisions), `example` (plans), `bug` (issues), `success` (tests)
- Each entry is a self-contained narrative — readable without context from other entries.
- Include timestamps on each entry (date in header, ISO format).

## Writing Vault Files — Append-Only via `write_doc`
Generate prose through `write_doc`, not by hand (Documentation Delegation Rule, HARD). Because this doc is APPEND-ONLY, the worker generates **only the new dated entry** — never the whole file. Follow **Reason, Then Delegate** in [Doc Before Writing](agents/doc_before_writing.md) with `doc_type="retrospective"` and `Voice/tone: first-person candid`.
- **New doc:** the spec's `Outline` is the header + ToC + first entry; `write_doc` writes the whole file once.
- **Existing doc:** call `write_doc` to a scratch path *under the vault* (e.g. `{SystemName}/_retro_entry.tmp.md`, so the `obsidian` modifier still auto-applies) with an `Outline` of just the new `## Entry: YYYY-MM-DD` section → the worker emits only that entry's prose. Then `Read` the existing doc, `Edit` to append the entry at the bottom and add its ToC link, and delete the scratch file. **NEVER** pass prior entries through a regen — they are historical records.
