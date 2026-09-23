---
disable-model-invocation: true
---

# /ingest_conversation Steps 4–7 — classify, collaboration read, gate, execute

Read at the step named. Entrypoint: [`../ingest_conversation.md`](../ingest_conversation.md).

## Step 4 — Classify (session model)

Routing judgment stays with the orchestrator. Map each candidate to exactly one archetype:

| # | Archetype | Route |
|---|---|---|
| a | focus / priority statement | `Claude/Meta/Development-Focus.md` revision proposal — the doc carries an `## At a glance` skim section under the same discipline as the digest's takeaways (step 7) |
| b | design-identity statement | edit proposal to the project's vision owner, the skill or doc its CLAUDE files name for product direction |
| c | design topic | verbatim idea-seed list + named downstream command — the project's idea brainstorm when the space needs populating, its design drive when converged |
| d | deferred task | worklog add proposal (routes per the CLAUDE.md boundary: user-owned roadmap Parts stay on `roadmap.md`; standalone user-judgment items → `User-Tasks.md`; ambiguous → regular Active) |
| e | roadmap change | roadmap-update proposal for the project's roadmap command |
| f | collaborator-owned action item | `User-Tasks.md` proposal — only for owner-owned in-engine/checklist actions; items every collaborator must see, or business-level items, propose a shared-board card instead, since collaborators may not read the vault |

**Done when:** every `extract-actionables` candidate maps to exactly one archetype or an explicit `no-route` with a reason, and the count reconciles against that job's candidate count plus any `outside-read`-tagged candidates from step 3.7.

## Step 5 — Collaboration read (session model)

Trend judgment stays with the orchestrator for the same reason routing does: it needs the corpus the delegate was never given. Read `<vault>/Claude/Meta/Collaboration-Profile.md` (create from the template in [`ingest_conversation_templates.md`](ingest_conversation_templates.md) §Collaboration-Profile.md template on first run), then place every `extract-collab-signals` observation against it.

**Tiers count distinct conversations, never strength of feeling.**

| tier | requires | written as |
|---|---|---|
| `observed` | 1 conversation | a datapoint — holding list only, never a trait line |
| `pattern` | ≥2 conversations, a quote from each | "tends to …" |
| `standing` | ≥4 conversations, no counterexample since the last promotion | the trait, unhedged |

An observation enters the holding list on first sighting and earns a trait line on the second — one conversation never produces a trait.

**Demotion is mandatory.** An observation contradicting a `pattern`/`standing` line drops it one tier and lands in the trend log with both quotes. A profile that only accretes is a horoscope; the demotion path is what makes a promotion mean anything.

**Suggestions** are per person, split as-a-dev and as-a-communicator, each addressed to a behavior and traced to a `pattern`-or-better line. A suggestion with no evidenced line behind it is dropped rather than hedged.

**Cross-track:** a suggestion naming concrete repo work — the checklist, template, or harness rule that would make the habit unnecessary — is ALSO emitted as a step-4 archetype-(d) candidate, so the fix lands somewhere executable instead of dying as advice.

**Done when:** every observation is placed (holding list, promotion, demotion, or discard with a stated reason), every profile line carries its tier + conversation count, and every suggestion traces to a `pattern`-or-better line.

## Step 6 — One gate

A single `AskUserQuestion` batch over the whole proposal set — approve / reject / defer per proposal (chain batches if >4 proposals; still one gate). The step-5 profile diff rides in that batch as one item, quoting its promotions and demotions. **No secondary approvals downstream** (`feedback_single_gate_no_secondary_approval`).

## Step 7 — Execute + record

Write `digest.md` ([`ingest_conversation_templates.md`](ingest_conversation_templates.md) §digest.md template) with dispositions filled in — opening with `## High-Level Takeaways`, immediately after the title paragraph and before `## Decisions`: two subsections distilling the whole digest into a <30s skim. **Project-direct** — concrete project/codebase decisions and actions, each bulleted with a link to its tracked destination (the Proposals/Decisions row's actual destination file — `Worklog.md`, `User-Tasks.md`, `Development-Focus.md`, a roadmap — or the digest's own heading when nothing external tracks it). **Shared-board / collaborator talking points** — business, scheduling, tracking, and communication items: open questions, outside-read reframes, shared-board routing, anything meant to be raised with a collaborator next. Bullets only, no verbatim quotes — the ledger below still carries every citation. Each bold lead-in states the actual point in plain language by itself — never a category label ("Reframe worth raising:") or a jargon stack requiring decode ("Tier-0 invalidation-proof asset backlog"); run every bullet through `instruction_quality` §6/§6b before finalizing. Execute approved **mechanical** actions in-session: worklog adds via [`/worklog`](../worklog.md), the focus-doc revision, `User-Tasks.md` adds, the vision-owner edit if approved verbatim.

**Both living docs carry the same skim discipline.** `Meta/Development-Focus.md` and
`Meta/Collaboration-Profile.md` are read the way a digest is — opened for the answer, not the
argument — so each carries an `## At a glance` section directly under its H1 ([`ingest_conversation_templates.md`](ingest_conversation_templates.md)),
refreshed on every write that changes it, under the takeaways rules above: bullets only, bold
lead-in stating the actual point in plain language.

Two constraints the digest does not carry, because these sections are **derived views** rather than
new records:

- **State no fact the body below does not.** A claim appearing only in At a glance has no evidence
  behind it and no revision-log or trend-log entry to age it.
- **Carry the source line's own qualifier.** On the profile that means the tier and count ride along
  and only `pattern`-or-better lines are eligible — a skim bullet that drops the tier launders a
  single-conversation datapoint into a fact, which is what the tier system exists to prevent.

Apply the approved profile diff to `Meta/Collaboration-Profile.md` with direct `Edit` — an evidence ledger whose structure step 5 dictates, per the CLAUDE.md §Tool Routing write-routing carve-out; the `## Standing read` paragraph is the one prose block and is small enough that the round-trip costs more than the writing. Bump `updated`, increment `conversations`, and append one trend-log row per promotion, demotion, and sentiment move.

**Done when:** every approved proposal is either executed with its artifact path named, or staged with its downstream command named, the digest's disposition column has no blank cells, the profile's `conversations` count equals the number of `Meetings/*/digest.md` files whose collaboration read ran, and the digest opens with a `## High-Level Takeaways` section carrying both subsections.
