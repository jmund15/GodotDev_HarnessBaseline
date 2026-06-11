# ISceneRunner Reference

Complete API reference for GdUnit4's scene runner - used for integration testing with input simulation.

## Quick Start

```csharp
[TestCase, RequireGodotRuntime]
public async Task Test_Player_Movement()
{
    using ISceneRunner runner = ISceneRunner.Load("res://Player/player.tscn");

    // Simulate input
    runner.SimulateActionPressed("move_right");
    await runner.AwaitInputProcessed();  // CRITICAL!

    // Assert result
    var pos = runner.GetProperty<Vector3>("Position");
    AssertThat(pos.X).IsGreater(0);
}
```

**Critical:** Always `await AwaitInputProcessed()` after ANY input simulation!

---

## Accessors

| Method | Returns | Description |
|--------|---------|-------------|
| `Scene()` | `Node` | The loaded scene root (null if disposed) |
| `GetProperty<T>(name)` | `T` | Get scene property value |
| `SetProperty(name, value)` | `Variant` | Set scene property, returns new value |
| `FindChild(name, recursive?, owned?)` | `Node` | Find node by name (recursive=true default) |
| `Invoke(name, args...)` | `Variant` | Call scene method dynamically |

```csharp
// Examples
var health = runner.GetProperty<int>("health");
runner.SetProperty("speed", 10.0f);
var weapon = runner.FindChild("WeaponNode");
var result = runner.Invoke("TakeDamage", 25);
```

---

## Frame & Time Control

| Method | Description |
|--------|-------------|
| `SimulateFrames(count)` | Process N frames |
| `SimulateFrames(count, delayMs)` | Process N frames with delay between each |
| `SetTimeFactor(factor)` | Speed multiplier (2.0 = 2x speed) |
| `AwaitMillis(ms)` | Wait for milliseconds of game time |
| `MoveWindowToForeground()` | Show scene window during test |

```csharp
await runner.SimulateFrames(60);           // Process 60 frames
await runner.SimulateFrames(10, 100);      // 10 frames, 100ms between each
runner.SetTimeFactor(5.0);                 // 5x speed
await runner.AwaitMillis(500);             // Wait 500ms game time
```

---

## Awaiting Conditions

| Method | Description |
|--------|-------------|
| `AwaitInputProcessed()` | Wait for input event processing (REQUIRED after input!) |
| `AwaitMethod<T>(name)` | Wait for method to return expected value |

```csharp
// Wait for method result with timeout
await runner.AwaitMethod<bool>("IsReady")
    .IsEqual(true)
    .WithTimeout(5000);
```

---

## Input Simulation

### Keyboard

| Method | Description |
|--------|-------------|
| `SimulateKeyPressed(key, shift?, ctrl?)` | Press and release key |
| `SimulateKeyPress(key)` | Hold key down |
| `SimulateKeyRelease(key)` | Release held key |

```csharp
runner.SimulateKeyPressed(Key.Space);                    // Tap space
runner.SimulateKeyPressed(Key.S, shift: false, ctrl: true); // Ctrl+S
runner.SimulateKeyPress(Key.Shift);                      // Hold shift
runner.SimulateKeyPress(Key.W);                          // Hold W
await runner.AwaitInputProcessed();
runner.SimulateKeyRelease(Key.W);
runner.SimulateKeyRelease(Key.Shift);
```

### Mouse

| Method | Description |
|--------|-------------|
| `SetMousePos(pos)` | Set cursor position |
| `GetMousePosition()` | Get current cursor position |
| `SimulateMouseButtonPressed(button, doubleClick?)` | Click mouse button |
| `SimulateMouseButtonPress(button)` | Hold mouse button |
| `SimulateMouseButtonRelease(button)` | Release mouse button |
| `SimulateMouseMove(pos)` | Move cursor to position |
| `SimulateMouseMoveRelative(offset, duration?, trans?)` | Move by offset |
| `SimulateMouseMoveAbsolute(pos, duration?, trans?)` | Animate to position |

```csharp
runner.SetMousePos(new Vector2(100, 100));
runner.SimulateMouseButtonPressed(MouseButton.Left);           // Click
runner.SimulateMouseButtonPressed(MouseButton.Left, true);     // Double-click
await runner.SimulateMouseMoveAbsolute(new Vector2(500, 300), 0.5f); // Drag
await runner.AwaitInputProcessed();
```

### Actions (Input Map)

| Method | Description |
|--------|-------------|
| `SimulateActionPressed(action)` | Press and release action |
| `SimulateActionPress(action)` | Hold action |
| `SimulateActionRelease(action)` | Release action |

```csharp
runner.SimulateActionPressed("jump");        // Tap jump
runner.SimulateActionPress("sprint");        // Hold sprint
await runner.AwaitInputProcessed();
runner.SimulateActionRelease("sprint");
```

### Touchscreen

| Method | Description |
|--------|-------------|
| `SimulateScreenTouchPressed(pos, doubleTouch?, index?)` | Tap screen |
| `SimulateScreenTouchPress(pos, index?)` | Hold touch |
| `SimulateScreenTouchRelease(index?)` | Release touch |
| `SimulateScreenTouchDrag(pos, index?)` | Start drag |
| `SimulateScreenTouchDragRelative(offset, duration?, trans?, index?)` | Drag by offset |
| `SimulateScreenTouchDragAbsolute(pos, duration?, trans?, index?)` | Drag to position |
| `SimulateScreenTouchDragDrop(from, to, duration?, trans?, index?)` | Complete drag-drop |

```csharp
// Single tap
runner.SimulateScreenTouchPressed(new Vector2(200, 300));

// Drag and drop
await runner.SimulateScreenTouchDragDrop(
    new Vector2(100, 100),  // from
    new Vector2(400, 400),  // to
    0.5f                    // duration
);
await runner.AwaitInputProcessed();

// Multi-touch (index parameter)
runner.SimulateScreenTouchPress(new Vector2(100, 100), index: 0);
runner.SimulateScreenTouchPress(new Vector2(300, 100), index: 1);
```
