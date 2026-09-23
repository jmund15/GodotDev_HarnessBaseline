---
name: feedback-dispatch-transport-is-session-bound
description: "Workflow/Agent subagents are transport-bound to the session endpoint: pin THAT transport's own model ids, siblings on it need no sidecar, and a cross-transport dispatch (Anthropic included) goes through a sidecar launcher"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: b729ef67-4b61-4c62-b99a-cdf587856c42
  modified: 2026-08-13T03:39:18.394Z
retire_when:
  - review-by: 2027-02-08
---

Workflow/Agent subagents run on the session's own endpoint and nothing else. What changes with the
endpoint is the PIN VOCABULARY, and since 2026-09-04 nothing translates between vocabularies.

**Pin the session transport's own model ids.** On an Anthropic session that means role names
(`opus`/`sonnet`/`haiku`/`fable`) or `claude-*` ids; on a codex session `gpt-5.6-*`; on deepseek
`deepseek-v4-*`. `hooks/workflow_provider_guard.py` DENIES the other vocabulary before the call
runs and names the legal set — it does not warn, because the failure it prevents is silent: a pin
the endpoint cannot serve returns a null agent that `.filter(Boolean)` swallows into "0 findings",
a fan-out that looks clean and ran nothing.

**Siblings on your OWN transport need no sidecar.** A codex session pins `gpt-5.6-terra` in a
Workflow and it dispatches in-harness; the guard injects `args.__transport` so `dispatch.js` and
`review_fanout.js` accept those ids. A sidecar is for CROSSING transports — including reaching
Anthropic from a provider session, which is what `.claude/scripts/anthropic_sidecar.sh` is for.

**The committed `.claude/workflows/*.js` scripts pin Anthropic role names**, so they are
Anthropic-session tools by construction; running one from a provider session is denied rather than
silently mis-routed.

**Why:** 2026-08-07 — a Sonnet-session fan-out meant for deepseek ran all four survey agents on
claude-sonnet-5 (~494k tokens of plan quota) because CLAUDE.md's "pins translate MECHANICALLY"
sentence lacked the endpoint qualifier. Translation was the fix then and the liability later: it
made a role name mean different things on different endpoints, which is why the rule is now one
vocabulary per transport and a deny for the rest.

History (retired 2026-09-04): `model_pin_translate.py` used to rewrite role pins to vendor ids on
deepseek sessions and STRIP the Agent tool's pin, because bare role names hard-error on that
endpoint. Both behaviours are gone with the hook.

**Verified:** 2026-09-04 memory-claim audit — `model_pin_translate.py` retired in f56fdc4ca; `hooks/workflow_provider_guard.py` returns `permissionDecision: deny` (3 sites); `workflows/dispatch.js:184` `.filter(Boolean)` swallows a null agent.
