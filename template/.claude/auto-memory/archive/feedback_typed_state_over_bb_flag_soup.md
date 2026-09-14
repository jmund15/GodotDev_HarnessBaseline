---
name: Typed component property over BB-bool flag soup
description: Prefer an owning component property for one-producer state; use a raw blackboard fact only when no single owner exists.
type: feedback
---

For cross-system polled state, prefer a typed component property plus a typed condition over adding one blackboard bool per concern.

**Decision:**
- One authoritative producer, many readers: expose a privately settable component property. Store the component reference on the blackboard. Let a typed condition select the property.
- Event-driven interrupt: subscribe on state entry, unsubscribe on exit, and transition from the handler.
- Multiple independent producers and consumers: use a raw blackboard fact. No component has truthful sole write ownership.
- One-shot external trigger: a raw flag is valid only with explicit consume-and-clear semantics.

Do not pattern-match a compound state's current leaf from unrelated conditions. That leaks state-machine structure and breaks on refactors.

**Litmus:** Can one class truthfully own every write? Yes means component property. No means a shared blackboard fact may be the better boundary.
