---
name: feedback-dispatch-transport-is-session-bound
description: "Workflow/Agent subagents are transport-bound to the session endpoint: pin THAT transport's own model ids, siblings on it need no sidecar, and a cross-transport dispatch (Anthropic included) goes through a sidecar launcher"
metadata: 
  node_type: memory
  type: feedback
retire_when:
  - review-by: 2027-02-08
---

Workflow/Agent subagents run on the session's own endpoint and nothing else. What changes with the
endpoint is the PIN VOCABULARY, and since 2026-09-04 nothing translates between vocabularies.

**Pin the session transport's own model IDs.** Use `model_registry.py for-role <tier>` to read
the current roster, then pass that endpoint's legal pin. `hooks/workflow_provider_guard.py` DENIES
another transport's vocabulary before the call runs: an unsupported pin can otherwise return a
null agent that a workflow mistakes for zero findings.

**Siblings on the current transport need no sidecar while the session's plan pays.** Transport-aware
Workflow engines accept that endpoint's IDs from their arguments. A separate sidecar process reaches
another transport or another plan of the same transport; it can also isolate process-level settings
on the current one.

**A committed script is not a transport.** A reusable workflow can accept runtime pins, while a
script with literal provider pins is limited to that endpoint. Rule: `orchestration` §5 *Effort*.

**Why:** 2026-08-07 — a Sonnet-session fan-out meant for deepseek ran all four survey agents on
claude-sonnet-5 (~494k tokens of plan quota) because CLAUDE.md's "pins translate MECHANICALLY"
sentence lacked the endpoint qualifier. Translation was the fix then and the liability later: it
made a role name mean different things on different endpoints, which is why the rule is now one
vocabulary per transport and a deny for the rest.

History (retired 2026-09-04): `model_pin_translate.py` used to rewrite role pins to vendor ids on
deepseek sessions and STRIP the Agent tool's pin, because bare role names hard-error on that
endpoint. Both behaviours are gone with the hook.

**Verified:** 2026-09-04 memory-claim audit — `model_pin_translate.py` retired in f56fdc4ca; `hooks/workflow_provider_guard.py` returns `permissionDecision: deny` (3 sites); `workflows/dispatch.js:184` `.filter(Boolean)` swallows a null agent.
