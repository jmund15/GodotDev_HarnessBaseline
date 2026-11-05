# Guard overlay: survey — godot layer

`tools/guard_text.py` appends each section below to `survey.md`'s section of the same tier; `survey.md`'s header rules bind this file too.

## detailed

- A PascalCase identifier on `.cs`: the declaration anchor is `Grep("class X\b"|"interface X\b" -g "*.cs")`. [CLAUDE.godot.md §Godot Docs and C# Navigation]
- In `.tres`/`.tscn`/`.gd`/`.godot`: Grep for the occurrences of an exact name; semantic search when the location is unknown (`CLAUDE.coding.md` §Semantic Search MCP).
- Blast-radius sweep: a serialized reference names the target's `res://` path or `uid://`, rarely its class name — Grep both. [rules/godot_files.md §UID handling]

## condensed

- `.cs` anchor: `Grep("class X\b" -g "*.cs")`. In `.tres`/`.tscn`/`.gd`/`.godot`: Grep an exact name's occurrences; semantic search when the location is unknown (`CLAUDE.coding.md` §Semantic Search MCP).
