---
description: Ingest a conversation transcript into a dated Meetings digest of deduped, routed actionables plus a rolling collaboration profile.
disable-model-invocation: true
---

# /ingest_conversation

Turns a cofounder conversation transcript into a durable, deduped ledger of routed actionables, and folds the same transcript into a rolling per-participant collaboration profile.

**Vault convention:** one folder per conversation at `<vault>/Claude/Meetings/YYYY-MM-DD-<slug>/`, holding `transcript.md` (verbatim, never edited) and `digest.md` (the processed ledger). The digest corpus — never the raw transcripts — is what future ingests dedup against.

**One rolling doc** at `<vault>/Claude/Meta/Collaboration-Profile.md` accumulates across ingests: tendencies, communication, strengths, watch-fors, and per-person suggestions, each line carrying the number of distinct conversations behind it. It is **private to Jmo** — suggestions addressed to another participant are drafts for Jmo to decide whether and how to raise, never written as something that person has seen or agreed to.

## When to invoke

A conversation happened (voice note, call, live session) and its decisions, ideas, and action items need to land somewhere durable and routed. Explicit invocation only.

**Not this command:** already-converged design (`/design_drive`, `/feature_drive`), a single deferral (`/worklog`), or audio with no transcript — transcribe first, this command takes text.

---

## Arguments

`/ingest_conversation <transcript-path> [slug]` — path may point anywhere on disk. No argument → ask for the path and nothing else.

---

## Procedure

### Step 1 — Intake

