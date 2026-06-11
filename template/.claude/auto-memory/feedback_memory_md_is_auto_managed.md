---
name: MEMORY.md is agent-maintained (auto-memory protocol)
description: When writing a new topic file in the auto-memory dir, update MEMORY.md in the same turn to add the pointer. Auto-memory = Claude does both; no background indexer.
type: feedback
originSessionId: 15bc6648-e4d1-4a64-b970-d32e8c122873
---
When writing a new topic file in `.claude/auto-memory/`, **also update `MEMORY.md` in the same turn** to add the pointer. Auto-memory's "auto" means Claude (the agent) maintains the dir using normal file tools — not that a background subsystem indexes for you. Per the [canonical doc](https://code.claude.com/docs/en/memory): "Claude reads and writes memory files during your session ... When you see 'Writing memory' or 'Recalled memory' in the Claude Code interface, Claude is actively updating or reading from" the memory dir. The "automatic" part is *Claude takes the initiative* — not *something else handles it*.

**Why MEMORY.md being current is load-bearing:** Per the canonical doc, only the **first 200 lines / 25KB of `MEMORY.md`** are auto-loaded at SessionStart. Topic files are NOT auto-loaded — "Claude reads them on demand using its standard file tools when it needs the information." The only signal Claude has at SessionStart that a topic file exists is the pointer in MEMORY.md. **No pointer → rule effectively invisible at next session.** This was demonstrated 2026-05-11: two topic files written via raw `Write` tool, MEMORY.md not updated for 55+ minutes — the files were on disk but would have been invisible at next SessionStart.

**How to apply:**
- New behavioral rule / feedback / principle → write topic file (`feedback_*.md` / `arch_rule_*.md` / etc.) AND add a one-line pointer to MEMORY.md in the same turn under the appropriate section.
- Pointer format: `- [Title](filename.md) — one-line hook ≤150 chars`.
- Section choice: use the existing categories at the top of MEMORY.md (Communication & process discipline / Architectural & design rules / Testing & TDD / Tool routing & workflow / Jmodot framework / Godot / scene / data / Spell / combat / visual subsystems / Misc gotchas).
- If genuinely new and recent → "Recent additions" section at top works too.

**Counter-pattern (DON'T):** Set up hooks, slash commands, or `/session_end`-style workflows that hand-edit MEMORY.md outside the normal topic-file write flow. Those would race any future Claude session's own MEMORY.md rewrites (Auto Dream consolidation passes do full-file rewrites; even normal-mode Claude in another session may re-shape the index based on its own model of what should be there). The safe write-window is **same-turn-as-topic-file**, because at that point Claude (you) is the only writer and the action is atomic from the user's view.

**Counter-pattern (DON'T):** Add MEMORY.md lines that don't correspond to topic files on disk. Auto Dream (24h + 5-session consolidation, currently early-access; this user doesn't have it) will prune orphaned pointers when it fires. Normal-mode Claude may also remove them if it re-shapes the index.

**Historical context:** The user has previously experienced manual MEMORY.md additions being silently removed. The likely mechanism is *next-session Claude rewriting MEMORY.md based on its own model* (or Auto Dream when active), not a concurrent background process. Manual edits that don't conform to the canonical shape (point at a topic file, fit a category, ≤150 chars) are particularly at risk.
