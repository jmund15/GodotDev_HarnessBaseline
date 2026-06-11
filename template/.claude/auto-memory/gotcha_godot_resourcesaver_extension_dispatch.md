---
name: gotcha-godot-resourcesaver-extension-dispatch
description: "ResourceSaver.Save keys its serializer off the file extension; temp/scratch/backup paths must keep the original extension as the trailing token (foo.tres → foo.tmp.tres, NOT foo.tres.tmp)."
metadata: 
  node_type: memory
  type: reference
  originSessionId: fd0b005f-63ab-4f79-a253-a2db6c715771
---

`ResourceSaver.Save(resource, path)` returns `Error.FileUnrecognized` when the path extension doesn't match a registered Resource format (`.tres`, `.res`, `.scn`, etc.). Naming a temp file `<path>.tmp` strips that dispatch signal — the last extension becomes `.tmp` and no serializer matches.

**Fix pattern:** insert `.tmp` BEFORE the original extension. `foo.tres` → `foo.tmp.tres`. Implementation: `path.LastIndexOf('.')` + `Substring` insertion. See `Jmodot/Implementation/Persistence/AtomicResourceFile.MakeTempPath`.

**Why:** P3 persistence test #1 (`WriteAtomic_NewPath_CreatesFile`) failed with `FileUnrecognized` on first GREEN attempt. The integration test caught it; pure-logic stubbing couldn't have. Without the assertion on `Error.Ok`, the failure would have shipped silently as "save returns Failed, user has no save data."

**How to apply:** Any time you build a temp/scratch/backup path for a Godot Resource, preserve the original extension as the trailing token. Same hygiene applies to `ConfigFile.Save` (less strict — writes INI-style regardless — but consistency aids debugging).
