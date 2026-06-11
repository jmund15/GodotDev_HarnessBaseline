---
name: type-name-equals-namespace-leaf-collision
description: "A C# class whose name equals its folder/namespace leaf ({{PROJECT_NAME}}.Foo.Foo) collides — bare type refs bind to the namespace (CS0118); rename the type, aliases don't help."
metadata: 
  node_type: memory
  type: reference
  originSessionId: 42fe26cb-2d31-4490-a3a7-9c29895490e4
---

A class whose name equals its enclosing namespace's leaf segment (`Wizard/Wizard.cs` → `{{PROJECT_NAME}}.Wizard.Wizard`) cannot be referenced by its bare name as a **type** from any *other* `{{PROJECT_NAME}}.*` namespace: the simple name binds to the **namespace** `{{PROJECT_NAME}}.Wizard`, not the type, raising **CS0118** ("namespace used like a type / variable"). A namespace member shadows even a `global using Foo = ...;` alias, so aliasing does NOT fix it.

**Asymmetry that hides it:** expression-context uses resolve fine (`Foo.Instance` — static member access prefers the type); only type-context uses break (`Action<Foo>`, casts, generic args, `is Foo` patterns). A stutter class (`Global`, `Potion`, `RadialBlastScene`) can appear to "work" until someone uses it as a type.

**Fix:** rename the **type** so it differs from the namespace leaf (e.g. `Wizard`→`WizardCharacter`). Do NOT leave the class in the global namespace to dodge it — that's an R13 violation. Full-qualifying every site (`{{PROJECT_NAME}}.Foo.Foo`) also compiles but scatters stutter (159 sites in the Wizard case).

**Concrete:** 2026-05-30 structure alignment — `class Wizard` blocked the `Wizard/` folder from adopting `{{PROJECT_NAME}}.Wizard.*`; renamed to `WizardCharacter`. Documented in `structure_rules.md` R13. Related: [[namespace-rename-breaks-relative-using]].
