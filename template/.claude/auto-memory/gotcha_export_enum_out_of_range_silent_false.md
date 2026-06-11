---
name: gotcha-export-enum-out-of-range-silent-false
description: "Godot [Export] enum field with .tres value outside the current enum's range silently casts to int and compares-as-int — never matches any canonical enum member."
metadata: 
  node_type: memory
  type: gotcha
  originSessionId: e308a485-9d76-4b60-8b37-c85c880e7d97
---

A `.tres` file storing a numeric value for a `[Export] MyEnum field` loads even when the value is outside the enum's declared range. Godot stores the raw int; equality comparisons against canonical enum members all silently return false, so any `Check()`/`if` keyed on the enum never fires.

**Why:** Godot's .tres loader does not validate enum-range at parse time (unlike, e.g., C# strict enum constraints). The build is green, the scene loads, the script runs — only runtime *behavior* deviates, and only via "this transition never fires" which can take weeks to notice.

**How to apply:** When renaming/collapsing an enum, audit every `.tres` that exports it. After collapse, any value ≥ new enum.Count is stale. Combined with the [[godot_files]] *Value-type `= null` is always a bug* rule: value-type Exports are a recurring footgun class — null OR out-of-range both fail silently.

**Concrete:** `collection_to_battle.tres` stored `GamePhaseTrigger=3` after an enum collapsed from 4→3 values; `Check()` returned false forever because `RunPhase.GameOver=2`. Caught at /plan_part time during the GameManager→RunController demotion (P2, 2026-05-19).
