---
name: Verify Explore agent empirical claims via prior-art grep
description: When an Explore subagent makes an empirical claim about codebase/runtime capabilities (e.g., "Godot can't serialize X", "no static seam exists for Y"), verify with a 1-grep prior-art check before letting it shape downstream decisions
type: feedback
originSessionId: e9092f23-933b-4528-b9e3-ec8a6b452b62
---
When an Explore subagent reports an empirical claim about codebase or runtime capabilities — "Godot can't do X", "class Y has no attribute Z", "no static seam exists for W" — verify the claim with a 1-grep prior-art check before letting it shape downstream decisions.

**Why:** Explore agents reason plausibly but their evidence can be incomplete. In one planning session an agent claimed Godot cannot serialize `Dictionary<Resource, X>` keys in .tres files (citing serializer constraints); a single `Grep("Dictionary\[(SubResource|ExtResource)<", glob="*.tres")` returned 5 prior-art files proving the claim wrong. Acting on the false claim would have inverted a plan's high-tier finding, sending the reconciliation toward a sidecar-array workaround instead of the actually-correct spec implementation.

**How to apply:** Before letting an Explore-agent empirical claim shape a decision (recommend / defer / invent workaround), spend one search verifying. Cheap; catches confident-but-wrong agent reasoning. Especially urgent when the agent's claim *contradicts* a spec commitment — the spec author probably knew something the agent's heuristic missed. Litmus: "could one grep falsify this claim?" If yes, run it before acting.

**Concrete:** 2026-05-17 plan-mode session, audit H4 (Identity-keyed Dictionary migration), Explore-agent claim "Godot serializes only primitives/StringName/NodePath as Dict keys" vs verified prior art at `Wizard/wizard_base_chararch.tres:19,29`, `Wizard/walk_stat_context.tres:35`, `Ingredients/WindShroom/wind_shroom_data.tres:37`, `Environment/Wall/iron_wall_statsheet.tres:11`. User caught: "i'm like 90% sure if you use a godot dictionary you can export anything that inherits from resource."
