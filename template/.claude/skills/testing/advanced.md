# Advanced GdUnit4 Features

Lifecycle hooks, parameterized tests, utilities, and FAQ solutions.

## Lifecycle Hooks

| Attribute | Scope | When | Use For |
|-----------|-------|------|---------|
| `[Before]` | Suite | Once before all tests | Expensive setup (DB connections, shared resources) |
| `[After]` | Suite | Once after all tests | Cleanup shared resources |
| `[BeforeTest]` | Test | Before each test | Fresh instance per test |
| `[AfterTest]` | Test | After each test | Reset state, cleanup temp files |

```csharp
[TestSuite]
public partial class MyTests
{
    private Node _sharedResource = null!;
    private Node _testInstance = null!;

    [Before]
    public void SetupSuite()
    {
        _sharedResource = new Node();  // Once for all tests
    }

    [After]
    public void TearDownSuite()
    {
        _sharedResource?.Free();
    }

    [BeforeTest]
    public void SetupTest()
    {
        _testInstance = AutoFree(new Node());  // Fresh per test
    }

    [AfterTest]
    public void TearDownTest()
    {
        // Reset any global state modified by test
    }
}
```

---

## Parameterized Tests

### Multiple Test Cases

```csharp
[TestCase(1, 2, 3)]
[TestCase(10, 20, 30)]
[TestCase(-1, 1, 0)]
public void Test_Addition(int a, int b, int expected)
{
    AssertThat(a + b).IsEqual(expected);
}

// With custom names for failure identification
[TestCase(1, 2, 3, TestName = "Small numbers")]
[TestCase(100, 200, 300, TestName = "Large numbers")]
public void Test_Addition_Named(int a, int b, int expected) { }
```

### Dynamic Data with DataPoint (C# Only)

```csharp
[TestCase]
[DataPoint(nameof(AdditionData))]
public void Test_Addition_Dynamic(int a, int b, int expected)
{
    AssertThat(a + b).IsEqual(expected);
}

// Static property as data source
public static IEnumerable<object[]> AdditionData => new[]
{
    new object[] { 1, 2, 3 },
    new object[] { 10, 20, 30 },
};

// Async data source
public static async IAsyncEnumerable<object[]> AsyncData
{
    await Task.Delay(1);
    yield return new object[] { 1, 2, 3 };
}

// External class data source
[TestCase]
[DataPoint(typeof(ExternalDataProvider), nameof(ExternalDataProvider.Data))]
public void Test_External(int value) { }
```

---

## Skipping Tests

```csharp
// Always skip
[TestCase(DoSkip = true)]
public void Test_NotImplemented() { }

// Skip with reason (shows in test output)
[TestCase(DoSkip = true, SkipReason = "Waiting for API update")]
public void Test_FutureFeature() { }

// Conditional skip (runtime)
[TestCase(DoSkip = Engine.GetVersionInfo()["hex"].AsInt64() < 0x40100)]
public void Test_Godot41_Only() { }

// Skip entire suite via [Before]
[Before(DoSkip = true, SkipReason = "Suite disabled")]
public void Setup() { }
```

---

## Flaky Test Handling

Configure in GdUnit4 Settings:
- **Enable Flaky Test Retry:** Toggle on
- **Max Retries:** Default 3

**Best Practice:** Investigate root causes (race conditions, timing) rather than relying on retries.

---

## Test Utilities

### Auto-Cleanup

```csharp
// Object freed automatically after test
var node = AutoFree(new Node3D());
```

### Temporary Files

```csharp
// Create temp directory (auto-cleaned after suite)
var dir = CreateTempDir("my_test_data");

// Create temp file
var file = CreateTempFile("test.txt");
file.StoreString("test content");
// File auto-closed after test
```

### Resource Loading

```csharp
// Load resource file as string
var content = ResourceAsString("res://test_data/expected.txt");

// Load as array of lines
var lines = ResourceAsArray("res://test_data/items.csv");
```

---

## FAQ Solutions

### Script errors after GdUnit4 install
1. Restart Godot Editor
2. If persists: Close editor → Delete `.godot` folder → Reopen

### Tests hang with paused game
**Cause:** `mainLoop.Paused = true` freezes GdUnit4

```csharp
[AfterTest]
public void ResetPause()
{
    Engine.GetMainLoop().SetMeta("paused", false);
}
```

### Export crashes with GdUnit4
**Cause:** Godot can't export editor-only plugins

**Solution:** Project → Export → Resources tab → Add filter:
```
addons/gdUnit4/*
```

### Test timeout (default 5 minutes)
```csharp
// Custom timeout per test
[TestCase(Timeout = 10000)]  // 10 seconds
public void Test_Quick() { }

// Or configure globally in GdUnit4 Settings
```
