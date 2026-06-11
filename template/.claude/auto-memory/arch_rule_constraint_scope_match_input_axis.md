---
name: Gameplay geometry must match input dimensionality
description: 1D input → 1D snap; N+1 dimensional resolution breaks the player's mental model of what they control.
type: feedback
originSessionId: 15bc6648-e4d1-4a64-b970-d32e8c122873
---
Gameplay geometry must match the player's input dimensionality. **1D input (e.g., angle on a fixed circle) → 1D angular sweep snap**, not a 2D area search. Expanding the resolution geometry to N+1 dimensions breaks the player's mental model of what they control — the resolved result is sensitive to a dimension the player has no input control over, which feels like randomness or bugs.

**How to apply:** When designing snap / targeting / resolution logic, ask *"what dimensions does the player's input span?"* and constrain the resolution geometry to exactly that span. If the player controls θ on a fixed-radius circle, the snap operates along that circle — not radially outward.

**Concrete:** Rock Pillar cast targeting — player inputs cast angle only; the snap sweeps CW/CCW along the fixed `cast_radius` circle, not outward toward `max_cast_radius`. Expanding to a 2D search would mean the resolved pillar position depends on a radius the player didn't choose, producing surprise placements that read as buggy.

**Migrated from MCP** (was `Constraint_Scope_Match_Input_Axis`, entityType `DesignPrinciple`) 2026-05-11.
