# Fixture: sa-design-semantics — Mixed Concerns

## Metadata
- **agent:** sa-design-semantics
- **model:** opus
- **agent_source:** session_audit_agents.md
- **checklist_section:** C+D+S

## Synthetic Code

File: `Source/TestFixture/RecipeValidator.cs`

```csharp
namespace {{PROJECT_NAME}}.TestFixture;

using System.Collections.Generic;
using System.IO;

public class RecipeValidator
{
    // VIOLATION: Mixes IO (file reading) with calculation (validation) in same method
    public bool ValidateRecipe(string recipePath)
    {
        var lines = File.ReadAllLines(recipePath);

        var ingredients = new List<string>();
        foreach (var line in lines)
        {
            if (line.StartsWith("entry:"))
            {
                ingredients.Add(line.Substring(6).Trim());
            }
        }

        if (ingredients.Count < 2) { return false; }
        if (ingredients.Count > 6) { return false; }

        var uniqueCheck = new HashSet<string>(ingredients);
        return uniqueCheck.Count == ingredients.Count;
    }
}
```

## Synthetic Diff

```diff
diff --git a/Source/TestFixture/RecipeValidator.cs b/Source/TestFixture/RecipeValidator.cs
new file mode 100644
--- /dev/null
+++ b/Source/TestFixture/RecipeValidator.cs
@@ -0,0 +1,27 @@
+namespace {{PROJECT_NAME}}.TestFixture;
+
+using System.Collections.Generic;
+using System.IO;
+
+public class RecipeValidator
+{
+    public bool ValidateRecipe(string recipePath)
+    {
+        var lines = File.ReadAllLines(recipePath);
+
+        var ingredients = new List<string>();
+        foreach (var line in lines)
+        {
+            if (line.StartsWith("entry:"))
+            {
+                ingredients.Add(line.Substring(6).Trim());
+            }
+        }
+
+        if (ingredients.Count < 2) { return false; }
+        if (ingredients.Count > 6) { return false; }
+
+        var uniqueCheck = new HashSet<string>(ingredients);
+        return uniqueCheck.Count == ingredients.Count;
+    }
+}
```

## Expected Findings

- **min_count:** 1
- **max_count:** 7

### Required
1. **action:** FIX | **category:** rule | **file_contains:** RecipeValidator | **keywords:** separation, concern, IO, pure, testab, mixed, responsibility, single, File.ReadAllLines, coupling
