---
name: gotcha_explicit_dim_orphans_on_base_interface_removal
description: "Explicit interface member impl (`IFoo.Member => …`) becomes a CS0540 orphan when IFoo is removed from a type's base list; audit explicit-DIMs before any base-interface swap."
metadata: 
  node_type: memory
  type: project
  originSessionId: 779f9ccc-0d78-4ce7-bd10-c9d954616b18
---

When planning a **base-interface swap** (replacing `: IFoo` with `: IBar` on a class/interface), an **explicit interface member implementation** of the outgoing interface — `ReturnType IFoo.Member => …` — does NOT silently disappear. It becomes a **CS0540** orphan ("containing type does not implement the interface IFoo"), because the explicit qualifier `IFoo.` no longer resolves to a base. The error only appears when the swap compiles, so a plan that says "swap the base, then clean up callers" misses it.

**Why:** explicit DIMs are bound to the named interface, not to the type's general member set. Removing the interface from the base list strips the only thing the `IFoo.` qualifier can attach to. (This is the mirror image of [[gotcha_default_interface_method_for_base_member]] — there, a default-interface-method for a base member needs the *explicit* form to compile; here, the explicit form *breaks* when its interface leaves.)

**How to apply:** before swapping a base interface, `Grep` the type for `IFoo\.` (explicit member impls) and `IFoo.MemberName` DIM defaults. Each hit is a swap-blocker — either (a) keep `IFoo` in the base alongside the new one until its consumers are also retired, or (b) relocate/delete the member with the rest of the `IFoo` surface in the same Part. Evidence (PP collision migration S2): `ISpell` carries `IStatProvider? ICollisionHost.CollisionStatProvider => Blueprint?.Stats` (`ISpell.cs:149`); the planned `: ICollisionHost → : ICollisionResponseHost` swap orphans it — its only consumer is the PP collision runner, so it retires with the PP collision path (Part D). Related: [[feedback_interface_swap_audit_test_contract_pins]], [[gotcha_type_name_equals_namespace_leaf]].
