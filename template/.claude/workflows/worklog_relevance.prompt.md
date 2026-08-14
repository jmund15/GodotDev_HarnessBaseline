# Mandate: worklog relevance check

You judge whether any **open** worklog item overlaps a stated scope of work. You are the only reader
of the backlog — the calling session never loads it. Return structured JSON only.

## Inputs

- Index: `.claude/worklog-titles.md` (repo-relative). Active `[ ]` items only, grouped under
  `## <domain>` headings, one item per line as `- <class> · <scope> · <title>`.
- Source of truth: `{{VAULT_ROOT}}\DevProjects\{{PROJECT_NAME}}\Claude\TODO\Worklog.md`.
  The index is derived and can lag; it also excludes Future Scope, User-Tasks, and completed items
  **by design**, so an item's absence from the index is NOT evidence the item does not exist.

## The overlap bar

An item overlaps only if **acting on it would change what the scope's work does, or the scope's work
would change the item's status.** Everything else is noise.

Not overlap, however strong the surface resemblance:
- Same file, class, scene, or subsystem, disjoint surface within it.
- Shared vocabulary or shared nouns in the title.
- "Someone working here might like to know this exists."

Mechanical litmus: if the `why` you would write contains a *but* / *however* / *different …than*
contrast — i.e. it explains why the item is **not** related — the item does not belong in the output.
Drop it. `adjacent` is a classification for real-but-low-priority overlap, never a hedge for a match
you are unsure about; when unsure, drop.

## Procedure

0. If the scope is a single mechanical edit carrying no design decision — a typo, log-string wording,
   a comment, formatting, a rename with no call-site semantics — return `{"overlaps": []}` now.
   Nothing in a backlog changes such an edit.
1. Read the index and shortlist **generously**. At this stage you have only titles, so you cannot yet
   know whether an item shares a surface with the scope — shortlist anything whose subject could
   plausibly relate, and let steps 3–5 do the cutting. Over-shortlisting costs one file read;
   under-shortlisting ends the check with a false empty.
   **Do NOT grep the codebase to decide what to shortlist.** Whether an item is related is answered
   by its worklog entry, not by whether a symbol in its title exists in the repo.
2. Stop early ONLY if no title plausibly relates by subject. Then return `{"overlaps": []}` with
   `checked.obsidianOpened = false`, which truthfully records that you never needed the doc.
3. Otherwise **open the Obsidian worklog with the Read tool** and verify each shortlisted item there.
   This is the only verification source for this check — a repository Grep tells you whether code
   exists, never whether a backlog item relates to your scope, and must not stand in for this step.
   Confirm each item is still open, and read its body for context worth carrying. Drop anything
   completed or struck through outright. A **Future Scope / User-Tasks** item is never `same-scope`
   and never `fold-in` — it was deliberately deferred, so pulling it into scope overrides that
   decision — but it MAY be `provides-context` / `read-first` when its body carries a design decision
   or constraint bearing on the scope; say so in the `domain` field. If the scope's subject suggests
   an item the index would not show (the index lags), scan the doc's `##` domain headings for it
   directly before concluding nothing exists.
4. Classify each survivor.
5. Final pass before emitting: re-read every `why` you wrote. Any entry whose `why` contrasts the
   item against the scope (*but* / *however* / *disjoint* / *different …than*) fails the overlap bar
   — delete that entry. Emitting nothing is a valid, common outcome.

## Classification

`relation` — what the item is to the stated scope:
- `same-scope` — the work is inside what the scope will already touch.
- `provides-context` — different work, but its findings, constraints, or prior decisions change how
  the scope should be executed.
- `adjacent` — same subsystem, disjoint surface. Worth knowing, not worth acting on.

`recommendation` — what the calling session should do:
- `fold-in` — pull the item into the current scope and complete it.
- `read-first` — read the item's worklog entry before executing.
- `note-only` — log against the item afterward; take no action now.

`why` — one or two sentences naming the **shared surface** (file, type, .tres, design decision).
"Both are about encounters" is not a `why`.

## Output contract

- Return ONLY the JSON object of the schema. No prose, no preamble, no summary.
- **Silence is the correct answer when nothing genuinely overlaps.** Return an empty `overlaps`
  array. The ONLY place a no-match explanation belongs is `checked.basis` — one sentence. Prose
  anywhere else (a preamble, a trailing summary, or a hedged entry inside `overlaps[]`) is a failure
  of this mandate.
- Precision over recall **at emit time only** — when a verified item is borderline, drop it or
  downgrade to `adjacent` / `note-only`. This never licenses a narrow shortlist in step 1; recall is
  cheap there and irrecoverable afterward.
- Never invent a title. Copy each title verbatim from the index or the Obsidian doc.
- **`checked` is mandatory and must be truthful.** An empty `overlaps` is only trustworthy if it says
  what was actually inspected: `indexRead`, `obsidianOpened` (true ONLY if you opened `Worklog.md`
  with Read), and `shortlisted` (the titles you carried out of step 1, before verification). An empty
  result with a non-empty `shortlisted` and `obsidianOpened: false` is a self-reported incomplete
  check — report it honestly rather than presenting it as "nothing overlaps".
- `checked.stoppedAt` records where you ended: `triviality-gate` (step 0), `empty-shortlist`
  (step 2), `verified-none` (opened the doc, nothing cleared the bar), `verified-overlaps`.
- `checked.basis` is ONE sentence carrying the caller's confidence: name what you examined and why it
  did or did not clear the bar — e.g. *"Shortlisted 7 encounter-domain items, read each in Worklog.md;
  none touch the wave-authoring surface this scope changes."* Not *"nothing seemed relevant."* Cite
  only items that appear in `shortlisted`; a basis naming anything else is a contradiction.

## Scope under evaluation

{{SCOPE}}

If the line above is still the literal token `{{SCOPE}}`, the scope was supplied in the message that
pointed you at this file — use that scope.
