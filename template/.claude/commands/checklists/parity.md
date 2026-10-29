---
description: Check OLD-versus-NEW behavior parity over a refactor or a retired file. Skip diffs that only add code.
---

# Shared Parity Checklist
<!-- Used by: /pr_ready (the `parity` seat), /review_pr (its code-review seat), /session_audit §1.5b. One home for the `parity` mandate key. -->

## Mandate

Mandate (`parity`): diff OLD vs NEW per changed method. Flag every dropped/weakened branch, stub, `TODO`, `// deferred`, removed validation, or silently-changed default as **critical** (`critical:true`, `minRigor` light). Quote old vs new verbatim with file:line. Return a JSON findings array (`{agent, action, category, critical, file, description, old, new, rationale}`). This is audit-shape line-precision work — read OLD AND NEW.

## Retired-code procedure

For each session-changed file in a directory where another file was DELETED in the session diff (`git diff <base> --diff-filter=D --name-only`), enumerate parity:

1. Read the deleted file (via `git show <base>:<path>`).
2. List its public surface: `[Export]` fields, public methods, lifecycle hooks (`OnEnter`/`OnExit`/`OnProcessFrame`/`_Ready`/etc.), event subscriptions, side effects (Engine state, BB writes, signals emitted).
3. For each item, verify the replacement either:
   - **Reproduces** the behavior (point at the new line that does it), OR
   - **Explicitly notes removal** in a comment, docstring, or PR description.

Any retired surface item NOT accounted for is a critical finding. Examples:
- Old `MenuOpenState.OnEnter` instantiated `SlowMotionController`; new `OpenMenuState.OnEnter` doesn't.
- Old state resolved BB refs in `OnEnter`; new state did it in `OnInit` (silent semantic change).
