---
name: gotcha_relocation_doccomment_cref_boundary
description: "Relocating a type PP→Jmodot — audit its OWN doc-comment crefs, not just consumer usings; boundary grep misses unqualified cref names."
metadata: 
  node_type: memory
  type: project
  originSessionId: f0146384-d083-4bb1-98b3-f8796f01edb4
---

When relocating a type across the framework boundary ({{PROJECT_NAME}} → Jmodot), the type's own `<summary>` `<see cref="...">` references to sibling types can still point back at the consumer's namespace — a boundary violation the `{{PROJECT_NAME}}`-literal boundary grep **does not catch**, because crefs name types *unqualified* (`<see cref="SeedStreams"/>`, not `{{PROJECT_NAME}}.Global.SeedStreams`).

**Why:** It also won't trip a compiler error when Jmodot compiles into the same assembly (single `.csproj`) — the cref still resolves. So neither the boundary grep nor `dotnet build` flags it; only reading the moved file does.

**How to apply:** On any PP→Jmodot relocation, after the `git mv` + namespace change, read the moved file's doc comments and genericize any cref naming a consumer-specific type. The "zero-using-edit" blast-radius check covers *consumers' imports*; it does NOT cover the *relocated type's own* outbound doc references. Verified on the L1 `SeedStreamKeyAttribute` relocation (crefs to `SeedStreams`/`SeedStreamsExtensions` genericized). Relates to [[jmodot_framework_boundary_rule]], [[gotcha_namespace_rename_breaks_relative_using]], [[gotcha_errorsonly_build_hides_cref_drift]].
