---
disable-model-invocation: true
---

# `/codify` — landing details

Read at `/codify` §Step 6 to place and word a retirement trigger, queue a CLAUDE.md edit, and state the baseline status of a touched path. Entrypoint: [`../codify.md`](../codify.md).

## Retirement trigger

A trigger goes in `retire_when:` in the memory file's frontmatter, or a `<!-- retire-when: ... -->` or `<!-- basis: ... -->` comment on the line below a rule landing in a command, skill or `rules/` file. Name the condition that would make the rule wrong, in this order:

1. **Mechanical**, when one exists — `claude >= X`, `tool:<name> absent`, `load_census budget <name> under X`.
2. **Evidence**, when the rule rests on thin evidence or reasoning and the reversing observation is a count the escape log, yield log, review ledger or task records hold: `<!-- basis: thin|reasoned; reverses-if: <the observation, in prose>; review-when: <source> [<field>:<value>]... since:<YYYY-MM-DD> >= <N> -->`, grammar in `tools/rule_retirement.py`'s docstring. Write it only when that observation is nameable. Mechanical and evidence are the only kinds that fire on a real state change.
3. **A prose usage condition in the file**, when the rule's real failure is its own use and no log observes the usage — "re-check this once three checklists have been authored under it". No marker: a marker here is decoration.
4. **`review-by: YYYY-MM-DD`**, only when nothing observable applies. A date says the rule may be stale, never why; it is the last resort, not the default.

**Reach differs by location.** SessionStart evaluates `review-by` dates in `rule_retirement._review_scope` (CLAUDE.md, `rules/**`, top-level `auto-memory/*.md`); mechanical `retire-when` conditions require the full scan. Evidence-marker due scanning covers supported `.claude/` Markdown. Put staleness that should prompt a review on an always-loaded surface; a command rule whose condition is usage takes form 3.

`tools/rule_retirement.py` evaluates them, `/autolearn` proposes the retirement once one fires, and a trigger no kind matches is reported as malformed — it retires nothing.

CLAUDE.md and a `rules/` file may carry one file-level `review-by` comment covering every rule in the file; the always-loaded bundle is paid every session, so per-rule comments would grow it. An evidence marker is per rule.

## Queued edits

**Queue non-load-bearing CLAUDE.md edits** under `/apply_harness_edits` and its load-mode contract. Append to `.claude/pending_harness_edits.md`, quoting each `old` string verbatim and anchoring by heading, never by line number.

## Baseline status

Target's `baseline.lock.json` row has `status: tracked` → it is shared doctrine (CLAUDE.md §10). Flag for `/sync_baseline` classification, and check whether a companion new file must upstream in the same operation — a cite pushed without its target dangles in every consuming project. A memory cite is exempt: it is an evidence pointer, consumers adopt memories only by offer, and the citing rule states its load-bearing sentence itself.
