---
paths:
  - "**/*.cs"
---

# C# LSP Plugin (Code Intelligence)

Type-aware C# navigation via `csharp-ls` v0.24.0 + Node.js adapter. Loads when Claude reads `.cs` files.

**PREFER LSP for C# symbol operations:** `findReferences` (callers, usages), `hover` (signatures, types), `incomingCalls` (call chains). LSP is semantic — it resolves by type, not text. Use `Grep` only for the legitimate cases below.

**Anti-patterns (Grep-shapes that are LSP-bypass smells on `.cs`):**

- `Grep("class X")` / `Grep("interface X")` as a *terminal* step → the Grep itself IS the legitimate anchor (see workflow below), but stopping there loses semantic resolution. Chain into `LSP documentSymbol` for the line number, then `findReferences` / `incomingCalls` from the anchored position. The smell is *single-Grep-and-stop*, not the Grep itself.
- `Grep(": IFoo")` / `Grep(": .*IFoo,")` to enumerate implementers → use `LSP findReferences` on the interface declaration site (after anchoring per workflow below). Catches both `: IFoo` declarations AND consumers holding `IFoo` references.
- `Grep("MethodName(")` to find callers → use `LSP findReferences` or `incomingCalls` on the method declaration (after anchoring per workflow below).
- `Grep("BareSymbolName")` (single PascalCase identifier with no regex meta) → see "Anchor-then-navigate workflow" below. The MCP LSP tool's `workspaceSymbol` cannot search by name (no `query` parameter) — you need an anchor first.
- When the symbol's exact name isn't known yet, fall through to semantic-search (CLAUDE.md §8) BEFORE Grep — Memory holds rules; semantic-search holds code; Grep is for literal text patterns.

**Calling LSP correctly — schema quirks:** the LSP tool requires `filePath`, `line`, and `character` on EVERY operation. There is **no `query` parameter** in the schema (`additionalProperties: false`). Two consequences:

