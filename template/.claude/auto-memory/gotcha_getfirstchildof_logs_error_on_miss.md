---
name: gotcha-getfirstchildof-logs-error-on-miss
description: NodeExts GetFirstChildOfType/GetFirstChildOfInterface log JmoLogger.Error on miss → test failure; use the Try-variant for OPTIONAL child lookups.
metadata: 
  node_type: memory
  type: project
  originSessionId: 52f5bfd6-3f00-443c-934b-14869ca19d44
---

`NodeExts.GetFirstChildOfType<T>()` / `GetFirstChildOfInterface<T>()` log a `JmoLogger.Error` when no matching child exists ("Couldn't find a child of type X in node Y"). Since `JmoLogger.Error` fails tests, using the non-Try variant for a lookup whose target is *legitimately optional* turns a benign absence into a red test.

**Rule:** when the looked-up child MAY legitimately be absent (a caster with no `CasterComponent`, a body with no `ICombatant`, etc.), use the no-log **Try-variant** — `TryGetFirstChildOfType<T>(out var x) && x != null` — never the bare `GetFirstChildOfType`. Reserve the non-Try form for children that are a hard wiring contract (their absence IS an error worth logging).

**Verified:** `EnvironmentDepositBehavior`/`MinionSpawnBehavior` resolving a caster's optional `CasterComponent` via `GetFirstChildOfType` failed 3 `RockPillarCraftedPropagationTests` (caster fixture had no `CasterComponent`) with exactly that log; switching to `TryGetFirstChildOfType` fixed it. `RockPillarImpactComponent` already documents the same hazard for `GetFirstChildOfInterface`. Companion: `jmodot_utilities.md` lists the Try-variants as "safe (no throw)" but omits the error-log-on-miss → test-failure consequence. Links: [[feedback_comment_discipline]] (JmoLogger.Error == test failure).
