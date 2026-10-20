---
disable-model-invocation: true
---

# Godot Test Quality Checklist

Godot items for [`test_quality.md`](test_quality.md), which reads this file when it exists. Each `## Godot <Section>` extends the section of that name there; apply it wherever that section applies.

## Godot Quality

- [ ] **Structure coupling**: Test resolves production nodes by type/`%UniqueName`/exported NodePath, not direct-child-by-name off a scene root — unless topology IS the declared subject, stated in the `<summary>` (`rules/test_authoring.md`)

## Godot Curation

- [ ] **Double already exists**: Any hand-rolled fake/spy/mock was checked against `Tests/Framework/Mocks/` first

## Godot Testability

- [ ] **Lifecycle-trapped logic**: Business logic embedded in Node lifecycle methods (`_Ready`, `_Process`) that could be extracted for unit testing — as a pure static, an injectable service, or a standalone class. A pure static needs no `[RequireGodotRuntime]`
