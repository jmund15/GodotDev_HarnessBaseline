---
name: gotcha-config-exception-node-bound-not-static
description: "Jmodot's NodeConfigurationException/ResourceConfigurationException require a Node/Resource arg and their base is abstract — pure/static code can't throw them; throw a CLR exception and rewrap at the Node boundary."
metadata: 
  node_type: memory
  type: project
  originSessionId: 88d7b283-4fd6-48bd-9a96-423e2e79a81d
---

Jmodot's config exceptions are **object-bound**: `NodeConfigurationException` /
`ResourceConfigurationException` have only `(msg, Node/Resource)` ctors, and their base
`GodotConfigurationException` is `abstract` — so a pure/static method **cannot construct them**
(no `this` to pass; you can't build a Node without the engine running). To fail-fast from a
CLR-pure validation layer, throw a plain `InvalidOperationException` in the pure method and
**rewrap it at the Node/Resource boundary** (`catch (InvalidOperationException ex) → throw new
NodeConfigurationException(ex.Message, this)`). Keeps the pure layer CLR-testable (no
`[RequireGodotRuntime]`) while preserving the framed `[name]` boot error.

**Verified:** read `NodeConfigurationException.cs` (only `(string,Node)` / `(string,Node,Exception)`
ctors) + `GodotConfigurationException.cs` (`abstract` base).
Concrete: GLM `ValidateScenePaths` (pure) / `GuardScenePathsConfigured` (rewraps). See
[[arch_rule_autoload_line_earns_its_place]].
