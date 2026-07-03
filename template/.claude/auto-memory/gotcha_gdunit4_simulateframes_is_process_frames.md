---
name: gotcha-gdunit4-simulateframes-is-process-frames
description: ISceneRunner.SimulateFrames pumps _Process (not _PhysicsProcess) — fixed-tick accumulators must live in _Process; pin deltas with the (frames, deltaMs) overload
metadata:
  type: project
---

GdUnit4's `ISceneRunner.SimulateFrames(n)` advances `_Process` frames; `_PhysicsProcess`-driven
logic does not tick under it (the sim ran 0 ticks while the test simulated a full second).
Frame deltas are also not wall-time-stable in headless runs.

**Why:** a fixed-tick sim accumulator carries its own clock and only needs wall delta — putting
it in `_PhysicsProcess` couples it to a physics pump the test runner doesn't drive.

**How to apply:** any accumulator-style runner accumulates in `_Process`;
integration tests use `SimulateFrames(n, 16)` (the delta-per-frame overload) so timing
assertions are deterministic.
