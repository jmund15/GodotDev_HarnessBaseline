---
disable-model-invocation: true
---

# /ingest_conversation Steps 1–3 — intake, extract, dedup

Read at the step named. Entrypoint: [`../ingest_conversation.md`](../ingest_conversation.md).

## Step 1 — Intake

Create `<vault>/Claude/Meetings/<today>-<slug>/` (slug from the argument, else derived from the conversation's dominant topic). Copy the transcript in as `transcript.md`, byte-verbatim — no cleanup, no reflow, no speaker normalization. The transcript is the evidence layer; every later quote is checked against it.

One file may concatenate many voice messages. Default to one file = one conversation unit, arc inside it read as a step-5 signal. Split into dated sibling folders only where the transcript itself marks session boundaries (datestamps, an explicit break) — the unit count is what every tier promotion is denominated in, so inflating it by guesswork manufactures patterns, and collapsing a month of separate exchanges into one hides them.

Speaker labels are load-bearing evidence — preserve them exactly as written. A transcript with no speaker labels still yields actionables; the collaboration read is then **skipped with that reason recorded in the digest**, never run on guessed attribution.

## Step 2 — Extract (dispatched, two jobs)

| label | extracts |
|---|---|
| `extract-actionables` | topic segmentation + candidate actionables |
| `extract-collab-signals` | per-speaker behavioral observations + per-topic sentiment |

Two jobs, not one prompt: an extractor also reading tone starts inferring intent from mood, and the quote-anchored ledger is the artifact that must not drift.

**Args transport:** the transcript NEVER transits Workflow `args`. The session writes one job prompt file per job under `.claude/scratch/`, each carrying the transcript's **absolute path**; the delegate `Read`s that path itself (retry once on failure). Passing transcript text through `args` corrupts it — `gotcha_workflow_args_generation_fidelity`.

**STT rule, both jobs** (`user_feedback_is_speech_to_text`): input is speech-to-text — a garbled segment with two plausible readings that imply *different work* surfaces BOTH readings. Flagged segments go to the digest's `## Ambiguous segments`, both readings written out.

### `extract-actionables`

Segment the transcript into topics; extract candidate actionables, each carrying a verbatim quote plus a transcript line anchor.

**Done when:** every transcript segment is either assigned to a topic or explicitly marked no-actionable, and every candidate carries a verbatim quote + line anchor.

### `extract-collab-signals`

Extract what each speaker DID with words, plus how each topic landed. Three rails, each a rejection at intake rather than a softening later:

- **Attribution.** Every observation names the speaker label the segment carries. A segment whose label is absent or ambiguous produces no attributed observation — it goes to `## Ambiguous segments`.
- **Behavior, never persons.** Record the observable move: *"restated the constraint three times before the topic moved on"*. Mental-state, personality-type, and clinical framings (*"gets anxious when unheard"*) are out of scope at every tier.
- **One transcript, one conversation's worth of claim.** This job sees a single transcript and holds no prior profile, so *tends to / usually / always / has a habit of* are unavailable to it. Trend language is the session's at step 5, where the prior profile is in hand.

Sentiment is per topic — one of `aligned` / `energized` / `diverging` / `unresolved friction` — each carrying the quote that shows it. No numeric scores: a scale invented from one conversation is precision the evidence cannot pay for.

**Done when:** every observation carries speaker + verbatim quote + line anchor, every topic carries a sentiment tag or an explicit `no-signal`, and no line uses cross-conversation or diagnostic language.

## Step 3 — Dedup (inside `extract-actionables`, push-don't-pull)

**The SESSION gathers the corpus first, then embeds that index in the dispatch prompt. The delegate COMPARES against what it was pushed and never discovers by search** — an intermittently-empty search reads as "nothing prior" and silently destroys the ledger's value (`gotcha_workflow_fanout_search_false_absence`).

Corpus to gather (Glob, then list paths + titles into the prompt):
- `<vault>/Claude/Meetings/*/digest.md`
- `<vault>/Claude/BrainstormingDesigns/**/ideas.md`
- `.claude/worklog-titles.md`
- Roadmap Part rows (`<vault>/Claude/**/roadmap.md` Parts tables)
- `<vault>/Claude/Meta/Development-Focus.md` — the standing focus directive; a candidate restating it
  is a ratification, not a new priority statement

`.claude/worklog-titles.md` is a **discovery index only** — a title hit is a *candidate* repeat. The session verifies against the full Obsidian `Worklog.md` before a REPEAT tag or any worklog routing sticks (`feedback_plan_worklog_items_from_source_not_mirror`).

**Done when:** every candidate carries exactly one of `NEW` / `REPEAT → <file#anchor>` / `EVOLVED → <what changed>`, and `## Dedup corpus` records the file count compared against. **Zero REPEATs against a zero-file corpus count is a failed run, not a clean one** — re-gather and re-dispatch.
