---
description: Ingest a conversation transcript into a dated Meetings digest of deduped, routed actionables plus a rolling collaboration profile.
disable-model-invocation: true
---

# /ingest_conversation

Turns a collaborator conversation transcript into a durable, deduped ledger of routed actionables, and folds the same transcript into a rolling per-participant collaboration profile.

**Vault convention:** one folder per conversation at `<vault>/Claude/Meetings/YYYY-MM-DD-<slug>/`, holding `transcript.md` (verbatim) and `digest.md` (the processed ledger). The digest corpus — never the raw transcripts — is what future ingests dedup against.

**One rolling doc** at `<vault>/Claude/Meta/Collaboration-Profile.md` accumulates across ingests: tendencies, communication, strengths, watch-fors, and per-person suggestions, each line carrying the number of distinct conversations behind it.

## When to invoke

A conversation happened (voice note, call, live session) and its decisions, ideas, and action items need to land somewhere durable and routed. Explicit invocation only.

**Not this command:** already-converged design (the project's design or feature drive), a single deferral (`/worklog`), or audio with no transcript — transcribe first, this command takes text.

---

## Arguments

`/ingest_conversation <transcript-path> [slug]` — path may point anywhere on disk. No argument → ask for the path and nothing else.

---

## Procedure

Seven steps in order. Read each step's reference file at the step that runs it — none of it is needed before then.

1. **Step 1 — Intake.** Create the dated Meetings folder and copy the transcript in byte-verbatim; speaker labels decide whether the collaboration read runs at all — [`reference/ingest_conversation_extract.md`](reference/ingest_conversation_extract.md) §Step 1 — Intake.
2. **Step 2 — Extract.** ONE `.claude/workflows/dispatch.js` fan-out carrying two jobs, both `agentType: "general-purpose"` at the default fan-out tier with `medium` effort (converged spec, evidence extraction — neither job renders judgment); record both pins in the run report. Job specs, the args-transport rule and the STT rule — [`reference/ingest_conversation_extract.md`](reference/ingest_conversation_extract.md) §Step 2 — Extract.
3. **Step 3 — Dedup.** The session gathers the corpus and pushes it into the dispatch prompt; the delegate never discovers by search — [`reference/ingest_conversation_extract.md`](reference/ingest_conversation_extract.md) §Step 3 — Dedup.
4. **Step 3.5 — Premise check** (session model). Verify the conversation's factual claims about the codebase before classification turns them into staged work — [`reference/ingest_conversation_verify.md`](reference/ingest_conversation_verify.md) §Step 3.5 — Premise check.
5. **Step 3.7 — Outside read** (session + one blind dispatch). Solution-stripped briefs, one blind job, compared against what the conversation concluded — [`reference/ingest_conversation_verify.md`](reference/ingest_conversation_verify.md) §Step 3.7 — Outside read.
6. **Steps 4–6 — Classify, collaboration read, one gate.** Archetype routing and trend judgment stay with the session model; a single `AskUserQuestion` batch approves the whole proposal set — [`reference/ingest_conversation_synthesis.md`](reference/ingest_conversation_synthesis.md) §Step 4 — Classify, §Step 5 — Collaboration read, §Step 6 — One gate.
7. **Step 7 — Execute + record.** Write the digest, apply the approved profile diff, execute the approved mechanical actions — [`reference/ingest_conversation_synthesis.md`](reference/ingest_conversation_synthesis.md) §Step 7 — Execute + record; document shapes in [`reference/ingest_conversation_templates.md`](reference/ingest_conversation_templates.md).

---

## Constraints

- The transcript is never edited after intake — corrections go in the digest, not the evidence.
- No archetype is inferred silently: an unclassifiable candidate is `no-route` with a stated reason, never dropped.
- **Design-shaped actions are STAGED, never run** — the digest names the seed list and the downstream command. Running a drive inside ingest is out of scope by design: a digest gate approving "explore this design" is not a design-lock approval.
- The profile is private to the owner. Write suggestions aimed at another participant as drafts for the owner to raise or not; nothing in the doc is phrased as read, agreed to, or acknowledged by anyone else.
- The profile asserts behavior only — mental state, personality type, and clinical framing stay out at every tier, including the `## Standing read` synthesis.
- The profile is derived, so a wrong line is repaired by correcting the digest evidence behind it and re-deriving, never by editing the conclusion alone.
- The outside read addresses **decisions, never persons** — equal standing across participants, structurally free to land against the gate-holder's position.
- The outside read cites the transcript, the repo, and prior digests (trend facts live there) — **never profile lines**: the digest folder is shareable with participants, the profile is not.
- Vault folders may be created programmatically; renames route through the Obsidian UI (wikilink auto-update is UI-only — `obsidian_conventions`).

## Cross-references

- [`/worklog`](worklog.md) — executor for archetype (d) adds
- The project's roadmap-update command, where the coding layer ships one — executor for archetype (e)
- [`obsidian_conventions`](../skills/obsidian_conventions/SKILL.md) — vault taxonomy; `Meetings/` is a Live-design-surface folder