1. **`filePath` is a language-server routing hint** (extension-driven: `.cs` → C# server) AND for most operations it identifies the symbol position. It is never `"."` — that returns `Path is not a file` and the call silently fails. Pass a real `.cs` file in the repo.
2. **`workspaceSymbol` is NOT a name-search.** It returns up to 100 symbols from across the workspace, alphabetically by file path, *unfiltered*. Useful as a "browse the workspace" tool for orientation in an unfamiliar project. Useless for "find the canonical declaration of FooBar" — the answer is almost certainly past position 100 in the alphabetical dump.

**Anchor-then-navigate workflow (the right way to find a C# symbol):**

```
1. Find the declaration FILE:
   - semantic-search("FooBar")                           → ranked file list (best for fuzzy / when unsure)
   - Grep("class FooBar\b" -g "*.cs")                    → declaration sites only (legitimate Grep — NOT a bypass smell, since LSP can't do this)
   - Grep("interface FooBar\b" -g "*.cs")                → for interfaces
   - Grep("(struct|record|enum) FooBar\b" -g "*.cs")     → for value types

2. Find the LINE within that file:
   LSP(operation="documentSymbol", filePath="<file from step 1>", line=1, character=1)
   → returns ALL symbols in the file with exact line numbers.

3. Now navigate semantically from the anchored position:
   LSP(operation="findReferences",   filePath="<file>", line=<decl line>, character=<col>)   → all callers
   LSP(operation="hover",            filePath="<file>", line=<decl line>, character=<col>)   → signature + XML doc
   LSP(operation="incomingCalls",    filePath="<file>", line=<decl line>, character=<col>)   → call hierarchy
   LSP(operation="goToDefinition",   filePath="<usage file>", line=<usage line>, character=<col>)  → jump from a usage to the decl

   For methods, character should land inside the identifier (typically the last char works).
   The line/character must be on the symbol — pointing at whitespace returns nothing.
```

**When the LSP call fails or returns empty, do not silently fall through to Grep+Read** — that is the C2-failure shape (rubric scores PASS by name, but functional outcome equals never reaching for LSP). Re-issue with a corrected `filePath`/position first; only fall back to Grep if the symbol genuinely doesn't resolve.

**`documentSymbol` line-coordinate gotcha (range.start vs selectionRange.start) — EMPIRICALLY CONFIRMED 2026-05-03 against `SpellCrafter.ApplySynergies`:** when `documentSymbol` reports a line for a symbol (method / property / field), the wrapper surfaces the symbol's **full LSP `range.start`** rather than its **`selectionRange.start`**. Per LSP / Roslyn convention, `range.start` is the start of *all leading trivia* — every blank line + every `/// <summary>` line + every `[Attribute]` line between the previous token and the symbol's first non-whitespace token. The actual identifier sits at `selectionRange.start`, which can be 5–20 lines BELOW `range.start` depending on whitespace + doc-comments + attribute count.

Symptom (silent-empty case): `findReferences(line=<reported>)` returns `"No references found"` on a symbol that obviously has callers. Diagnostic: `hover(line=<reported>, character=<reported>)` — if hover returns `"the cursor is not on a symbol"`, you've hit this gotcha (the LSP server is fine; the position is on whitespace).

**Worse symptom (silent-wrong-symbol case, empirically observed 2026-05-03):** the *valid identifier-position window* is only ~14 columns wide (the identifier itself). Positions inside the same line but on adjacent tokens — return type, parameter type, opening `(`, opening `{` — can return `findReferences` results for a *different* symbol entirely (BCL types return empty; project types return their full caller graph, which can be 100+ entries). Empirically: column 39 → correct 9 callers of `ApplySynergies`; column 53 (`(`) → 216 results for a different symbol; column 14 (return-type `HashSet`) → empty. **There is no diagnostic that distinguishes "right symbol, no callers" from "wrong symbol, many callers"** — `findReferences` returns identical-shape output either way. If a refactor-planning result comes back with a suspiciously-large or topology-wrong caller list, suspect a column-off-by-N before trusting it. **Workaround — line-precision anchor before findReferences (the only fully-safe path):**

```
Step 2b (when documentSymbol's line gives empty findReferences):
  Grep("(public|private|protected|internal|static).*MethodName\\(" -g "<that.cs>")
    → exact identifier line and column
  LSP(operation="findReferences", filePath="<that.cs>", line=<exact>, character=<col on identifier>)
```

This Grep IS legitimate (single-file, anchored to a method-signature shape — it's the "find the identifier line within a known file" anchor, parallel to the cross-file `Grep("class FooBar\b")` anchor in step 1). NOT an LSP-bypass smell because the LSP call IS the next step.

**Legitimate Grep on `.cs`:** multi-pattern alternation (`Foo\|Bar\|Baz`), token soup LSP can't disambiguate (XML doc-comment text, string literals, `[Attribute]` markers), comment scans (`TODO\|FIXME\|deferred`), or **verified-unique-name lookup** (carve-out below).

**Verified-unique-name carve-out:** for a single PascalCase identifier you have explicitly verified is unique (no overloads, no other class defines it, no common-verb prefix like `Apply`/`Update`/`Process`/`Get`/`Set`/`Init`), `Grep("FooBar", glob="*.cs")` returns the same set as `LSP findReferences` and saves the documentSymbol coordinate-gotcha overhead. **Two conditions must BOTH hold to invoke this carve-out:** (1) the user explicitly asserts uniqueness OR you have verified it via `Grep("class FooBar\b" -g "*.cs")` returning exactly one declaration site, AND (2) the response cites the carve-out as the reason for choosing Grep. Without explicit verification + justification, default to anchor-then-navigate — uniqueness is easy to assume incorrectly (the C2 case: `ApplySynergies` *is* unique, but `Apply` alone has 500+ matches; the agent can't always tell upfront). The carve-out trades 1 LSP call for risk of silently missing indirect callers (delegate references, reflection, generic-arg usages) — accept this trade only when the trade is consciously made.

**Do NOT use:** `goToImplementation` (broken in csharp-ls — use `findReferences` on the interface declaration instead). Pull-mode `lsp__getDiagnostics` tool may be unreliable; `dotnet build` remains the authoritative compile-error check (~8s). Push-mode `publishDiagnostics` notifications are dropped at the adapter level (re-disabled 2026-05-07) — csharp-ls indexes Godot's generated `obj/.../*.g.cs` partial-class output alongside the source `.cs`, producing structural CS0102/CS0111 false positives that the v0.24 server-side fixes can't address. Treat any `lsp_diagnostics`-style payload as advisory only; trust `dotnet build`.

**Adapter:** `.claude/tools/csharp-ls-adapter.js` (canonical). Fixes `workspace/configuration` + `file://` URI normalization, includes per-request timeout watchdog (`LSP_REQUEST_TIMEOUT_MS`, default 60s — synthesizes error response on hang). `LSP_ADAPTER_DEBUG=1` for debug logging.

**Setup:** `.claude/tools/setup-csharp-ls.sh` for new workstations. **Local only** — disabled on cloud via `settings.local.json`.
