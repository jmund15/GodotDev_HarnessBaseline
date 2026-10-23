---
name: feedback-live-hook-edits-keep-each-state-importable
description: Edit a dispatcher-loaded hook so every intermediate state imports; one denied Edit of a batch can leave a hook every tool call loads broken.
metadata:
  node_type: memory
  type: feedback
---

Edit a hook that a dispatcher loads on every call (`pre_bash_dispatch.py` loads `approving_gates.py`) so each intermediate state imports cleanly: make each Edit stand alone (define a name before the Edit that references it), or write the change in one Write. Then run its proof before the next call.

**Why:** in session febee8f1 four parallel Edits to `approving_gates.py` were sent together; the auto-mode classifier denied three, and the one that landed added a `CONTRACTS` row naming a function the denied Edit was to define. The module failed to import (NameError), so the dispatcher ran without its approvals until the row was taken out the same turn.

**How to apply:** before batching Edits to a hook under `.claude/hooks/` that a dispatcher imports, check each Edit's state alone; if a later Edit depends on an earlier one, send them in order across calls or as one Write. Related: [[feedback-guard-denial-of-justified-work-fix-the-guard]].
