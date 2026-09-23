# `csharp_lsp.md` — measurements, adapter and setup

Read a section when [`rules/csharp_lsp.md`](../../rules/csharp_lsp.md) names it. This file does not auto-load; the rule carries the routing decisions and the traps, this carries the evidence behind them and the workstation surface.

## Anchor-then-navigate call shapes

```
1. Find the declaration FILE:
   - semantic-search("FooBar")                           → ranked file list (best for fuzzy / when unsure)
   - Grep("class FooBar\b" -g "*.cs")                    → declaration sites only (legitimate Grep, since LSP can't do this)
   - Grep("interface FooBar\b" -g "*.cs")                → for interfaces
   - Grep("(struct|record|enum) FooBar\b" -g "*.cs")     → for value types

2. Find the LINE within that file:
   LSP(operation="documentSymbol", filePath="<file from step 1>", line=1, character=1)
   → returns ALL symbols in the file, at their range.start lines (see the coordinate trap).

3. Navigate semantically from the anchored position:
   LSP(operation="findReferences",   filePath="<file>", line=<decl line>, character=<col>)   → all callers
   LSP(operation="hover",            filePath="<file>", line=<decl line>, character=<col>)   → signature + XML doc
   LSP(operation="incomingCalls",    filePath="<file>", line=<decl line>, character=<col>)   → call hierarchy
   LSP(operation="goToDefinition",   filePath="<usage file>", line=<usage line>, character=<col>)  → jump from a usage to the decl

   For methods, character should land inside the identifier (typically the last char works).
```

## Line-precision anchor

`documentSymbol` surfaces the LSP **`range.start`**, not **`selectionRange.start`**. Per LSP/Roslyn convention `range.start` is the start of all leading trivia between the previous token and the symbol's first non-whitespace token. The line-precision anchor is the only fully safe path to the identifier:

```
Step 2b (when documentSymbol's line gives empty findReferences):
  Grep("(public|private|protected|internal|static).*MethodName\\(" -g "<that.cs>")
    → exact identifier line and column
  LSP(operation="findReferences", filePath="<that.cs>", line=<exact>, character=<col on identifier>)
```

That Grep is single-file and anchored to a method-signature shape: the within-file parallel of the cross-file `Grep("class FooBar\b")` anchor in step 1.

## Coordinate-trap probe results

Empirically confirmed 2026-05-03 against `AbilityBuilder.ApplySynergies`. `documentSymbol` reported the symbol's `range.start` — the start of all leading trivia — while the identifier sat at `selectionRange.start` well below it.

Within the correct line, the valid identifier window was about 14 columns wide, and the three probes outside it failed in three different ways:

| Character column | Token under the cursor | `findReferences` result |
|---|---|---|
| 39 | the `ApplySynergies` identifier | 9 callers — correct |
| 53 | the opening `(` | 216 results, for a different symbol |
| 14 | the return type `HashSet` | empty (a BCL type) |

Column 53 is the dangerous one: the output shape is identical to a correct answer. That is why the rule tells you to suspect a column-off-by-N whenever a caller list comes back suspiciously large or topology-wrong.

## Why the carve-out demands verification

The verified-unique-name carve-out was softened into the doctrine after the C2 routing-battery case, and the same case shows why the verification step is not optional: `ApplySynergies` *is* unique, but `Apply` alone returns 500+ matches. An agent cannot reliably tell which of the two shapes it is holding before it searches, so uniqueness has to be established rather than assumed.

## Why diagnostics are disabled

Push-mode diagnostics are dropped at the adapter level (re-disabled 2026-05-07). csharp-ls indexes Godot's generated `obj/.../*.g.cs` partial-class output alongside the source `.cs`, producing structural `CS0102` / `CS0111` false positives that the v0.24 server-side fixes cannot address. Pull-mode diagnostics may also be unreliable. `dotnet build` (~8s) remains the authoritative compile-error check.

## Adapter and setup

- **Stack:** `csharp-ls` v0.24.0 behind a Node.js adapter.
- **Adapter:** `.claude/tools/csharp-ls-adapter.js` is canonical. It fixes `workspace/configuration` and `file://` URI normalization and carries a per-request timeout watchdog (`LSP_REQUEST_TIMEOUT_MS`, default 60s — it synthesizes an error response on hang). Set `LSP_ADAPTER_DEBUG=1` for debug logging.
- **Setup:** `.claude/tools/setup-csharp-ls.sh` provisions a new workstation. Local only — disabled on cloud via `settings.local.json`.
