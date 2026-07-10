---
name: gotcha-pure-clr-poco-mirror-payload-type
description: A pure-CLR published-state POCO stays pure-CLR only if its payload is primitive; a Godot-typed payload (Resource/StringName/PackedScene) reintroduces the #24 test-host SIGSEGV.
metadata:
  node_type: memory
  type: project
  originSessionId: 83b89f38-76ed-4be6-84f9-095f353c42de
---

A published-state POCO that is pure-CLR-testable with a *primitive* payload (e.g. `float`) is NOT pure-CLR once its payload is a Godot type (`Resource` / `StringName` / `PackedScene`): constructing that payload in a pure-Logic test (no `[RequireGodotRuntime]`) SIGSEGVs the test host — the #24 native-allocation crash. The mirror breaks at the **payload-type seam**, not the keying logic.

**How to apply:** keep the keying contract payload-agnostic — key on an opaque `object` token and never dereference the payload — so the Logic test exercises add/replace/clear with a `null`/stub payload and stays pure-CLR; pin the real non-null payload path in a `[RequireGodotRuntime]` test ([[feedback_pin_coverage_axes_separately]]). A `StringName` id-token does NOT dodge this (StringName construction is itself the native allocation) — use `string`/`int` for a pure-CLR token.

**Concrete:** `RunSpawnState` (EnemyIdentity payload) mirrored `RunLightingState` (float payload); `/plan_check` + `/session_audit` both flagged the "pure-CLR" label as #24-unsafe → resolved via null-payload keying tests, 2026-06-25.

**Verified:** the #24 crash chain (`.claude/rules/jmodot_utilities.md` JmoRng "Runtime requirement") traces SIGSEGV through `Godot.RandomNumberGenerator..ctor` → `Godot.StringName..cctor`, confirming StringName/Resource construction is the native allocation; `RunSpawnStateTests` run pure-CLR using `new SpawnRequest(null, N)`.
