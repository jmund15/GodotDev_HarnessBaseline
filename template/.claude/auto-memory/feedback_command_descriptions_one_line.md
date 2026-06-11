---
name: Command descriptions stay to one line
description: Command descriptions get injected into the always-loaded listing; target ~90 chars line 1, action-first. Commands are slash-invoked, so verbose descriptions are pure context tax.
type: feedback
originSessionId: fe876944-6081-4c17-adf9-e9b9a4499281
---
Command descriptions (the first line of any `.claude/commands/*.md` file, or the `description:` frontmatter field if present) get injected into the session's available-skills listing every session. The matcher truncates around 95–100 chars; anything beyond is bytes paid without information delivered.

**Why:** Commands are slash-invoked from the command picker — they don't carry the keyword-surface burden that skill descriptions do (skills must auto-trigger on user prose). Verbose command descriptions are pure context tax with no discoverability payoff. User confirmed 2026-05-11: "max 1 line per command" after I left ~13 commands still truncated in the listing after a prior trim pass that was too conservative.

**How to apply:**
- Target ~90 chars for command-file line 1. Anything that would push it over ~95 should be reframed or trimmed.
- Lead with an action verb (`Audit…`, `Create…`, `Generate…`, `Run…`, `Apply…`). Don't open with a definition of the subject ("The X is the core system…"). The first-line is "what this command DOES when invoked," not "what the subject IS."
- If a WHY or extra context is genuinely useful for the user reading the file, push it to line 3+ behind a blank line. The parser stops at the first newline, so the listing only sees the tight first line.
- Skills follow a different rule: their descriptions carry auto-trigger keyword surface and may legitimately run longer. The one-line cap is command-specific.
- Empirical signal of failure: command appears in the system-reminder available-skills listing with a `...` truncation marker. That marker = wasted bytes per session.
