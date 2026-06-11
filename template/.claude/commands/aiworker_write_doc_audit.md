---
disable-model-invocation: true
---

Audit recent doc-workflow output for write_doc rule violations. Advisory; does NOT block.

## When to use

- After `/Brainstorming`, `/doc_full`, `/doc_architecture`, `/doc_retrospective`, or `/create_obsidian_design_doc` completes — verify the output didn't drift from the system-prompt rules.
- Periodically as a drift watch — combine with `/eval_dashboard` cadence.
- After a `models.yaml` or `write_doc.*.md` change to confirm the change took effect on real output.

Companion to `/doc_workflow_battery` (standalone scenario tests). This command audits *actual* output; the battery tests *capability* with synthetic scenarios. Run both for full picture.

## Procedure

### Step 1: Identify recent docs

Bash to enumerate docs modified in the last 7 days (or since last audit). Three doc surfaces to scan:

```bash
DOCS_ROOT="{{VAULT_ROOT}}/DevProjects/{{PROJECT_NAME}}/Claude"
find "$DOCS_ROOT/BrainstormingDesigns/" -name "*.md" -mtime -7 -not -name "_*"
find "$DOCS_ROOT/Documentation/" -name "Architecture.md" -mtime -7
find "$DOCS_ROOT/Documentation/" -name "Retrospective.md" -mtime -7
```

If `find` returns zero files, exit cleanly: *"No docs modified in the last 7 days. Nothing to audit."*

### Step 2: Run violation scans per doc

For each doc identified in Step 1, run these Grep-anchored checks. Use the `Grep` tool, not bash grep — output shape is consistent and line numbers are first-class.

| Check ID | Pattern (regex) | Severity | Rule violated |
|---|---|---|---|
| **V1** Inline conversational provenance | `user surfaced\|per user direction\|per user pushback\|user said\|user mentioned\|user requested\|user direction was\|user flagged` | **HIGH** | `write_doc.design.md` "Conversational provenance belongs in Revision History as one summary line, NEVER inline" |
| **V2** Vague rejection reasoning | `less suitable\|didn't fit\|more complex\|too complex\|wasn't right\|not ideal\|suboptimal` (within 3 lines of `rejected\|considered\|alternative\|approach`) | **MEDIUM** | `write_doc.design.md` "For every rejected approach, give a SPECIFIC reason... 'Less suitable' / 'didn't fit' / 'complex' are NOT specific reasons" |
| **V3** Idea-pool enumeration | Markdown tables under `## Idea Pool\|## Bonus Ideas\|## Room Ideas\|## Pool` headings with >15 data rows | **MEDIUM** | `write_doc.design.md` "Idea pools: commit to a target count + 5–10 representative entries. Do not exhaustively enumerate" |
| **V4** Line-count overrun | `wc -l` on doc body; design >1500 lines, architecture >1200 lines | **LOW** | `write_doc.design.md` "~1200–1400 line target"; `write_doc.architecture.md` "~600–1000 lines, surface in Open Questions if >1200" |
| **V5** Stale model reference | `kimi\|highest accuracy` outside the doc's Revision History / Changelog sections | **LOW** | Cleanup — references model that's no longer the default since 2026-05-11 |

For V2, the proximity rule matters — `Grep` with `-C 3` then post-filter for the rejection-context anchor words.

For V3, scan for table-shape (lines starting with `|`) under matching headings, count rows.

### Step 3: Report

Print a per-doc table of findings:

```markdown
## Doc Audit — YYYY-MM-DD

### `BrainstormingDesigns/2026-05-12-stamina.md` (1247 lines)

| Check | Status | Evidence |
|---|---|---|
| V1 Inline provenance | ✅ PASS | 0 matches |
| V2 Vague rejection | ⚠️ MEDIUM | line 142: "Approach B was rejected as less suitable" — no specific reason given |
| V3 Idea-pool enumeration | ✅ PASS | Largest pool: 9 rows under `## Hazard Ideas` |
| V4 Line-count | ✅ PASS | 1247 ≤ 1400 |
| V5 Stale model ref | ✅ PASS | 0 matches |

### `Documentation/VisualComposer/Architecture.md` (845 lines)
... (per-doc table)
```

### Step 4: Verdict

After per-doc tables, render an overall verdict:

| Total findings | Verdict |
|---|---|
| 0 across all docs | **PASS** — workflow is calibrated; no drift detected. |
| Only LOW (1–3 total) | **PASS-D** — advisory cleanup at next doc revision; no immediate action. |
| Any HIGH OR ≥4 MEDIUM | **FLAG** — recommend reviewing the relevant skill/prompt for drift. Common causes: model regression (check `models.yaml` `doc_writer`); prompt rule weakened (check `write_doc.{design,architecture,retrospective}.md`); skill bypass (Step 7.5 not firing). |
| Any check throws errors / files unreadable | **INCOMPLETE** — fix tooling before re-running. |

### Step 5: Surface to worklog if FLAG

If verdict is FLAG, propose a worklog item naming the specific drift pattern (not the individual doc):
- *"Investigate doc-workflow drift: 4 MEDIUM `V2 Vague rejection` findings across 3 docs since 2026-05-12 — possible prompt-rule regression"*

The worklog item carries the audit date so the next investigation can compare against this baseline.

## Output discipline

This command is read-only — does NOT modify docs, does NOT modify prompts, does NOT auto-fix. The user reads the report and decides whether to fix individual docs (via doc revision pass) or fix the workflow (via prompt/skill update).
