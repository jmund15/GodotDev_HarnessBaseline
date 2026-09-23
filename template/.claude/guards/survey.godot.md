# Guard overlay: survey — godot layer

`tools/guard_text.py` appends each section below to `survey.md`'s section of the same tier; `survey.md`'s header rules bind this file too.

## detailed

- A PascalCase identifier on `.cs`: the declaration anchor is `Grep("class X\b"|"interface X\b" -g "*.cs")`; the language server here is the csharp-ls LSP. [CLAUDE.godot.md §Godot Docs and C# Navigation]
- A PascalCase name in `.tres`/`.tscn`/`.gd`/`.godot` or other text data: route to semantic-search, not Grep. Semantic search does not index `.cs`. [CLAUDE.godot.md §Godot Docs and C# Navigation]

## condensed

- `.cs` anchor: `Grep("class X\b" -g "*.cs")`; names in `.tres`/`.tscn`/`.gd` route to semantic-search.
