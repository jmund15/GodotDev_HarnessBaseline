# Guard overlay: any — godot layer

`tools/guard_text.py` appends each section below to `any.md`'s section of the same tier; `any.md`'s header rules bind this file too.

## detailed

- This project's single-flight tools under the concurrency bar: Godot and C# tests, builds, `scripts/verify.ps1` and `/regression_gate` — the GdUnit4 named pipe and the engine are machine-wide single-flight — and the csharp-ls LSP (single-flight wrapper); use Grep/Read instead of the LSP. A brief that mandates a Godot or C# test or build run gets the STOP-and-report. A C# build or test log's error marker is `error CS`. [testing §Single-flight; gotcha_workflow_single_flight_concurrency]

## condensed

- Concurrency bar, this project: Godot/C# tests, builds, `scripts/verify.ps1`, `/regression_gate` and the csharp-ls LSP. C# log error marker: `error CS`.

## minimal

- Under a concurrent dispatch, never run Godot/C# tests, builds, `scripts/verify.ps1`, `/regression_gate` or the csharp-ls LSP.
