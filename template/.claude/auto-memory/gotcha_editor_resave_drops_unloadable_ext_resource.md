---
name: editor-resave-drops-unloadable-ext-resource
description: A Godot editor resave can remove failed ext_resource entries and serialized values that refer to them.
metadata:
  type: project
---

When Godot opens a scene while an `ext_resource` target cannot load, saving the scene can remove both
the resource declaration and serialized array or dictionary entries that referred to it.

**Why:** The save may produce no direct warning. The first symptom can be unrelated runtime behavior
caused by the missing authored value.

**How to apply:** Before accepting an editor-dirty scene, compare resource declarations and serialized
collections with the prior committed version. Restore any required reference with the target's current
UID. Treat a clean import as insufficient proof that authored references survived.
