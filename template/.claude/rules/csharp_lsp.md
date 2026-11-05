---
paths:
  - "**/*.cs"
---

# C# LSP Plugin (Code Intelligence)

Type-aware C# navigation, loaded when Claude reads `.cs` files; local only — disabled on cloud via `settings.local.json`. Stack identity, adapter internals and workstation setup: [`reference/rules/csharp_lsp_examples.md`](../reference/rules/csharp_lsp_examples.md) §Adapter and setup.

**PREFER LSP for C# symbol operations:** `workspaceSymbol` with a `query` (a declaration by name), `findReferences` (callers, usages), `goToImplementation` (implementers of an interface or abstract member, derived classes included), `hover` (signatures, types), `incomingCalls` (call chains). LSP is semantic — it resolves by type, not text. Use `Grep` only for the legitimate cases below.

**The first call of a session pays the solution load, about 30 s; later calls take seconds.** Wait for it. A call failing at once with `server is running` means the server died and this session's client will not restart it: use the anchor Grep fallback and name the coverage it loses.

**If LSP returns empty, correct the `filePath`/position and retry.** Fall back to Grep only when the symbol does not resolve.

Probed: `reference/rules/csharp_lsp_examples.md` §Runtime probe (csharp-ls 0.28.0).

## Schema

Every operation requires `filePath`, `line` and `character`. `filePath` routes the call to the C# server by extension and, for position operations, names the file the position is in; it is never `"."` (`Path is not a file`). `workspaceSymbol` also takes `query`: it returns the symbols whose names contain it, each at its declaration line, with any real `.cs` file as `filePath`.

## Anchor-then-navigate workflow

1. **Declaration:** `workspaceSymbol` with the name as `query`; its line is the declaration line. With the LSP unavailable, `Grep("(class|interface|struct|record|enum) FooBar\b", glob="*.cs")`.
2. **Navigate** from a position inside the identifier: `findReferences` for callers, `goToImplementation` for implementers, `hover` for the signature, `incomingCalls` for the call hierarchy, `goToDefinition` from a usage. A position on whitespace returns nothing.

Call shapes for each step: `reference/rules/csharp_lsp_examples.md` §Anchor-then-navigate call shapes.

## documentSymbol coordinate trap

`documentSymbol` lists a file's symbols. A member's line is its declaration line, but a type's line is its `range.start`, which begins at its leading trivia (blank, `///` and `[Attribute]` lines): the type's identifier can sit well below it. Take a type's line from `workspaceSymbol`.

- **Empty result:** `findReferences` answering "No references found" on a symbol with obvious callers, confirmed by `hover` answering "the cursor is not on a symbol", means the position is on whitespace.
- **Wrong symbol:** an adjacent token (return type, parameter type, `(`, `{`) can return another symbol's references in an identical output shape. Suspect a column offset before trusting a suspiciously large or topology-wrong caller list.
- **Fix:** find the exact identifier line and column with a single-file signature Grep, then call `findReferences` there. That Grep is legitimate because the LSP call follows it. Grep and call shape, and the probe results: `reference/rules/csharp_lsp_examples.md` §Line-precision anchor and §Coordinate-trap probe results.

## Legitimate Grep on `.cs`

Multi-pattern alternation (`Foo\|Bar\|Baz`), token soup LSP can't disambiguate (XML doc-comment text, string literals, `[Attribute]` markers), comment scans (`TODO\|FIXME\|deferred`), the anchor Grep when the LSP is unavailable, and the carve-out below.

**Verified-unique-name carve-out:** for a single PascalCase identifier you have explicitly verified is unique — no overloads, no other class defining it, no common-verb prefix like `Apply`/`Update`/`Process`/`Get`/`Set`/`Init` — `Grep("FooBar", glob="*.cs")` returns the same set as `LSP findReferences` and saves the coordinate-trap overhead.

**Uniqueness must be verified to invoke it:** the user explicitly asserts uniqueness, OR you verified it via `Grep("class FooBar\b" -g "*.cs")` returning exactly one declaration site. Otherwise default to anchor-then-navigate. The carve-out trades one LSP call for the risk of silently missing indirect callers (delegate references, reflection, generic-arg usages) — take that trade only when it is consciously made. Why uniqueness must be verified rather than assumed: `reference/rules/csharp_lsp_examples.md` §Why the carve-out demands verification.

## Diagnostics

Treat any diagnostics payload as advisory only and trust `dotnet build`; push-mode diagnostics are dropped at the adapter level and pull-mode may be unreliable. Cause: `reference/rules/csharp_lsp_examples.md` §Why diagnostics are disabled.

<!-- retire-when: review-by: 2027-03-05 -->
