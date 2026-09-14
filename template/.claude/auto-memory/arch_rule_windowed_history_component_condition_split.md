---
name: windowed-history-component-condition-split
description: Windowed predicates use a stateful runtime component and a stateless authored condition.
metadata:
  type: feedback
---

When a transition predicate needs recent events, accumulated input, or a sliding window, split it into
two objects:

1. A per-consumer runtime component owns and publishes the raw history.
2. A stateless authored condition reads that history and applies its configured predicate.

**Why:** A Resource condition may be shared across consumers. Mutable history on it leaks state or is
lost when a clone is replaced. The runtime owner has the right lifetime, while the condition remains
reusable data.

**How to apply:** Put timestamps, counters, trails, and subscriptions on the component. Resolve it
through the established context or blackboard seam. If the condition needs to remember a prior call,
move that state to the runtime owner before implementing the predicate.
