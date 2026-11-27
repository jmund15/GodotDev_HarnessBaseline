# Guard overlay: common — godot layer

`tools/guard_text.py` appends each section below to `common.md`'s section of the same tier; `common.md`'s header rules bind this file too.

## detailed

- The drive-close scope run (`verify.ps1 -Scope`) is your dispatcher's, solo or concurrent; the full `/regression_gate` runs in the main check after the land (`change_control` §Gate cadence). A multi-job dispatch also bars Godot/C# tests and builds. A chain lane may run those unless its `MACHINE-WIDE single-flight` guard names them; runtime-test width follows orchestration §2 Width. A mandated barred run gets STOP-and-report. C# log error marker: `error CS`. [testing §Quick Start runtime-slots bullet]
- Run a Godot/C# test or build call in the foreground when its verdict is your next step: the verdict then returns in that call. Start it with `run_in_background: true` only when you have other work meanwhile. A gate call uses `-Detach`. A Python proof under `.claude/tests/` is not such a call. [testing §Quick Start]

## condensed

- The drive-close scope run (`verify.ps1 -Scope`) is your dispatcher's; the full gate runs in the main check (`change_control` §Gate cadence). A multi-job dispatch also bars Godot/C# tests and builds; a chain lane may run those unless its guard names them, within orchestration §2 Width. C# log error marker: `error CS`.
- Run Godot/C# test and build calls in the foreground when the verdict is your next step, so it returns in that call, and in the background only when you have other work meanwhile; a gate call uses `-Detach`. Python proofs are not such calls. [testing §Quick Start]

## minimal

- Never run `/regression_gate` or a scope `verify.ps1`: your dispatcher runs the scope run at close, and the main check runs the gate after the land (`change_control` §Gate cadence). A multi-job dispatch also bars Godot/C# tests and builds; a chain lane may run those unless its guard names them, within orchestration §2 Width.
- Run Godot/C# test and build calls in the foreground when the verdict is your next step; a gate call uses `-Detach`. [testing §Quick Start]
