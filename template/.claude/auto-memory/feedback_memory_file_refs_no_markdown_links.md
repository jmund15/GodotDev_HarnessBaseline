---
name: feedback_memory_file_refs_no_markdown_links
description: "{{PROJECT_NAME}} convention: reference memory files as bare code-styled `name.md`, not markdown links. They're cited from many surface types (skills, commands, CLAUDE.md, chat) where no single relative link resolves uniformly."
metadata:
  node_type: memory
  type: feedback
  originSessionId: 10c65425-68c7-4266-a24a-b35e9a15e00d
  modified: 2026-08-16T18:11:54.130Z
---

Reference memory files (`.claude/auto-memory/*.md`) using **bare code-styled text**, not markdown links:

✅ **Correct:** `` `feedback_no_performative_agreement.md` ``
❌ **Wrong:** `` [`feedback_no_performative_agreement.md`](feedback_no_performative_agreement.md) ``

**Why bare code-style** (the files ARE in-repo now, so links *could* resolve — but the convention still holds for a different reason): memory files are cited from many heterogeneous surfaces — `.claude/skills/`, `.claude/commands/`, `CLAUDE.md`, and chat responses. No single relative path resolves from all of them — a link that works from `skills/foo/SKILL.md` is wrong from `CLAUDE.md` or a chat message. The bare file name is the uniform discoverability surface: searchable by name (semantic-search, glob, grep), and a reader opens `feedback_X.md` directly.

**No exception for `MEMORY.md`** (changed 2026-08-16): the index carries no paths at all. Its entries are recall *hooks* — plain prose, no link, no filename — and recall runs by semantic-searching the hook's own wording. Measured at the time of the change: 161 links spent 7,744 chars on filenames against 5,455 chars of hook text, so the addressing cost more than the meaning it addressed, on a surface every session pays for. Stripping them cut the index 18.2 KB → 9.9 KB with no loss of recall, since the hook text is what semantic-search matches anyway.

**Litmus:** citing a memory file from prose → bare `` `name.md` ``, never a markdown link. Adding a line to the `MEMORY.md` index → hook text only, no filename.

**When a hook loses meaning that lived in the filename, put it in the hook.** Six entries needed this in the strip pass (e.g. `precedent_is_evidence_not_authority.md` had rendered as "precedent is evidence", dropping the whole point). The filename is not a place to store meaning the index needs.

**A hook is retrievable when its wording overlaps its file's own vocabulary — audited, not assumed.** All 161 hooks were tested against the live index after the strip: 160 returned their topic file at rank 1–2. The single failure was `inspect first`, a terse abbreviation of *inspect existing abstractions first*; restoring the three dropped words moved it from absent to rank 1. **The failure mode is abbreviation, not brevity** — short hooks like `distress lexicon` and `hysteresis` resolve fine because their words are the file's words.

Recall survives the missing paths because the ranker scores `pathMatch` off **the topic file's own path**, so every hit lands at `pathMatch=1.00` with no help from the index. The index's copy of the filename was always redundant with the file's. Re-run the audit (`terms(hook)` → FTS → rank of the intended file) after any bulk index edit; BM25 alone under-reports, so validate anything it flags through the full ranker before calling it a gap.

**Caught:** 2026-04-29 markdown audit found 9 broken `[...](...)` memory links in a skill; the author had reflexively applied markdown-link syntax to cross-references. Bare code-style avoids the whole class.

Related: [[feedback_memory_md_is_auto_managed]].
