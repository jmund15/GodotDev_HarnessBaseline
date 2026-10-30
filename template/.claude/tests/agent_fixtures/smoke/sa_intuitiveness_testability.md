# Fixture: sa-intuitiveness-testability — Untestable Lifecycle Logic

## Metadata
- **agent:** sa-intuitiveness-testability
- **model:** sonnet
- **agent_source:** session_audit_agents.md
- **checklist_section:** I+test

## Synthetic Code

File: `Source/TestFixture/PotionBubbler.cs`

```csharp
namespace {{PROJECT_NAME}}.TestFixture;

using Godot;

public partial class PotionBubbler : Node3D
{
    [Export] public float BubbleRate { get; set; } = 0.5f;
    [Export] public float MaxBubbleSize { get; set; } = 2.0f;

    private float _currentSize;
    private float _timer;

    // VIOLATION: Business logic embedded in _Process — impossible to unit test
    // without running the full scene. Should extract to a pure function.
    public override void _Process(double delta)
    {
        _timer += (float)delta;
        if (_timer >= BubbleRate)
        {
            _timer = 0f;
            _currentSize += 0.1f;
            if (_currentSize > MaxBubbleSize)
            {
                _currentSize = 0f;
            }
            Scale = Vector3.One * (1.0f + _currentSize);
        }
    }

    public float GetCurrentSize() => _currentSize;
}
```

## Synthetic Diff

```diff
diff --git a/Source/TestFixture/PotionBubbler.cs b/Source/TestFixture/PotionBubbler.cs
new file mode 100644
--- /dev/null
+++ b/Source/TestFixture/PotionBubbler.cs
@@ -0,0 +1,30 @@
+namespace {{PROJECT_NAME}}.TestFixture;
+
+using Godot;
+
+public partial class PotionBubbler : Node3D
+{
+    [Export] public float BubbleRate { get; set; } = 0.5f;
+    [Export] public float MaxBubbleSize { get; set; } = 2.0f;
+
+    private float _currentSize;
+    private float _timer;
+
+    public override void _Process(double delta)
+    {
+        _timer += (float)delta;
+        if (_timer >= BubbleRate)
+        {
+            _timer = 0f;
+            _currentSize += 0.1f;
+            if (_currentSize > MaxBubbleSize)
+            {
+                _currentSize = 0f;
+            }
+            Scale = Vector3.One * (1.0f + _currentSize);
+        }
+    }
+
+    public float GetCurrentSize() => _currentSize;
+}
```

## Expected Findings

- **min_count:** 1
- **max_count:** 7

### Required
1. **action:** PLAN | **category:** improvement | **file_contains:** PotionBubbler | **keywords:** testab, extract, pure, _Process, unit test, logic, static, business logic
