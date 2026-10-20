# Guard overlay: any — godot layer

`tools/guard_text.py` appends each section below to `any.md`'s section of the same tier; `any.md`'s header rules bind this file too.

## detailed

- `/regression_gate` and a scope `verify.ps1` run are your dispatcher's at close, solo or concurrent (`guards/author.md`). Concurrent dispatch bars the csharp-ls LSP; use Grep/Read instead. A multi-job dispatch also bars Godot/C# tests and builds. A chain lane may run those unless its `MACHINE-WIDE single-flight` guard names them; runtime-test width follows orchestration §2 Width. A mandated barred run gets STOP-and-report. C# log error marker: `error CS`. [testing §Quick Start runtime-slots bullet]
- Start every Godot/C# test or build call with `run_in_background: true`, keep working, then wait with the until-loop rule in `guards/any.md`; a gate call uses `-Detach`. A Python proof under `.claude/tests/` is not such a call. [testing §Quick Start]

## condensed

- `/regression_gate` and a scope `verify.ps1` run are your dispatcher's at close (`guards/author.md`). Concurrent dispatch bars the csharp-ls LSP. A multi-job dispatch also bars Godot/C# tests and builds; a chain lane may run those unless its guard names them, within orchestration §2 Width. C# log error marker: `error CS`.
- Start Godot/C# test and build calls with `run_in_background: true`, then use the until-loop rule in `guards/any.md`; a gate call uses `-Detach`. Python proofs are not such calls. [testing §Quick Start]

## minimal

- Never run `/regression_gate` or a scope `verify.ps1`: your dispatcher runs them at close. Under a concurrent dispatch, never run the csharp-ls LSP. A multi-job dispatch also bars Godot/C# tests and builds; a chain lane may run those unless its guard names them, within orchestration §2 Width.
- Start Godot/C# test and build calls with `run_in_background: true`; a gate call uses `-Detach`; wait with the until-loop rule in `guards/any.md`. [testing §Quick Start]
