---
name: type-name-equals-namespace-leaf-collision
description: A C# type that repeats its namespace leaf can bind as the namespace and fail with CS0118.
metadata:
  type: reference
---

A type named the same as its enclosing namespace leaf, such as
`Features/Example/Example.cs` → `{{PROJECT_NAME}}.Features.Example.Example`, can bind as the namespace
when another namespace uses the bare name as a type. C# then reports CS0118.

Expression-context access may still compile, which can hide the collision until the name appears in a
cast, generic argument, delegate, or pattern.

**How to apply:** Give the type a distinct role name, such as `ExampleController`. Do not move it to the
global namespace or scatter fully qualified references as a workaround.
