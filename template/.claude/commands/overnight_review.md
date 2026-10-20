---
description: Resolve open overnight questions
---

# /overnight_review — answer parked overnight questions

`/overnight` parks what it cannot decide as `Q` rows in its Close docs. This command collects every row still open across all of them, closes the ones whose premise has lapsed, asks the rest in one sitting and applies the answers.

## Arguments

None required. Text after the command is the owner's notes and requests: carry them out beside Steps 1–5, and ask no question an answer in them already settles.

## Step 1 — Collect

The Overnight dir is `<VAULT_ROOT>/DevProjects/<PROJECT_NAME>/Claude/TODO/Overnight`, from `.claude/baseline.lock.json` substitutions. Vault files can be cloud placeholders, so read each `*.md` there once through the shell (`Get-Content` on Windows) before the scan, then run:

```bash
python3 .claude/hooks/overnight_ask_guard.py --open-questions
```

It prints `{docs_scanned, open, malformed, resolved, unreadable}`. Report every `unreadable` doc by path; it is never clean. No-op: `open`, `malformed` and `unreadable` all empty → print `No open overnight questions.`, then run Step 5.

## Step 2 — Close lapsed rows

For each `open` and `malformed` row, check the premise it cites against live state: the branch, commit, file, worktree or PR it names. A premise that no longer holds, or a decision already carried out, gets `Resolved: <YYYY-MM-DD> — obsolete — <current evidence>` directly under the row, at its indentation. Ask no question for it.

## Step 3 — Ask

Ask the remaining rows in batched `AskUserQuestion` calls, 4 per call, one question per row. Each question states the fork and what each option buys as a packet per `CLAUDE.core.md` §Preferences.

- Options come from the row's `Options:` text. Split on `O<n>` or `(a)` markers, else on ` / ` or `;`.
- More than 4 options: quote the full row and offer its first 4.
- A `malformed` row, or fewer than 2 parsed options: quote the raw line and offer `Still open` / `Obsolete`. The tool's free-text answer carries any other decision.
- The row's Recommendation goes first, marked "(Recommended)". `Recommendation: none` marks nothing. A recommendation matching no option is quoted in the question.
- A compound row is asked as its first decision, with the rest quoted.

## Step 4 — Apply

Re-verify each answer's target, then carry it out in this session; the owner's answer is the authorization. After it succeeds, write `Resolved: <YYYY-MM-DD> — <answer> — <outcome or commit>` directly under the row, then run `/worklog unblock <sid8> Q<n>` so the work the doc's Left undone rows parked on it becomes ready. A failed apply leaves the row open and is reported with its error. `Still open` writes nothing.

## Step 5 — Archive

After the last apply, move every doc that needs no owner action out of the Overnight dir:

```bash
python3 .claude/hooks/overnight_ask_guard.py --archive-closed
```

It moves each session's Close doc and same-session packets to `archive/` when none holds an open, malformed or unreadable row and that session is not armed, and prints `{archived, kept}` with a reason per kept doc. A kept `archive-target-exists` doc is a name collision: report it.

Final message: counts of resolved-obsolete, answered-and-applied, still-open, unreadable and archived, plus each failed apply.
