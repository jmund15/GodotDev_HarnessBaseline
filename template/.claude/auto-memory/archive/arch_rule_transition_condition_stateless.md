---
name: transition-condition-resources-must-be-stateless
description: Resource-backed transition conditions keep no mutable evaluation state; runtime owners provide state through context.
metadata:
  type: feedback
---

`TransitionCondition` Resources must hold no mutable per-evaluation state. `Check(agent, bb)` is a
pure function of its arguments and immutable authored configuration.

**Why:** Godot can share a loaded Resource, including inline sub-resources, across consumers. A latch,
counter, cached subscription, or timestamp on the condition can leak between actors. Cloning a
behavior condition does not solve lifetime bugs when tree reinitialization replaces the clone.

**How to apply:**
- Put mutable state on the object that owns its lifetime, such as an entity component or blackboard.
- Query event history for recent-event checks instead of latching the result on the condition.
- Treat any condition that needs to remember a prior call as a misplaced state owner.
