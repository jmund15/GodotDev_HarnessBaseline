---
name: Shared Resource holds zero per-consumer state — config-Resource + runtime-object split
description: A Godot Resource shared by reference across multiple consumers must hold only immutable config; per-consumer mutable state lives on a separate runtime object the consumer owns, minted by a factory method.
type: feedback
originSessionId: b50c3838-a19f-4ba0-ad05-0670b2b57267
---
A `[GlobalClass] Resource` referenced by `.tres` from more than one consumer is a **single shared instance** — Godot loads one object and hands the same reference to every `[ext_resource]`/`[Export]` that points at it. So **any mutable field on that Resource is shared mutable state across all consumers.** One consumer's write (or teardown reset) silently corrupts every other consumer's view.

**The split:** the Resource holds only immutable designer config + pure static helpers. All per-consumer mutable state lives on a separate **runtime object the consumer owns**, minted by a factory method on the Resource (`CreateRuntime(consumer, deps)` / `CreateState()`). The consumer stores the runtime, drives it per-frame, and disposes it on teardown. The Resource never learns the consumer exists.

**Why this beats `resource_local_to_scene = true`:** the flag forces a deep per-scene *copy* — but it must be toggled on for **every** Resource that needs it (and every sub-resource it references), it's invisible at the call site, and a missing flag fails silently far from the cause. The config/runtime split makes statelessness structural and unforgeable: there's no mutable field to stomp, so the bug class can't recur even if someone forgets a flag. **(User preference: treat Resources as stateless; `resource_local_to_scene` is a rejected shortcut.)**

**Concrete (2026-06-04):** `IngredientPickupStrategy` (a `.tres` shared by the hub wizard AND the run-scope wizard — a fresh wizard spawns per scene) stashed `_collector`/`_inventory`/`_pullState` on itself. The hub wizard's `_ExitTree` → `Detach()` nulled the run wizard's collection, so **arena ingredient collection silently no-op'd while the hub worked.** Symptom was maddening: "works in one scene, dead in another." Fix: strategy became stateless config; `CreateRuntime(collector, inventory)` mints a per-collector `PickupStrategyRuntime`. Pinned by `PickupStrategyStatelessnessTest` (one shared strategy must hand DISTINCT runtimes to distinct collectors).

**Sibling instance:** `arch_rule_transition_condition_stateless.md` is the same rule for `TransitionCondition` Resources (shared across actors/states; no latch fields). Same Factory→Runner principle as the broader Resource state-cache ban. When designing ANY new Resource subclass, ask *"will two consumers ever reference the same .tres?"* — if yes (or unknown), no mutable instance fields; split config from runtime.
