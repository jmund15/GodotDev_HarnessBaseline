---
description: Apply queued injected-file harness edits from scratch/pending_harness_edits.md — run as the FIRST act of a fresh session.
---

Applies edits that a prior session deferred because their target files are prompt-prefix-resident (CLAUDE.md, MEMORY.md — editing them mid-session invalidates the prompt cache at full-context cost; in a fresh session's first turn the same edit lands in the cold prefix for ~free).

## Procedure

1. **Read the queue:** `.claude/scratch/pending_harness_edits.md`. If absent or empty below the header → report `No pending harness edits.` and stop.
2. **Timing check:** if this session already has substantial context (not within the first few turns), warn that applying now pays a proportionally larger cache re-write and ask whether to proceed or leave queued. First-turn invocation proceeds without asking.
3. **Apply each entry** with direct `Edit` against the target file, exactly as specified (entries carry verbatim insertion text and an anchor). Injected-file edits route direct — never `write_doc` (CLAUDE.md §9 write routing).
4. **Conflict policy = skip-and-audit-trail** (mirrors `/worklog` cloud replay): if an entry's anchor no longer matches (target drifted since queueing), strike the entry through with `(skipped: anchor drifted)` and continue — never hard-fail the batch.
5. **Clean up:** all entries applied → delete the queue file. Any struck entries remain → keep the file with only struck lines and note them.
6. **Commit** the applied edits + queue deletion as one `docs(harness)` commit (meta — regression-gate exempt). Baseline classification is **opt-in**: mention `/sync_baseline` only when invoked with `--check-baseline`, matching `/commit_push` and `/clean_push`.

## Queue-entry contract (for sessions WRITING the queue)

Each entry must be applyable context-free: target file, a durable anchor (named paragraph/heading — never a line number or commit hash), verbatim content to insert/replace, and a one-line why.

**Surface taxonomy (canonical home — CLAUDE.md §9 references this):** queue ONLY prefix-resident surfaces, whose mid-session edit re-writes the prompt cache:
- Natively-injected files: CLAUDE.md (project + user-global), auto-memory `MEMORY.md`.
- Unverified, treat as prefix-resident until tested: skill/command FRONTMATTER and agent definitions (they feed system-prompt listings; listing-refresh behavior unmeasured).

Everything loaded on demand is cache-free — edit directly, never queue: skill/command bodies, `rules/*.md`, `hooks/*.py`, auto-memory topic files, `worklog-titles.md`, and hook-STDOUT-injected content (hook output is a frozen transcript event, not refreshed on file change).
