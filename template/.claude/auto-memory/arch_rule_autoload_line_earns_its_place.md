---
name: arch-rule-autoload-line-earns-its-place
description: "A new app-scope singleton defaults to a global.tscn / sub-scene child; it earns its own top-level [autoload] line only when its boot order relative to other autoloads is load-bearing."
metadata: 
  node_type: memory
  type: project
  originSessionId: 88d7b283-4fd6-48bd-9a96-423e2e79a81d
---

A new app-scope singleton (Node-with-static-`.Instance`) defaults to being a **child of a
script-less grouping scene** (`global.tscn`, or a domain aggregator like `persistence.tscn`).
It earns its own top-level `[autoload]` line ONLY when its boot order *relative to other
autoloads* is load-bearing — i.e. some other autoload reads its `.Instance` during that
autoload's `_EnterTree`/`_Ready`.

**Litmus:** grep whether any autoload touches `X.Instance` synchronously in another autoload's
init. None → group it under a container scene (zero ordering hazard, one fewer top-level line).
A consumer that is runtime UI, a lifecycle *scene* loaded after boot, or a quit-time call does
NOT count — those resolve `.Instance` long after every autoload `_Ready` has run.

**Evidence:** the three persistence repositories (`SettingsRepository` / `MetaProgressionRepository`
/ `PlayerProfileRepository`) were three independent top-level lines but no autoload read their
`.Instance` during boot (all consumers are `DebugHudPanel`, `MainMenuController`/`HubScene`, or
`GLM.QuitApp`). Grouping them under a script-less `persistence.tscn` dropped the autoload count
11→9 with no ordering downside. Contrast: `TransitionOrchestrator` MUST stay a top-level line
*declared before* `GameLifecycleManager`, because GLM's `_Ready` subscribes to
`TransitionOrchestrator.Instance` synchronously — that ordering IS load-bearing, and reordering
breaks it silently (no crash). See [[gotcha_autoload_to_autoload_subscription_order]] and
[[gotcha_pp_autoload_dual_registration]] (autoloads live in both `[autoload]` lines AND
`global.tscn` children — check both).

A script-less container scene grouping N independent singletons is NOT the rejected
`SaveSystem`-god-object anti-pattern: that rejection targets a single *class* accreting all
logic, not an organizational `.tscn` with zero script (precedent: `global.tscn` groups 9
singletons this way).
