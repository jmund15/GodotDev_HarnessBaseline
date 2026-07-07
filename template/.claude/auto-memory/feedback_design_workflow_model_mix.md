---
name: feedback-design-workflow-model-mix
description: Design-exploration workflows use Opus (or main-loop) generators with Sonnet red-team — never Sonnet generators
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 8d59ce77-9de6-4405-9305-e5e56b10bc71
---

For idea/design-exploration Workflows, the user wants **Opus-class generators** (creative
concept agents) with **Sonnet red-teamers** (adversarial verdict agents).

**Why:** Generation quality bounds the whole exploration — a weak generator wastes the
red-team pass; critique is cheaper to do well than invention. User corrected a
sonnet-generator run mid-flight ("workflow should be fable generators, sonnet red team…
actually opus generators i think is best", 2026-06-11).

**How to apply:** In Workflow scripts, set `model: 'opus'` on generator/lens `agent()`
calls and `model: 'sonnet'` on red-team/verdict calls (or omit generator model to
inherit the main loop when it is Opus-class or better).

**Review/audit fan-outs — never Fable agents.** Multi-agent review/audit/red-team
dispatches always carry explicit per-template model overrides (opus reviewers, sonnet
verifiers per the agent templates); agents must not inherit a Fable session model.
`review_fanout.js` floors missing overrides to sonnet, but the caller still passes the
template's intended models. ("don't use fable for audit agents though, delegate
properly", 2026-07-06 session audit.)
