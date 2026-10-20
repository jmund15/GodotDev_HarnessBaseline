---
disable-model-invocation: true
---

# `/codify` — memory landing

Read at `/codify` §Step 6 when §Step 4 routes an item to auto-memory (`.claude/auto-memory/`).

## Memory landing

**Step 1: Extend an existing topic file, or create one — in that order.**

**Default to extending. Search before you create.** Semantic-search the store for the concern, and read the `MEMORY.md` line your pointer would join. A new file is correct only when no existing topic file owns this concern.

**The index line is the tell.** Append your rule as a numbered entry in the topic file that [Brevity budget](#brevity-budget) selects. Retroactive cleanup: `/memory_audit` lens 5.

- Extends an existing topic file → append to its body; **no new `MEMORY.md` link**.
- Genuinely new concept → create `<slug>.md` (frontmatter: `name`, `description`, `metadata.type` of user|feedback|project|reference), and cross-link related topic files with `[[other-file-slug]]` wikilinks in the body.
- **`How to apply:` points at the owner.** When a harness file owns the procedure, the line names that file and section and never restates the procedure: a restated copy drifts from its owner. `/memory_audit` lens 7 checks the pairing.

Admission, tier and placement are `CLAUDE.core.md` §2's and [`/codify`](../codify.md) §Step 4–§Step 5's. **A note an SSOT already states lands nothing.** When `CLAUDE*.md`, a `rules/` file, a skill or command, a hook deny or the system prompt carries the decision in full, write no note. A clause that owner lacks (the non-obvious why, an unreplicated how) folds into the owner, or stays as one cold line when no owner fits; incident narrative alone is commit evidence. The same test deletes an existing note.

Never store a large code block in auto-memory: link to its source.

**Step 2: Catalog the failure mode (if applicable)**
When the new entity codifies a **recurring failure pattern** (regression class, bug shape, mistake-shape with detection signal), propose a corresponding entry in [`checklists/known_failure_modes.md`](../checklists/known_failure_modes.md). The catalog is the **detection-pattern layer** that critic agents fetch per entry on demand, such as the coding layer's plan-review `plc-memory-alignment` and explore `exp-memory` lenses.

Trigger: the new entity describes a *bug class* (not a *preference* or *fact*). Litmus — would another future occurrence of this class be catchable by a grep regex, LSP query, or structural pattern? If yes, the catalog entry adds value.

Entry format, ID and placement: [`checklists/known_failure_modes.md`](../checklists/known_failure_modes.md) §How to add an entry (autolearn) — `kfm.py` parses the five lines, so a malformed entry silently vanishes from the index.

An entry with no concrete detection signal waits for the next pass, when the pattern surfaces a second time and the signal becomes clearer. Catalog entries are append-only; manual sweeps are not part of a landing.

## Brevity budget

The `MEMORY.md` index auto-loads into every session; its 200-line/25KB cap is `CLAUDE.core.md` §2's. Cold `archive/` files are search-only, so length matters far less there. Three litmus tests sit inside the cap:

- An index line carries one rule; a second comma-label means a second line.
- A line whose prefix already names the topic takes the rule into that topic file, not a new link. The index hits its byte cap through link count, so a store of individually-compliant entries can still blow it.
- A hot body holds the rule, its why, and a `How to apply:` pointer; anything more is evidence for `archive/`.

A hot index line targets 120 characters, derived from the runtime pair: 25KB over 200 lines is about 128 bytes per line, so a line near 120 characters exhausts both caps together.

A budget violation on a hot entry is an audit signal: merge genuinely redundant principles, demote bulk detail to `archive/`, or keep a larger faithful rule with a recorded reason. Run the focused audit after the addition.

## Anti-patterns in memory-entry prose

Don't write observations in these shapes:

- **Stacked errata.** "CORRECTION 2026-04-30 (supersedes obs #1 + #3): ..." — *consume* the correction by editing obs 1+3 in place; never leave both versions.
- **Incremental followups.** "EXPANDED PATTERN (2026-04-26 followup): the int-Export bug ALSO bites bool fields ..." — *merge* into one general rule covering all variants the day the second variant lands.
- **Runbook-shaped entries.** Symptom, Detection, Fix, Recovery and Prevention sections are documentation. Write a cold `archive/` file or a skill section instead, then a one-line `MEMORY.md` pointer if it warrants hot-tier surfacing.

Restatement loops, historical narration and overfit names in the rule prose are `instruction_quality` §6's cut list.
