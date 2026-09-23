# Guard overlay: survey — coding layer

`tools/guard_text.py` appends each section below to `survey.md`'s section of the same tier; `survey.md`'s header rules bind this file too.

## detailed

- A code identifier: anchor its declaration, then navigate; never a bare Grep of the name alone. **Under a concurrent dispatch (any fan-out of 2+) the language server is BANNED: anchor with a Grep on the declaration, then Read.** A serialized single-agent dispatch continues from the anchor with LSP `documentSymbol`/`findReferences`. [CLAUDE.coding.md §Semantic Search MCP]

## condensed

- Anchor a code identifier's declaration before navigating; under a concurrent dispatch use Grep and Read, never the language server.
