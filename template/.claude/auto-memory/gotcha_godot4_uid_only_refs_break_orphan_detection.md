---
name: godot4-uid-only-refs-break-orphan-detection
description: Godot UID-only references make path-only orphan detection incomplete.
metadata:
  type: reference
---

Text search by path or basename cannot prove that a Godot Resource is orphaned.

`ext_resource` entries may use only a UID, and a Resource's own UID may be absent from its text header.
Dynamic loads and directory-backed registries add references that static path scans cannot see.

**How to apply:** Treat text-found orphans as candidates, never deletion proof. Resolve the Resource's
UID through Godot's cache, search both path and UID references, inspect dynamic loaders, and verify in
the editor before removal. A large orphan set is a prompt to check detector coverage first.
