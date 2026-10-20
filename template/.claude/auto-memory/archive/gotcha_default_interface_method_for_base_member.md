---
name: gotcha_default_interface_method_for_base_member
description: "Providing a default impl in a derived interface for a BASE interface's member requires EXPLICIT form (Type IBase.Member()); implicit form creates a new method and leaves implementers unsatisfied (CS0535)."
metadata: 
  node_type: memory
  type: reference
---

When interface `IDerived : IBase` provides a default implementation for a member declared on `IBase`, it **must** use the explicit form:

```csharp
// IBase: Identity GetIdentity();
public interface IDerived : IBase {
    Identity IBase.GetIdentity() => /* default */;   // ✓ satisfies IBase for all IDerived implementers
}
```

Writing it implicitly — `Identity GetIdentity() => …` — compiles, but creates a **new** `IDerived.GetIdentity()` member that does NOT serve as the implementation of `IBase.GetIdentity()`. Every concrete class implementing `IDerived` then fails with **CS0535 "does not implement interface member IBase.GetIdentity()"** even though the default "looks" present.

**Trade-off it forces:** the explicit default is **interface-access only** — callable through `IDerived`/`IBase`-typed references, NOT on a concrete-typed variable (`concrete.GetIdentity()` → CS1061). Before hoisting a member up to an interface default and deleting per-class impls, verify no concrete-type call sites exist (the compile is the authoritative check; a surviving concrete call fails CS1061). If one exists, keep a one-line per-class body delegate.

Surfaced hoisting `IIdentifiable.GetIdentity()` onto a content interface — a project that uses default interface methods heavily hits this repeatedly.

**Second failure mode — DIM diamond (CS8705).** Adding a DIM for a base-`B` member to interface `A` causes **CS8705 "no most specific implementation"** for any concrete type that *also* implements an unrelated interface `C` providing its OWN DIM for the same `B` member (`A` and `C` are incomparable, so neither wins) — the dual-implementer must supply a concrete override to resolve it. Before adding a DIM to an interface, check whether any implementer also implements a sibling interface that DIMs the same member. **Concrete:** 2026-06-03 — adding collision-response DIMs to a content interface collided with Jmodot `ICollisionHost`'s kinematic DIMs on two dual-implementers; both needed concrete overrides. See [[feedback_symmetric_guards_across_siblings]] for the parallel-implementer pattern.
