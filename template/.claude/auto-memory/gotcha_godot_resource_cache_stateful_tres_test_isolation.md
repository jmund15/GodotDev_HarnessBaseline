---
name: gotcha_godot_resource_cache_stateful_tres_test_isolation
description: GD.Load caches .tres by path; a resource with stateful inline sub-resources reused across tests carries stale state — load CacheMode.Ignore for a fresh instance.
metadata: 
  node_type: memory
  type: feedback
  originSessionId: dbb6bdf3-ae9a-4faa-91fb-7130587af263
---

`GD.Load` / `ResourceLoader.Load` cache resources by path, so a `.tres` whose *inline sub-resources hold per-binding/per-run state* (e.g. a one-shot latch) hands every test the SAME stale instance. For per-test isolation, load via `ResourceLoader.Load(path, "", ResourceLoader.CacheMode.Ignore)` — re-parses the file fresh (fresh inline sub-resources) while ext-resource refs stay cache-shared.

**How to apply:** when a content `.tres` carries a latch/accumulator in an inline sub-resource and ≥2 tests load the same path, load with `CacheMode.Ignore`. (The `.tres` itself stays shared-stateless config per [[arch_rule_resource_config_runtime_split]]; the hazard is specifically a stateful *inline sub-resource* + the path cache.)

**Concrete:** P6 tide tests — `OnRunStartTrigger._bound` one-shot latch; `eclipse_buddha_tide.tres` loaded by two activation tests. The caching is documented Godot behavior; `CacheMode.Ignore` was applied prophylactically (not isolated via a red test).
