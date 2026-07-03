---
name: gotcha-tscn-csharp-node-exports-need-node-paths
description: Hand-authored .tscn C# Node-typed exports silently stay null unless the [node] header carries node_paths=PackedStringArray(...)
metadata:
  type: project
---

Hand-authoring a `.tscn` with C# Node-typed `[Export]` properties (`Runner = NodePath("Runner")`)
is NOT enough — the scene loader only defer-resolves those NodePaths into node references when
the `[node]` header lists them: `[node name="X" type="Node2D" node_paths=PackedStringArray("Runner", "UnitLayer")]`.
Without the marker, the property stays null and `ValidateRequiredExports()` throws
`NodeConfigurationException` at `_Ready` ("must be assigned in the Inspector").

**Why:** the editor writes `node_paths` automatically on save, so editor-authored scenes never
show the failure — only hand-written ones. The error reads like a missing Inspector assignment,
which misdirects to the C# side.

**How to apply:** every hand-written `.tscn` node with C# node-reference exports gets the
`node_paths` list enumerating exactly those property names. See [[gotcha-gdunit4-simulateframes-is-process-frames]].
