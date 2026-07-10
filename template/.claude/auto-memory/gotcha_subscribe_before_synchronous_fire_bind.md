---
name: gotcha_subscribe_before_synchronous_fire_bind
description: "Observer wiring to a source that fires synchronously during its Bind/setup must subscribe BEFORE the Bind call, or the first fire is lost."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: dbb6bdf3-ae9a-4faa-91fb-7130587af263
---

When wiring an observer to a source that can emit synchronously *inside its own* `Bind`/setup call, attach the subscription BEFORE calling Bind. A subscribe-after-bind order silently drops the first (often only) fire — the source emits during Bind while no subscriber is attached yet.

**Why:** a fire-on-bind source raises its event during `Bind`; `source.Bind(x); source.Event += handler;` registers the handler too late.

**How to apply:** flip seam code to subscribe-first. Suspect this when a trigger "never activates" but the wiring looks present. Distinct from [[gotcha_autoload_to_autoload_subscription_order]] (cross-autoload declaration order, not intra-method ordering).

**Verified:** P6 — `TideDirector.BindDefinition` called `runScope.Bind(graph)` then `Trigger.Triggered += ActivateTide`; integration test `RosterInjected_TideActivatesOnRunStart` failed (tide never activated) until the two lines were swapped — single-change red→green isolation. P1's seam had only been exercised by a manually-fired test trigger, so the order was never tested before.
