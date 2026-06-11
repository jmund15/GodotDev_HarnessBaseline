---
name: feedback_memory_file_refs_no_markdown_links
description: "PP convention: reference memory files as bare code-styled `name.md`, not markdown links. They're cited from many surface types (skills, commands, CLAUDE.md, chat) where no single relative link resolves uniformly."
metadata:
  node_type: memory
  type: feedback
  originSessionId: 10c65425-68c7-4266-a24a-b35e9a15e00d
---

Reference memory files (`.claude/auto-memory/*.md`) using **bare code-styled text**, not markdown links:

✅ **Correct:** `` `feedback_no_performative_agreement.md` ``
❌ **Wrong:** `` [`feedback_no_performative_agreement.md`](feedback_no_performative_agreement.md) ``

**Why bare code-style** (the files ARE in-repo now, so links *could* resolve — but the convention still holds for a different reason): memory files are cited from many heterogeneous surfaces — `.claude/skills/`, `.claude/commands/`, `CLAUDE.md`, and chat responses. No single relative path resolves from all of them — a link that works from `skills/foo/SKILL.md` is wrong from `CLAUDE.md` or a chat message. The bare file name is the uniform discoverability surface: searchable by name (semantic-search, glob, grep), and a reader opens `feedback_X.md` directly.

**One exception:** `MEMORY.md` itself sits in `.claude/auto-memory/` alongside the files, so its index entries use co-located relative links (`[Title](feedback_X.md)`) — that's the index's established style, not the citing convention.

**Litmus:** citing a memory file from prose anywhere *other than* the `MEMORY.md` index → bare `` `name.md` ``, not a markdown link.

**Caught:** 2026-04-29 markdown audit found 9 broken `[...](...)` memory links in a skill; the author had reflexively applied markdown-link syntax to cross-references. Bare code-style avoids the whole class.

Related: [[feedback_memory_md_is_auto_managed]].
