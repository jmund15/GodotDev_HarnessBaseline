---
paths:
  - "Jmodot/**/*.cs"
---

# Jmodot Framework Authoring Rules

**Context:** Rules that fire only when editing files *inside* the Jmodot submodule — framework-internal authoring concerns that don't apply to {{PROJECT_NAME}} consumer code. For consumer-facing utility reference (NodeExts, JmoRng, etc.), see [`jmodot_utilities.md`](jmodot_utilities.md). Auto-loads on `Jmodot/**/*.cs` reads.

## 2D/3D Parity Convention

**Rule:** Every spatial concept in Jmodot exists as both `Type3D` and `Type2D`. When adding a new contract or component to Jmodot's combat / physics / identification surface, **mirror it to 2D in the same PR**.

- *Litmus:* Adding `FooComponent3D`? Add `FooComponent2D` alongside.
- *Why:* Splitting leaves 2D consumers behind a known asymmetry. Even if the immediate consuming codebase is 3D-only ({{PROJECT_NAME}} is), future 2D Jmodot users will hit an asymmetric API.
- *Shape:* Two interfaces (no shared base) preferred over a generic `IFoo<T>` — Godot's editor type system doesn't surface generic interfaces well.

**Existing parity pairs** (non-exhaustive — mirror this list when adding):
- `HitboxComponent3D` / `HitboxComponent2D`
- `HurtboxComponent3D` / `HurtboxComponent2D`
- `HitContext3D` / `HitContext2D`
- `IVelocityProvider3D` / `IVelocityProvider2D`
- `KnockbackComponent3D` / `KnockbackComponent2D`
- `IPayloadInterceptor3D` / `IPayloadInterceptor2D`

## Framework Boundary

**Rule:** Jmodot code MUST NOT reference `{{PROJECT_NAME}}.*` namespaces. The framework is a submodule consumed by PP, not the reverse.

- *Why:* Jmodot is designed to be reusable across multiple games. Inbound coupling to a specific consumer permanently brands it.
- *Pattern for project defaults:* Introduce a framework-agnostic static seam in `Jmodot.Core.*` with nullable static fields; the consuming game's autoload forwards values into it at `_EnterTree`.
- *Canonical example:* `Jmodot.Core.Combat.CombatFactoryDefaults` — six combat factories resolve project defaults via static fields wired from PP autoload.
- *Test isolation:* Every static seam owns its own `Reset()` so Jmodot-only tests don't depend on the consuming project's reset path.
- *No carve-outs:* "Temporary" violations during refactor are not accepted — fix the design instead. See auto-memory `jmodot_framework_boundary_rule.md` for the no-carve-out pattern.

## Static Seam Pattern (full shape)

```csharp
// Inside Jmodot/Core/Combat/CombatFactoryDefaults.cs
public static class CombatFactoryDefaults
{
    public static IFooDefault? Foo { get; set; }
    public static IBarDefault? Bar { get; set; }

    internal static void Reset()
    {
        Foo = null;
        Bar = null;
    }
}

// Inside {{PROJECT_NAME}} autoload (NOT visible to Jmodot)
public override void _EnterTree()
{
    CombatFactoryDefaults.Foo = new PPFooDefault();
    CombatFactoryDefaults.Bar = new PPBarDefault();
}
```

- Factory consumers inside Jmodot read `CombatFactoryDefaults.Foo` directly — no namespace coupling.
- Override pattern at the consumer call site: `instanceOverride ?? CombatFactoryDefaults.Foo` (per architecture_philosophy *Default Value Pattern*).

## Touchpoints

- [`jmodot_utilities.md`](jmodot_utilities.md) — consumer-facing utility reference.
- [`../skills/architecture_philosophy/SKILL.md`](../skills/architecture_philosophy/SKILL.md) §"Default Value Pattern" — framework-boundary caveat lives there as the canonical home for the pattern.
- Auto-memory `jmodot_framework_boundary_rule.md` — full no-carve-out rationale.
- Auto-memory `jmodot_combat_factory_defaults_seam.md` — six-factory inventory.
