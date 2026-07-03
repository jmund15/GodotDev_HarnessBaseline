---
name: feedback-design-workflow-model-mix
description: Design-exploration workflows use Opus (or main-loop) generators with Sonnet red-team — never Sonnet generators
metadata:
  type: feedback
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
