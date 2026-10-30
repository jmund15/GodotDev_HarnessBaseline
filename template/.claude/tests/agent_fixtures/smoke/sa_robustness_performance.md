# Fixture: sa-robustness-performance — Division by Zero

## Metadata
- **agent:** sa-robustness-performance
- **model:** opus
- **agent_source:** session_audit_agents.md
- **checklist_section:** R+P

## Synthetic Code

File: `Source/TestFixture/DamageCalculator.cs`

```csharp
namespace {{PROJECT_NAME}}.TestFixture;

public static class DamageCalculator
{
    /// <summary>
    /// Calculates damage per target when splitting damage across multiple targets.
    /// </summary>
    public static float CalculateSplitDamage(float totalDamage, int targetCount)
    {
        // VIOLATION: No guard for targetCount == 0 — division by zero
        float perTarget = totalDamage / targetCount;
        return perTarget;
    }

    public static float ApplyResistance(float damage, float resistancePercent)
    {
        return damage * (1.0f - resistancePercent);
    }
}
```

## Synthetic Diff

```diff
diff --git a/Source/TestFixture/DamageCalculator.cs b/Source/TestFixture/DamageCalculator.cs
new file mode 100644
--- /dev/null
+++ b/Source/TestFixture/DamageCalculator.cs
@@ -0,0 +1,19 @@
+namespace {{PROJECT_NAME}}.TestFixture;
+
+public static class DamageCalculator
+{
+    /// <summary>
+    /// Calculates damage per target when splitting damage across multiple targets.
+    /// </summary>
+    public static float CalculateSplitDamage(float totalDamage, int targetCount)
+    {
+        float perTarget = totalDamage / targetCount;
+        return perTarget;
+    }
+
+    public static float ApplyResistance(float damage, float resistancePercent)
+    {
+        return damage * (1.0f - resistancePercent);
+    }
+}
```

## Expected Findings

- **min_count:** 1
- **max_count:** 4

### Required
1. **action:** FIX | **category:** bug | **file_contains:** DamageCalculator | **keywords:** division, zero, guard, targetCount, divide, DivideByZero, check, boundary
