---
name: powershell_statement_paren_gotcha
description: PowerShell `(if ...) + 1` parses clean but throws "The term 'if' is not recognized" at RUNTIME when the condition is true
metadata:
  type: reference
---

A bare parenthesized STATEMENT in expression position — `(if ($c) { 1 } else { 0 }) + 1` — passes
`[Parser]::ParseFile` clean, so parse-check gates and linters cannot catch it, but throws
`The term 'if' is not recognized as a name of a cmdlet...` at RUNTIME, and only when the `if`
condition is TRUE (the false branch evaluates the statement fine — measured 2026-08-15, the
`run_integration_batched.ps1:356` counter increment crashed every run with a populated manifest,
while the first run on an empty manifest completed).

**Why:** PowerShell's parser accepts a statement inside parentheses in some expression contexts,
but the runtime compiles the `if` as a command invocation when the true branch executes.

**How to apply:** in any PowerShell expression context, use the `$(if (...) {...} else {...})`
subexpression shape or the `$x = if (...) {...} else {...}` assignment shape — both are safe.
Audit existing scripts for the bare-paren shape with a `\(if\s` grep; treat any hit as a latent
runtime crash even if the script appears to work today (it works only until the condition turns
true).
