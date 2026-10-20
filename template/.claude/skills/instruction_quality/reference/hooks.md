# Hook files — §13–§16

Read in full when the edit changes a hook, a guard, a dispatcher, a hook's settings registration or any model-facing string a hook emits. `SKILL.md` §1–§6b apply to it too.

## Hook-specific

§1–§6 apply to docstrings and every model-facing string a hook emits: each names an action or a condition, never a slogan.

### 13. Channel-contract validity
For PreToolUse/PostToolUse advisories, use `hookSpecificOutput.additionalContext` with exit 0. Plain stderr with exit 0 is not a model advisory. Block with exit 2 plus stderr, or with a `permissionDecision` JSON decision; `systemMessage` reaches the user. SessionStart/UserPromptSubmit stdout is model-facing. Verify the live event contract rather than assume other events behave identically. An allow decision covers the whole command: validate everything it executes.

### 14. Registration & liveness
Registration proves wiring, not matching. Verify the actual settings registration and matcher. Prove clean → planted violation → restored clean through the real output channel. An empty match set is not evidence until the positive control fires. Every hook has a consumer; remove orphan registrations and expired diagnostics, because a settings entry pointing at a deleted file bricks every tool its matcher covers. A crash is not an allow result.

### 15. Bounded state & cost
Measure hook runtime and emitted context per event. Reuse existing dispatchers instead of multiplying subprocesses. Deduplicate repeated advice and cap/rotate append-only state. Shared writes are atomic; establish writer/read ordering. Internal timeouts fit the caller's deadline.

### 16. Fail posture & docstring/behavior parity
Failures of a guard that protects the floor (peer work, owner authority, the gate) deny the guarded action. Other dispatcher failures are non-blocking and report the lost check and repair through `additionalContext`. Unknown observations are not healthy results. A trigger must see enough state to be right, or narrow it. Docstrings describe live behavior. Flag hardcoded environment assumptions that can drift on upgrade.
