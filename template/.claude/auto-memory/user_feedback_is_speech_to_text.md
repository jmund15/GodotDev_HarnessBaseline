---
name: user_feedback_is_speech_to_text
description: "Playtest feedback often arrives via voice transcription — garbled tokens can invert meaning; when a garbled word leaves two plausible readings with different work, surface both readings before executing."
metadata: 
  node_type: memory
  type: user
  originSessionId: a5f38f5c-e6d7-49ac-b411-a62a4960b966
---

Playtest feedback often comes through speech-to-text; transcription artifacts can invert meaning ("boss **remishes** way too big" = "boss **room is**"). When a garbled/odd token leaves two plausible readings that lead to *different work*, read it phonetically, state both readings and the chosen one prominently up front — or ask when the wrong branch is expensive.

**Concrete:** 2026-07-10 — shrank the boss sprite; user meant the boss ROOM; full revert + room rebuild.
