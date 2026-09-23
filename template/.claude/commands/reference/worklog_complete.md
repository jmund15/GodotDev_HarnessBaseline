---
disable-model-invocation: true
---

# `/worklog` — COMPLETE

Read at a `complete` invocation, and from the triage `complete` disposition. Entry formats: `worklog_formats.md`. Mirror-patch format: `worklog_mirror.md`.

## Operation: COMPLETE

Cross-doc move: the `BLOCK` leaves `Worklog.md` and an `XLINE` lands in `Worklog-Archive.md` under its domain.

1. **Identify the entry.** Match by title or fuzzy search against `[ ]` items in `## Active` **and** `## Ledger`. Multiple matches → list and ask.

2. **Compose an `XLINE`.**
   - `<class>` and `<n>` come from the original `BLOCK` — don't re-classify on completion unless asked.
   - `<ref>` is optional but recommended: commit hash (`abc1234`), PR (`#62`), or session name. Prefer the commit hash for shipped code.
   - Title keeps its wording, unbolded.

3. **Append the `XLINE` to `Worklog-Archive.md` FIRST** (before deleting from Active — atomicity note in step 4). Newest completion goes at the **bottom** of its domain section.

   **Archive doesn't exist:** create it — frontmatter (`title: Worklog Archive`, `status: archive`, `last_updated: <today>`), an `# Worklog Archive` heading, the opacity note (`> Completed worklog items ... never mirrored, never loaded at SessionStart, never scanned by sweep/triage/drive. Read only via /worklog history.`), then a `## <Domain>` section holding the line.
   **Archive has the item's `## <Domain>` section** — `SR(Worklog-Archive.md, search: "<lastline-of-domain-section>\n", replace: "<lastline-of-domain-section>\n<XLINE>\n")`, i.e. just before the next `## ` heading or at end of file.
   **Archive lacks that section** — `APPEND(Worklog-Archive.md, ...)` with a new `## <Domain>` section plus the line.

4. **Delete the `BLOCK` from `Worklog.md`** — `SR(Worklog.md, search: "<the verbatim BLOCK>", replace: "")`, matching exactly the sub-bullets present in this block.

   **Line-ending note:** both docs are LF — use `\n`. `Edit` matches `old_string` literally, so a separator mismatch fails the call with no match found: always verify the edit succeeded. Capture the verbatim block from a targeted read of its domain section, never from memory; on a failed match retry with `\r\n` (re-saved CRLF). See `obsidian_conventions`.

   **Atomicity:** if step 3 failed, do NOT execute this delete — surface the error and stop. A duplicate archive line from a retried step 3 is harmless; the archive is opaque.

5. **Remove the now-empty domain heading.** If the delete left the `### Domain` section with no remaining `- [ ]` items, remove the heading line too. Other `[ ]` items remain → leave it.

6. **`FM()` on BOTH `Worklog.md` and `Worklog-Archive.md`.**

7. **Patch the mirror (incremental).** Remove the completed item's line from `.claude/worklog-titles.md` — and its `### <category>` heading if that was the category's last item in its `## Active`/`## Ledger` supersection — then `Write` back. Do NOT re-read `Worklog.md`. Set `Last synced:` to today.

8. **Confirm:** `Marked complete.`

9. **Pair-emit to tackle history (if applicable).** Read `.claude/worklog-tackle-history.jsonl` if it exists (the `drive` form creates it; absent → skip). If a `tackle` event line has `title` matching the completed item AND no later `completion` event for that title exists, append via `printf '...\n' >> .claude/worklog-tackle-history.jsonl`:
   ```json
   {"event": "completion", "date": "YYYY-MM-DD", "title": "<title verbatim>"}
   ```
   `title` MUST match the tackle event verbatim — pairing is exact-string, not fuzzy. No matching `tackle` → skip silently. This step is what makes the drive form's anti-thrash penalty self-clearing.

### Edge cases for COMPLETE

- **Un-complete (re-open):** treat as a manual `add` — a fresh `BLOCK` with the original title and `Source: re-opened from <YYYY-MM-DD> completion`. There is no `uncomplete` form.
- **Item was scope 4 with a Plan doc:** the `## Linked Docs` entry stays; the doc remains a useful artifact.
- **A `debug` that resolved into a `fix`:** the original class is preserved on the `XLINE`. A follow-up `fix` item is a separate `add`.
