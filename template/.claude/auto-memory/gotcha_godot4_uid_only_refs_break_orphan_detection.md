---
name: godot4-uid-only-refs-break-orphan-detection
description: "Text-based orphan-resource detection is unreliable in Godot 4 — ext_resource can reference by uid only (no path), and a resource's uid isn't always in its header."
metadata: 
  node_type: memory
  type: reference
  originSessionId: 42fe26cb-2d31-4490-a3a7-9c29895490e4
---

Text-based orphan-resource detection (grep on file path / basename) is **unreliable** in Godot 4. Two reasons a heavily-referenced resource reads as a false orphan:

1. `ext_resource` entries can reference a resource by **uid only** (`uid="uid://x"` with no `path=` string) — a path/basename grep then finds zero inbound refs.
2. A resource's own uid is **not always in its header line** (older / stripped `.tres`), so you can't reliably derive "this file's uid" to count inbound uid refs either.

Add dynamic loads (registry directory-scans, `GD.Load` with constructed paths, autoload `*res://` entries) and the false-positive rate is high.

**Concrete:** 2026-05-30 — a set-based path+header-uid detector flagged ~140 orphan candidates; `Global/Attributes/AI/reaction_time.tres` was referenced by **108** files via uid-only `ext_resource` refs yet flagged orphan. Orphans are ASK-tier — never auto-delete. Verify in the Godot editor (authoritative `uid_cache.bin` resolution) before removing. Detection cue: an orphan sweep returning >50 candidates is almost certainly over-counting — suspect uid-only refs.
