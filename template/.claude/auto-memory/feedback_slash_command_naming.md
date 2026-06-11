---
name: Slash command naming — propose convention-aligned names first
description: When creating new slash commands, scan existing .claude/commands/ for naming prefixes (doc_, session_, doc_*_audit, etc.) and propose a name that extends an existing family before inventing a new namespace.
type: feedback
originSessionId: f28dcdb1-572c-4206-ba21-c4404c11a475
---
When creating or renaming slash commands, default to the **existing naming-family prefix** (e.g., `doc_` for doc-tooling commands, `session_` for session-lifecycle commands, `*_audit` for auditors) rather than inventing a new namespace.

**Why:** The repo has a coherent naming convention — e.g., 8+ commands use the `doc_` prefix (`doc_architecture`, `doc_architecture_audit`, `doc_audit_fix`, `doc_full`, `doc_npc`, `doc_retrospective`, `doc_start_here_update`, `doc_usage`, now `doc_reality_audit`). Convention-aligned names cluster together in the slash picker, signal family membership, and reduce cognitive load when the user is scanning commands.

**Confirmed:** 2026-04-20 — I initially proposed `reality_audit` (no prefix), user asked for `obsidian_reality_audit`, I flagged the convention mismatch and suggested `doc_reality_audit` as the convention-aligned alternative. User picked the convention-aligned option. That validates: *surface the convention observation BEFORE renaming, so the user can choose knowingly rather than being rushed into an inconsistent name.*

**How to apply:**
- Before proposing a name for a new command/skill/file, scan `.claude/commands/` (or the equivalent directory) for existing naming patterns
- If the new thing fits an existing family, propose `<family_prefix>_<name>` as the primary recommendation
- If the user proposes a name that breaks the convention, point out the observation (briefly, not pedantically) and offer the convention-aligned alternative before executing
- If the user insists on their preference, execute it — but at least the choice was informed
