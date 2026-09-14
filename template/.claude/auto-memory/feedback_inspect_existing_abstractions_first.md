---
name: Inspect existing abstractions before designing new ones
description: Before adding a type or configuration surface, inspect the existing family and its collaborators.
type: feedback
---

Before adding a class, strategy, interface, Resource, export, parameter, or behavior flag, inspect the
existing family that owns the concern.

**How to apply:**
- Search for the base class, interfaces, strategy/config slots, and domain terms.
- If a family has two or more siblings, read the base and at least two siblings before proposing a new
  parallel type.
- Read the existing API itself. Do not infer its methods or lifecycle from names or callers.
- Check collection fields on related Resources. An existing `Array<X>` or `Dictionary<X,Y>` may
  already be the authored home.
- Inspect raw engine-primitive collaborators such as `Curve`, `Gradient`, `Shape3D`, and
  `AnimationLibrary`; the project may already wrap them in a domain Resource.
- Read consumers when a change alters intent, output, or lifecycle contracts.

**Why:** A parallel abstraction creates a second home for one concern. A small extension to the live
family usually preserves its existing wiring, tests, and designer surface.

**Plan gate:** Every proposed configuration surface answers: “Where does this fit in the existing
abstraction tree?”
