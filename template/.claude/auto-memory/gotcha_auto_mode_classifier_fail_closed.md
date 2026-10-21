---
name: gotcha-auto-mode-classifier-fail-closed
description: "Diagnose an auto-mode denial from the actual result and its layer; the classifier judges intent over command prefix, and a gate-loosening edit to a .claude command file is denied by content."
metadata: 
  node_type: memory
  type: project
retire_when:
  - review-by: 2027-02-14
---

# Auto-mode denial diagnosis

**Verify current behavior:** a renewed prompt is not proof the retired shell-shape regression returned. Record the actual tool result and distinguish a hook denial from a classifier verdict.

**Current hook status:** `bash_shape_guard.py` is deleted; `hooks/pre_bash_dispatch.py` lists the Bash guards that remain. For current denials, use `/sync_permissions` §Identify the denial layer before changing permissions; a renewed prompt alone does not justify adding or reactivating a guard.

**Shapes whose behavior is still unmeasured:** an engine binary named through an env var, such as `"$ENGINE_BIN" <args>` (allow rules match EXPANDED text, so an env-var form matches no rule; run the engine through the project's launcher script), backslash-escaped whitespace in paths, and `< file` input redirection.

**The classifier judges intent over prefix.** `sed -i` against a guard file was denied while `Bash(sed *)` sat in `permissions.allow`. Broad allow entries (`cat`, `python3`, `bash`, `sed`) can explain a clean run, so a clean run is not proof the classifier tolerates the shape.

**Gate-loosening harness edits are denied by content, not by shape.** An `Edit`, `Write` or python rewrite that REMOVES or LOOSENS a halt valve, approval gate, "wait for user input" line or numeric attempt cap in a `.claude/` command file is denied by the auto-mode classifier for delegates AND for the driving session alike; writing a script that merely CONTAINS such an edit is denied too. Additions, pointer rewrites and duplicate-row deletions in the same files pass. Do not retry through another mechanism: list the exact intended text under `couldNotSatisfy` and hand it to an interactive session for approval.

**Class lesson:** a guard that works around a PLATFORM defect outlives the defect
silently. The vendor ships a fix, the workaround keeps costing, and nothing signals the change
because the guard's own denial reads exactly like the bug it was written for. Any hook whose
justification is "the platform does X" states the version it was measured against and gets re-tested
when the symptom stops being observed independently. A guard is not evidence of the thing it guards.

**Concrete:** the shell-shape workarounds (client 2.1.232, reverted in 2.1.233, measured clean 2026-09-01), their timeline and the sed-backtick denial are in `gotcha_auto_mode_shell_shape_history`.

Related: [[gotcha_workflow_args_permission_control_chars]] (same fail-closed permission layer), [[archive_claude_code_permissions_location]].
