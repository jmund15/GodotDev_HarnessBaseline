---
name: gotcha-gdunit4-filter-uses-test-class-name
description: "GdUnit4 --filter matches on the test CLASS FQN (e.g. SchemaMigratorTest.MyTest), not the production class under test (SchemaMigrator.MyMethod). Mistake produces silent \"No test matches\"."
metadata: 
  node_type: memory
  type: reference
  originSessionId: fd0b005f-63ab-4f79-a253-a2db6c715771
---

`dotnet test --filter "FullyQualifiedName~<X>"` for GdUnit4 matches against the TEST class's fully qualified name, not the production class name under test. Filtering on `SchemaMigrator.MyMethod` returns `"No test matches the given testcase filter"` because the actual test FQN is `{{PROJECT_NAME}}.Tests.<Logic|Integration>.<Path>.SchemaMigratorTest.MyMethod_<...>`.

**Why:** The "no match" message looks identical to a real misspelling — wastes a build cycle to spot. P3 session burned one round on `~SchemaMigrator.MigrateIfNeeded_VersionMatchesTarget` before correcting to `~SchemaMigratorTest.MigrateIfNeeded_VersionMatchesTarget`.

**How to apply:** When narrowing to a single test, filter on `<TestClassName>.<MethodName>` — production class + `Test` suffix is the convention. Either `~<TestClassName>` alone or `~<TestClassName>.<MethodName>` works. Substring match on production class name does NOT.
