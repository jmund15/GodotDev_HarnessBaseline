---
name: User distress lexicon — STOP signal
description: ALL CAPS / 'WHAT' / 'ridiculous' / stacked '?????' tokens → STOP making changes; acknowledge + ask; do NOT propose a fix in the same turn.
type: feedback
originSessionId: 15bc6648-e4d1-4a64-b970-d32e8c122873
---
When the user uses ALL CAPS, 'WHAT', 'ridiculous', or stacked '?????' — **STOP making changes.** Do NOT propose a fix in the same turn. Acknowledge concretely (no performative agreement), then ASK what state they want to recover to, then take ONE step. Cascading speculative-action patterns compound with every additional change; pausing one turn earlier averts the compounded mess.

**Why:** Distress lexicon signals the user has lost trust in the current trajectory. Continuing to act — even with "correct" tools — extends the damage. The recovery cost of pausing one turn is trivial; the recovery cost of one more wrong action while they're already frustrated is significant (lost work, deeper confusion about what state is current, eroded session trust).

**How to apply:**
- Parse the latest user message for ANY of: ALL-CAPS phrases, the bare token "WHAT", "ridiculous", "????" (≥2 question marks together).
- If present, the response MUST be acknowledgment + question, not action.
- Acknowledgment ≠ performative agreement. Don't say "you're absolutely right!" — state what happened concretely ("I deleted X without checking Y").
- Ask: "What state do you want to recover to?" or "Do you want to revert X, redo Y, or try Z?" — give them control of the next step.
- Then take ONE step max. Re-check for distress lexicon before any further action.

**Concrete (2026-05-11, Wind Blast):** User typed `"WHAT ARE YOU DOING. YOU ACTIVELY MADE IT WORSE"` followed by `"you LOST progress for ZERO reason?????"` — both followed cascading speculative changes (sequential `git checkout` and edits while trying to "fix" a perceived problem). Pausing at the first ALL-CAPS message would have avoided the `git checkout` overcorrection that compounded the loss.

**Migrated from MCP** (was `User_Distress_Lexicon_Stop_Signal`, entityType `communication_rule`) 2026-05-11 — moved to auto-memory so the rule loads at SessionStart, before any cascade can start.