Create `<vault>/Claude/Meetings/<today>-<slug>/` (slug from the argument, else derived from the conversation's dominant topic). Copy the transcript in as `transcript.md`, byte-verbatim — no cleanup, no reflow, no speaker normalization. The transcript is the evidence layer; every later quote is checked against it.

One file may concatenate many voice messages. Default to one file = one conversation unit, arc inside it read as a step-5 signal. Split into dated sibling folders only where the transcript itself marks session boundaries (datestamps, an explicit break) — the unit count is what every tier promotion is denominated in, so inflating it by guesswork manufactures patterns, and collapsing a month of separate exchanges into one hides them.

Speaker labels are load-bearing evidence — preserve them exactly as written. A transcript with no speaker labels still yields actionables; the collaboration read is then **skipped with that reason recorded in the digest**, never run on guessed attribution.

### Step 2 — Extract (dispatched, two jobs)

Dispatch via `.claude/workflows/dispatch.js` as ONE fan-out carrying two jobs, both default fan-out tier at `medium` effort (converged spec, evidence extraction — neither job renders judgment). Record both pins in the run report.

| label | extracts |
|---|---|
| `extract-actionables` | topic segmentation + candidate actionables |
| `extract-collab-signals` | per-speaker behavioral observations + per-topic sentiment |

Two jobs, not one prompt: an extractor also reading tone starts inferring intent from mood, and the quote-anchored ledger is the artifact that must not drift.

**Args transport:** the transcript NEVER transits Workflow `args`. The session writes one job prompt file per job under `.claude/scratch/`, each carrying the transcript's **absolute path**; the delegate `Read`s that path itself (retry once on failure). Passing transcript text through `args` corrupts it — `gotcha_workflow_args_generation_fidelity`.

**STT rule, both jobs** (`user_feedback_is_speech_to_text`): input is speech-to-text — a garbled segment with two plausible readings that imply *different work* surfaces BOTH readings. Flagged segments go to the digest's `## Ambiguous segments`, both readings written out.

#### `extract-actionables`

Segment the transcript into topics; extract candidate actionables, each carrying a verbatim quote plus a transcript line anchor.

**Done when:** every transcript segment is either assigned to a topic or explicitly marked no-actionable, and every candidate carries a verbatim quote + line anchor.

#### `extract-collab-signals`

Extract what each speaker DID with words, plus how each topic landed. Three rails, each a rejection at intake rather than a softening later:

- **Attribution.** Every observation names the speaker label the segment carries. A segment whose label is absent or ambiguous produces no attributed observation — it goes to `## Ambiguous segments`.
- **Behavior, never persons.** Record the observable move: *"restated the constraint three times before the topic moved on"*. Mental-state, personality-type, and clinical framings (*"gets anxious when unheard"*) are out of scope at every tier.
- **One transcript, one conversation's worth of claim.** This job sees a single transcript and holds no prior profile, so *tends to / usually / always / has a habit of* are unavailable to it. Trend language is the session's at step 5, where the prior profile is in hand.

Sentiment is per topic — one of `aligned` / `energized` / `diverging` / `unresolved friction` — each carrying the quote that shows it. No numeric scores: a scale invented from one conversation is precision the evidence cannot pay for.

**Done when:** every observation carries speaker + verbatim quote + line anchor, every topic carries a sentiment tag or an explicit `no-signal`, and no line uses cross-conversation or diagnostic language.

### Step 3 — Dedup (inside `extract-actionables`, push-don't-pull)

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

### Step 3.5 — Premise check (session model)

Both extract jobs read the transcript and nothing else. That is correct — an extractor consulting the
repo starts rewriting what people said into what it decides they meant — but it leaves the
conversation's **factual claims about the codebase** unverified, and those claims are what set scope.
Check them here, before classification turns them into staged work.

**Check premise-shaped claims only.** A premise-shaped claim asserts what exists, what is missing,
what remains to be built, or what something will cost: *"we only have one X"*, *"we'd need to build
Y"*, *"Z doesn't exist yet"*, *"that's months of work"*. Design opinions, preferences, and priority
statements carry no premise — skip them. Expect a handful per transcript, not dozens.

**A build proposal carries an unstated premise.** Any candidate proposing to build, add, or create
something asserts that it does not already exist, whether or not the transcript ever says so. Check
that assertion too — an unspoken premise sets scope exactly as hard as a spoken one, and reaches
classification with nothing having questioned it.

**Keep the checks cheap.** Existence-level evidence settles most premises: `git ls-files`,
semantic-search, a roadmap Part's state, a shipped `.tres`. Escalate to a dispatched read-only survey
only when one premise underwrites an entire staged topic.

Each checked claim resolves to exactly one verdict:

| verdict | meaning | consequence |
|---|---|---|
| `holds` | the repo agrees | candidate proceeds unchanged |
| `stale` | the repo contradicts it | **re-scope the candidate before step 4** — and its archetype may move with it, since a design topic whose design is already built is a task, not a drive |
| `unverifiable` | cheap checks cannot settle it | carry the candidate forward with the premise flagged, and record what would settle it |

**Existing is not done.** A file-existence check answers whether a thing is *named*, never whether it
is good enough to build on. Report what exists with paths and let the owner judge quality — an agent
inferring completeness from a directory listing produces the mirror of the error this step prevents.
Quality is a gate question, so it goes to the owner at step 6.

**A re-scoped candidate reaches the gate as re-scoped**, showing both the original and revised scope.
Different work at a different cost is a different approval — the same reason step 7 keeps
design-shaped actions staged rather than run.

**Done when:** every premise-shaped claim carries `holds` / `stale → <what the repo shows>` /
`unverifiable → <what would settle it>` with its evidence, `## Premise checks` records them, and
every `stale` verdict has re-scoped its candidate before step 4 classifies it.

### Step 3.7 — Outside read (session + one blind dispatch)

The extractors record what was said and the premise check verifies what was claimed; neither asks
whether the conversation *chose well*. This step does — with de-biasing that is structural, not
adjectival: something reasons about each problem before seeing the chosen answer.

1. **Assemble the contention set:** topics tagged `diverging` / `unresolved friction`, open
   questions, and any decision adopted with no competing alternative voiced in the transcript —
   cheap convergence is where blind spots hide. **Empty set → no dispatch**: write the section with
   its provenance header and a 2–3 sentence session-inline read.
2. **Write solution-stripped briefs** — one per item, one scratch file: the problem as posed,
   verbatim constraints both parties stated, premise-check repo facts. The participants' chosen
   answers are REMOVED — the blind brief is the de-biasing mechanism (the `--design_panel`
   blind-attempt structure).
3. **Dispatch ONE blind job** via dispatch.js — pin `opus` — `medium` for interpersonal/process contention, `high`/`xhigh` when a brief
   turns on repo facts the arm must discover — **never the default fan-out tier** (it fabricates on open judgment —
   CLAUDE.md ladder). Read-only; repo access allowed for feasibility. Deliverable: 2–3 directions
   per brief with tradeoffs. Record the pin in the run report.
4. **Compare and write `## Outside read`** (session): delegate directions vs what was concluded vs
   the repo. Entry tiers: `missed direction` / `misframe` / `unpriced risk` /
   `resolution proposal` (unresolved frictions only) / `concurrence` (≤2, and only carrying a
   reason absent from the transcript — else omit). Every entry cites transcript lines or repo
   paths; anything else is labeled speculation. **A null result is a valid result** — state "no
   missed direction found" plainly rather than manufacturing contrarianism.
5. **Cross-track:** an entry implying concrete work is ALSO emitted as a step-4 candidate tagged
   `outside-read`, so wins land somewhere executable and ride the same single gate.

**Done when:** every contention-set item has delegate directions compared against its conclusion
(or the inline read on an empty set), the section opens with the provenance header, every entry is
cited-or-labeled, and each work-implying entry appears among step 4's candidates.

### Step 4 — Classify (session model)

Routing judgment stays with the orchestrator. Map each candidate to exactly one archetype:

| # | Archetype | Route |
|---|---|---|
| a | focus / priority statement | `Claude/Meta/Development-Focus.md` revision proposal — the doc carries an `## At a glance` skim section under the same discipline as the digest's takeaways (step 7) |
| b | design-identity statement | `game_vision` SKILL.md edit proposal |
| c | design topic | verbatim idea-seed list + named downstream command — `/idea_brainstorm` when the space needs populating, `/design_drive` when converged |
| d | deferred task | worklog add proposal (routes per the CLAUDE.md boundary: user-owned roadmap Parts stay on `roadmap.md`; standalone user-judgment items → `User-Tasks.md`; ambiguous → regular Active) |
| e | roadmap change | [`/update_roadmap`](update_roadmap.md) proposal |
| f | cofounder-owned action item | `User-Tasks.md` proposal — only for Jmo-owned in-engine/checklist actions; both-cofounder-visible or business-level items propose a shared-board (Trello) card instead, since the cofounder doesn't read Obsidian |

**Done when:** every `extract-actionables` candidate maps to exactly one archetype or an explicit `no-route` with a reason, and the count reconciles against that job's candidate count plus any `outside-read`-tagged candidates from step 3.7.

### Step 5 — Collaboration read (session model)

Trend judgment stays with the orchestrator for the same reason routing does: it needs the corpus the delegate was never given. Read `<vault>/Claude/Meta/Collaboration-Profile.md` (create from the template below on first run), then place every `extract-collab-signals` observation against it.

**Tiers count distinct conversations, never strength of feeling.**

| tier | requires | written as |
|---|---|---|
| `observed` | 1 conversation | a datapoint — holding list only, never a trait line |
| `pattern` | ≥2 conversations, a quote from each | "tends to …" |
| `standing` | ≥4 conversations, no counterexample since the last promotion | the trait, unhedged |

An observation enters the holding list on first sighting and earns a trait line on the second — one conversation never produces a trait (`feedback_dont_codify_never_from_single_fix`).

**Demotion is mandatory.** An observation contradicting a `pattern`/`standing` line drops it one tier and lands in the trend log with both quotes. A profile that only accretes is a horoscope; the demotion path is what makes a promotion mean anything.

**Suggestions** are per person, split as-a-dev and as-a-communicator, each addressed to a behavior and traced to a `pattern`-or-better line. A suggestion with no evidenced line behind it is dropped rather than hedged.

**Cross-track:** a suggestion naming concrete repo work — the checklist, template, or harness rule that would make the habit unnecessary — is ALSO emitted as a step-4 archetype-(d) candidate, so the fix lands somewhere executable instead of dying as advice.

**Done when:** every observation is placed (holding list, promotion, demotion, or discard with a stated reason), every profile line carries its tier + conversation count, and every suggestion traces to a `pattern`-or-better line.

### Step 6 — One gate

A single `AskUserQuestion` batch over the whole proposal set — approve / reject / defer per proposal (chain batches if >4 proposals; still one gate). The step-5 profile diff rides in that batch as one item, quoting its promotions and demotions. **No secondary approvals downstream** (`feedback_single_gate_no_secondary_approval`).

### Step 7 — Execute + record

Write `digest.md` with dispositions filled in — opening with `## High-Level Takeaways`, immediately after the title paragraph and before `## Decisions`: two subsections distilling the whole digest into a <30s skim. **Game-dev-direct** — concrete game/codebase decisions and actions, each bulleted with a link to its tracked destination (the Proposals/Decisions row's actual destination file — `Worklog.md`, `User-Tasks.md`, `Development-Focus.md`, a roadmap — or the digest's own heading when nothing external tracks it). **Trello / cofounder talking points** — business, scheduling, tracking, and communication items: open questions, outside-read reframes, ventures/Trello routing, anything meant to be raised with Lorant next. Bullets only, no verbatim quotes — the ledger below still carries every citation. Each bold lead-in states the actual point in plain language by itself — never a category label ("Reframe worth raising:") or a jargon stack requiring decode ("Tier-0 invalidation-proof asset backlog"); run every bullet through `instruction_quality` §6/§6b before finalizing. Execute approved **mechanical** actions in-session: worklog adds via [`/worklog`](worklog.md), the focus-doc revision, `User-Tasks.md` adds, the `game_vision` edit if approved verbatim.

**Both living docs carry the same skim discipline.** `Meta/Development-Focus.md` and
`Meta/Collaboration-Profile.md` are read the way a digest is — opened for the answer, not the
argument — so each carries an `## At a glance` section directly under its H1 (templates below),
refreshed on every write that changes it, under the takeaways rules above: bullets only, bold
lead-in stating the actual point in plain language.

Two constraints the digest does not carry, because these sections are **derived views** rather than
new records:

- **State no fact the body below does not.** A claim appearing only in At a glance has no evidence
  behind it and no revision-log or trend-log entry to age it.
- **Carry the source line's own qualifier.** On the profile that means the tier and count ride along
  and only `pattern`-or-better lines are eligible — a skim bullet that drops the tier launders a
  single-conversation datapoint into a fact, which is what the tier system exists to prevent.

Apply the approved profile diff to `Meta/Collaboration-Profile.md` with direct `Edit` — an evidence ledger whose structure step 5 dictates, per the CLAUDE.md §9 write-routing carve-out; the `## Standing read` paragraph is the one prose block and is small enough that the round-trip costs more than the writing. Bump `updated`, increment `conversations`, and append one trend-log row per promotion, demotion, and sentiment move.

**Design-shaped actions are STAGED, never run** — the digest names the seed list and the downstream command. Running a drive inside ingest is out of scope by design: a digest gate approving "explore this design" is not a design-lock approval.

**Done when:** every approved proposal is either executed with its artifact path named, or staged with its downstream command named, the digest's disposition column has no blank cells, the profile's `conversations` count equals the number of `Meetings/*/digest.md` files whose collaboration read ran, and the digest opens with a `## High-Level Takeaways` section carrying both subsections.

---

## digest.md template

````markdown
---
date: YYYY-MM-DD
participants: Jmo, Lorant
source: voice-note | call | live
topics: <comma-separated slugs>
---

# <YYYY-MM-DD> — <conversation title>

## High-Level Takeaways

### Game-dev-direct
- <takeaway> → <link to its tracked destination, or the digest's own heading if nothing tracks it externally>

### Trello / cofounder talking points
- <takeaway — business/scheduling/tracking/communication item>

## Decisions
- <decision> — > "<verbatim supporting quote>" (transcript L<n>)

## Proposals
| Item | Archetype | Disposition | Destination |
|---|---|---|---|
| <actionable> | a–f | approved / rejected / deferred | <path or downstream command> |

## Ideas raised
- <idea> — `NEW` | `REPEAT → <file#anchor>` | `EVOLVED → <what changed>`

## Collaboration read
**Tenor:** <one line>
**Momentum:** moved — <…>; still circling — <…>

| Topic | Sentiment | Evidence |
|---|---|---|
| <topic> | aligned \| energized \| diverging \| unresolved friction | > "<quote>" (L<n>) |

| Speaker | Observation | Tier after this conversation | Evidence |
|---|---|---|---|
| <name> | <observable move> | observed \| pattern (N) \| standing (N) | > "<quote>" (L<n>) |

**Profile delta** → `Meta/Collaboration-Profile.md`
- promoted / demoted / new holding-list entry — <line>

## Outside read
> Generated by Jmo's configured agent from solution-stripped briefs (blind pass, `<model·effort>`) + repo checks — a structural, not neutral, third party.

- **<missed direction | misframe | unpriced risk | resolution proposal | concurrence>** — <the read> (L<n>; <repo path>; or *speculation*)
- *No missed direction found — <one line on why the conclusions hold>.* ← the null-result form

## Open questions
- <question>

## Ambiguous segments
- L<n> — reading A: <…> / reading B: <…> — both surfaced per the STT rule.

## Premise checks
| claim (L<n>) | verdict | evidence |
|---|---|---|
| <the conversation's factual claim about the codebase> | holds \| stale \| unverifiable | <path / roadmap Part, or what would settle it> |

Re-scoped by a `stale` verdict: <candidate> — was <original scope>, now <revised scope>.

## Dedup corpus
Compared against **N** files:
- <path>
````

---

## Development-Focus.md `## At a glance` template

Sits directly under the `# Development Focus` H1, before `## Current focus`.

````markdown
## At a glance

### Working on now
- **<the work, plainly>** — <one clause of what makes it done or why it is first> → <link to what tracks it, or `[[#Current focus|below]]`>

### Deliberately not now
- **<the thing being left alone>** — <the one-clause reason> → <link>

**Done with this phase when:** <the exit criterion, one sentence>
````

---

## Collaboration-Profile.md template

One section per name in the digest's `participants` — a third person or a live-session AI participant needs no schema change.

````markdown
---
updated: YYYY-MM-DD
conversations: N
participants: <comma-separated>
visibility: private
---

# Collaboration Profile

> Private working doc for Jmo. Every line derives from quoted transcript evidence in
> `Meetings/*/digest.md` and carries the count of distinct conversations behind it. Suggestions
> addressed to another participant are drafts for Jmo to weigh — nobody else has seen or agreed to
> them. Behavior only: what was said and done, never who someone is.

## At a glance

### What works
- **<the behavior, plainly — what it buys the collaboration>** — `pattern` (N) | `standing` (N)

### What keeps costing
- **<the behavior, plainly — what it costs>** — `pattern` (N) | `standing` (N)

### Worth trying next
> Drafts for Jmo to weigh — nobody else has seen or agreed to these.
- **<the suggestion, addressed to a behavior>** ← traces to <the line above it derives from>

**Corpus behind this doc:** N conversations · N lines at `pattern` · N at `standing`. Everything
else is a single-sighting datapoint in a holding list, and is deliberately absent from this section.

## Standing read
<3–5 sentences, rewritten each ingest: where the collaboration is, what it is good at, what it keeps paying for.>

## <Participant>
### Tendencies
- <behavior> — `pattern` (3: <slug>, <slug>, <slug>)
### Communication
### Strengths
### Watch-fors
### Suggestions
**As a dev:** <behavior-addressed suggestion> ← <the line it traces to>
**As a communicator:** <…> ← <the line it traces to>
### Holding list — `observed`, awaiting a second sighting
- <observation> — <slug> L<n>

## Together
### Recurring dynamics
### What works
### What stalls

## Trend log
| date | conversation | change |
|---|---|---|
| YYYY-MM-DD | <slug> | promoted <line> observed→pattern \| demoted <line> standing→pattern (counterexample L<n>) \| sentiment <topic> diverging→aligned |
````

---

## Constraints

- The transcript is never edited after intake — corrections go in the digest, not the evidence.
- No archetype is inferred silently: an unclassifiable candidate is `no-route` with a stated reason, never dropped.
- The profile is private to Jmo. Write suggestions aimed at another participant as drafts for Jmo to raise or not; nothing in the doc is phrased as read, agreed to, or acknowledged by anyone else.
- The profile asserts behavior only — mental state, personality type, and clinical framing stay out at every tier, including the `## Standing read` synthesis.
- The profile is derived, so a wrong line is repaired by correcting the digest evidence behind it and re-deriving, never by editing the conclusion alone.
- The outside read addresses **decisions, never persons** — equal standing across participants, structurally free to land against the gate-holder's position.
- The outside read cites the transcript, the repo, and prior digests (trend facts live there) — **never profile lines**: the digest folder is shareable with participants, the profile is not.
- Vault folders may be created programmatically; renames route through the Obsidian UI (wikilink auto-update is UI-only — `obsidian_conventions`).

## Cross-references

- [`/worklog`](worklog.md) — executor for archetype (d) adds
- [`/update_roadmap`](update_roadmap.md) — executor for archetype (e)
- [`obsidian_conventions`](../skills/obsidian_conventions/SKILL.md) — vault taxonomy; `Meetings/` is a Live-design-surface folder
