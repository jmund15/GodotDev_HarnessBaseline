---
name: read_files — enumerate file paths before bundling, never pass directory paths
description: When delegating Obsidian/vault/folder synthesis to mcp__ai-worker__read_files, glob to a list of file paths first. Passing directory paths used to silently drop entire subtrees on Windows; the worker now expands them but the discipline still matters for visibility and cap awareness.
type: feedback
originSessionId: 3f50f976-9fb2-4f14-ba5e-80457fd7b2ae
---
When using `mcp__ai-worker__read_files` to synthesize across Obsidian docs (or any folder of source files), enumerate to a concrete list of file paths *before* the call rather than passing directory paths.

**Why:**
On 2026-05-05, a brainstorm-prep `read_files` call passed five directory paths (`Design/`, `Planning/`, `BrainstormingDesigns/`, `Documentation/`, `Jmodot/Claude/`) plus two file paths. The worker's `read_text()` raised Windows `PermissionError` on each directory (Windows treats `open()` on a directory as EACCES, unlike Linux's `IsADirectoryError`). The reader model then **paraphrased** `[READ ERROR: ...permission denied...]` as "the folders are locked / permission errors", losing the literal marker text and making the entire failure invisible. A `Multi-Spell System Design.md` doc that directly answered the brainstorm question lived in one of the dropped subtrees and was completely missed.

The worker has since been patched (server.py:`_expand_paths` + conditional anti-confabulation directive in `read_files`), so directory paths now expand recursively to a 50-file cap. **The discipline still matters because:**
- Enumerating first lets you see exactly which files will be read (catch typos / wrong-vault before burning a worker call).
- The 50-file cap fires silently from the orchestrator's perspective unless you read the response carefully — explicit enumeration sidesteps it entirely for large folders.
- It documents intent in the call site (`paths=[<10 explicit docs>]` is self-explaining; `paths=[<one folder>]` is not).

**How to apply:**
- For Obsidian synthesis: `Glob("ObsidianVault/.../<Folder>/**/*.md")` → pick the relevant subset → `read_files(paths=[<that subset>], question=...)`. Or, if Obsidian MCP is online, use `obsidian_list_notes` as the enumerator.
- For code synthesis: `Glob` / `LSP` / `semantic-search` to a file list first, then bundle.
- Pass a directory path only when you genuinely want "everything in here" AND have confirmed the count is under ~50 — and even then, prefer the explicit list.
- If a worker response contains a `[DIRECTORY EXPANSION CAPPED: ...]` or `[DIRECTORY EMPTY: ...]` marker, treat the answer as partial and re-bundle with explicit paths.

**Related:** worker patch lives in `~/.config/ai-worker/server.py` (`_expand_paths`, `_DIR_EXPAND_MAX_FILES = 50`, `_DIR_EXPAND_EXTENSIONS`); reader-prompt anti-confabulation directive is conditional in the same `read_files` body. If the cap or extension allowlist needs tuning, edit those constants — no config knob currently exposed.

**Verified:** 2026-06-04 memory-claim audit — the *current* claim is accurate (the EACCES-silent-drop mechanism is correctly marked superseded): probed `read_files(paths=["…/Currency/Drops"])` (a directory) and the worker expanded it, reading all 3 contained files (no PermissionError, no silent drop). The patched expand-directory behavior is live; the enumerate-first rule is now discipline (visibility/cap-awareness), not a correctness requirement.
