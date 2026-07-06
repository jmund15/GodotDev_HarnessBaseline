---
name: gotcha-cascade-gate-vacuous-without-godot-bin
description: "Regression-gate import cascade prints PASS even when GODOT_BIN_PATH is unset — the log holds only a bash error, grep matches nothing, and the || branch reports PASS"
metadata: 
  node_type: memory
  type: project
  originSessionId: 8d59ce77-9de6-4405-9305-e5e56b10bc71
---

The `/regression_gate` step-4b cascade check (`"$GODOT_BIN_PATH" --headless --import ... ; grep InvalidCastException log && FAIL || PASS`) is **vacuous when `GODOT_BIN_PATH` is not exported in the shell**: the command fails with `: command not found`, the log contains only that error line, the grep finds no cast exception, and the `||` branch prints `TOOL CASCADE GATE: PASS`.

**Why:** the gate's pass condition is "no match in the log", which cannot distinguish "import ran clean" from "import never ran". Observed 2026-07-04 (a gate printed PASS on an empty run; caught two Parts later when a newly generated PNG's `.import` never appeared).

**How to apply:** the Bash tool shell does NOT inherit `GODOT_BIN_PATH`. Resolve the binary from `.runsettings` (`<GODOT_BIN>` element) and invoke it by absolute path. Verify the gate ran for real: the import log should end with editor-layout DONE lines, and any newly generated asset should gain a `.import` companion. Positive-liveness sibling of [[arch-rule-autonomous-loop-positive-liveness]] — never read "0 findings" as success without proving the lens ran.
