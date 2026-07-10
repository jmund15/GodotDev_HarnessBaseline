---
name: gotcha_inherited_scene_override_replaces_never_merges
description: "A derived .tscn property override REPLACES the base scene's value wholesale (arrays included, collision layers included). Editing the base template is invisible in every derived scene that touches the same property — verify the derived scenes, not the template."
metadata: 
  node_type: memory
  type: project
  originSessionId: 66931000-c20f-4f54-b483-af3fa3e136e8
---

An inherited-scene property override (`[node name="X" instance=...]` + `prop = ...`) **replaces** the base scene's value — arrays never merge, layer masks never combine. Any template-level edit to a property that derived scenes also set is silently dead in all of them.

**Why:** separation consideration added to `npc_template.tscn` `Steering._considerations` did nothing — all 12 enemy scenes override that array. Same session, same trap again: template body `collision_layer = 128` but every enemy overrides to 64, so an AllySensor mask of 128 sensed nothing.
**How to apply:** before editing a base-scene property, grep the derived scenes for the same property name; if any hit, edit the overrides (all of them) or pin the invariant with a per-scene test. See [[gotcha_inherited_scene_empty_resource_slot]].
**Verified:** live-chain tests RED on scene wiring/masks before the per-scene edits, GREEN after (SeparationLiveChainTests, 2026-07-07).
