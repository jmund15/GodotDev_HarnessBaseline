---
name: "Recommended fix" means implement, not defer
description: When the user says "do the recommended fix" they mean ship it now, not defer. Explicit justification required if deferring.
type: feedback
originSessionId: a413d188-90f6-4e6b-97e2-821a3e7e8297
---
When I present options like "(a) implement / (b) defer / (c) skip" and the user says "do the recommended fix" or "do (a)", default to **implementing in-session**, not deferring to a future PR or batch.

**Why:** Said explicitly during the PR #55 ASK/PLAN backlog sweep after I defaulted to "looks complex, probably defer" on items that actually had tractable fixes — e.g., the ExponentialDecayStrategy `exp(-rate*delta)` switch, the DecayProcessor runtime cycle detection, and the Jmodot HasCategory null-guard fix. The user's full phrasing: "for all the rest, do the recommended fix (NOT DEFER, if you want to defer you have to specifically ask and provide justification)."

**How to apply:** If I think something deserves deferral (complex setup, cross-repo workflow, unclear scope), present it as an explicit question BEFORE starting work — not as a unilateral "marking as DEFERRED in the doc." Deferral is a decision the user makes, not one I take.

**Extension (2026-04-24):** Pattern also applies to "DEFERRED:" tier markers in plan/summary output, even when the deferred items are tedious .tscn/.tres scene-wiring tasks. User push-back: "i'm pretty sure you CAN do all of these, right? please don't skip out on tasks within your capabilities."

Litmus test for "can this be done in-session?": if the task is text-authorable (multi-file scene edits, StateTransition .tres bundles, transition wiring with sub_resource Conditions, even when tedious), default to executing. "DEFERRED to editor" requires real justification:
- Visual layout iteration where defaults don't communicate intent (e.g., final wheel slot positioning).
- Cross-cutting architectural decisions that need their own design pass (e.g., UI BB-discovery in multi-player, where the "right" answer depends on undecided ownership questions).

NOT valid justifications:
- "5+ files to edit" — batch the parallel writes.
- "UIDs are tricky" — Godot self-heals UIDs on first editor open (per `MCP_UID_Gotchas`).
- ".tscn syntax is fragile" — read existing examples in the same domain, mirror format.
