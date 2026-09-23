---
disable-model-invocation: true
---

# /ingest_conversation Steps 3.5–3.7 — premise check and outside read

Read at the step named. Entrypoint: [`../ingest_conversation.md`](../ingest_conversation.md).

## Step 3.5 — Premise check (session model)

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

## Step 3.7 — Outside read (session + one blind dispatch)

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
3. **Dispatch ONE blind job** via dispatch.js — pin the executor tier — `medium` for interpersonal/process contention, `high`/`xhigh` when a brief
   turns on repo facts the arm must discover — **never the default fan-out tier** (it fabricates on open judgment —
   per the ladder, `reference/model_ladder_evidence.md`). Read-only; repo access allowed for feasibility. Deliverable: 2–3 directions
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
