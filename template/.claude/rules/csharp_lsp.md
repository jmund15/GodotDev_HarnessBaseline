---
paths:
  - "**/*.cs"
---

# C# LSP Plugin (Code Intelligence)

Type-aware C# navigation, loaded when Claude reads `.cs` files; local only — disabled on cloud via `settings.local.json`. Stack identity, adapter internals and workstation setup: [`reference/rules/csharp_lsp_examples.md`](../reference/rules/csharp_lsp_examples.md) §Adapter and setup.

**PREFER LSP for C# symbol operations:** `findReferences` (callers, usages), `hover` (signatures, types), `incomingCalls` (call chains). LSP is semantic — it resolves by type, not text. Use `Grep` only for the legitimate cases below.

**If LSP fails or returns empty, correct the `filePath`/position and retry.** Fall back to Grep only when the symbol does not resolve.

## Grep-shapes that are LSP-bypass smells on `.cs`

- `Grep("class X")` / `Grep("interface X")` as a *terminal* step → the Grep itself IS the legitimate anchor (workflow below), but stopping there loses semantic resolution. Chain into `LSP documentSymbol` for the line number, then `findReferences` / `incomingCalls` from the anchored position. The smell is *single-Grep-and-stop*, not the Grep itself.
- `Grep(": IFoo")` / `Grep(": .*IFoo,")` to enumerate implementers → use `LSP findReferences` on the interface declaration site, which catches both `: IFoo` declarations AND consumers holding `IFoo` references.
- `Grep("MethodName(")` to find callers → use `LSP findReferences` or `incomingCalls` on the method declaration.
- `Grep("BareSymbolName")` (a single PascalCase identifier with no regex meta) → anchor first; `workspaceSymbol` cannot search by name.
- When the symbol's exact name isn't known yet, fall through to semantic search (CLAUDE.coding.md §Semantic Search MCP) BEFORE Grep — memory holds rules, semantic search holds code, Grep is for literal text patterns.

## Schema quirks

The LSP tool requires `filePath`, `line` and `character` on EVERY operation, and there is **no `query` parameter** (`additionalProperties: false`). Two consequences:

1. **`filePath` is a language-server routing hint** (extension-driven: `.cs` → C# server) AND, for most operations, the thing that identifies the symbol position. It is never `"."` — that returns `Path is not a file` and the call silently fails. Pass a real `.cs` file in the repo.
2. **`workspaceSymbol` is NOT a name-search.** It returns up to 100 symbols from across the workspace, alphabetically by file path, *unfiltered* — useful for orienting in an unfamiliar project, useless for "find the canonical declaration of FooBar", whose answer is almost certainly past position 100.

## Anchor-then-navigate workflow

1. **Declaration file:** semantic search when the name is fuzzy; otherwise `Grep("(class|interface|struct|record|enum) FooBar\b", glob="*.cs")`.
2. **Line:** `LSP documentSymbol` on that file, corrected per the trap below.
3. **Navigate** from a position inside the identifier: `findReferences` for callers, `hover` for the signature, `incomingCalls` for the call hierarchy, `goToDefinition` from a usage. A position on whitespace returns nothing.

Call shapes for each step: `reference/rules/csharp_lsp_examples.md` §Anchor-then-navigate call shapes.

## documentSymbol coordinate trap

`documentSymbol` reports a symbol's `range.start`, which begins at its leading trivia (blank, `///` and `[Attribute]` lines), so the identifier can sit 5–20 lines below the reported line.

- **Empty result:** `findReferences` answering "No references found" on a symbol with obvious callers, confirmed by `hover` answering "the cursor is not on a symbol", means the position is on whitespace.
- **Wrong symbol:** an adjacent token (return type, parameter type, `(`, `{`) can return another symbol's references in an identical output shape. Suspect a column offset before trusting a suspiciously large or topology-wrong caller list.
- **Fix:** find the exact identifier line and column with a single-file signature Grep, then call `findReferences` there. That Grep is legitimate because the LSP call follows it. Grep and call shape, and the probe results: `reference/rules/csharp_lsp_examples.md` §Line-precision anchor and §Coordinate-trap probe results.

## Legitimate Grep on `.cs`

Multi-pattern alternation (`Foo\|Bar\|Baz`), token soup LSP can't disambiguate (XML doc-comment text, string literals, `[Attribute]` markers), comment scans (`TODO\|FIXME\|deferred`), and the carve-out below.

**Verified-unique-name carve-out:** for a single PascalCase identifier you have explicitly verified is unique — no overloads, no other class defining it, no common-verb prefix like `Apply`/`Update`/`Process`/`Get`/`Set`/`Init` — `Grep("FooBar", glob="*.cs")` returns the same set as `LSP findReferences` and saves the coordinate-trap overhead.

**Two conditions must BOTH hold to invoke it:** (1) the user explicitly asserts uniqueness, OR you verified it via `Grep("class FooBar\b" -g "*.cs")` returning exactly one declaration site; AND (2) the response cites the carve-out as the reason for choosing Grep. Without both, default to anchor-then-navigate. The carve-out trades one LSP call for the risk of silently missing indirect callers (delegate references, reflection, generic-arg usages) — take that trade only when it is consciously made. Why uniqueness must be verified rather than assumed: `reference/rules/csharp_lsp_examples.md` §Why the carve-out demands verification.

## Known-broken surface

**Do NOT use `goToImplementation`** — broken in csharp-ls; use `findReferences` on the interface declaration instead. Treat any diagnostics payload as advisory only and trust `dotnet build`; push-mode diagnostics are dropped at the adapter level and pull-mode may be unreliable. Cause: `reference/rules/csharp_lsp_examples.md` §Why diagnostics are disabled.

<!-- retire-when: review-by: 2027-03-05 -->
