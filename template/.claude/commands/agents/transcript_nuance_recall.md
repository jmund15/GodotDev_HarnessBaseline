---
disable-model-invocation: true
---

# Transcript Nuance Recall (conditional recall layer)

<!-- Single source of truth for the implicit-signal recall pass over pre-compaction transcripts. -->
<!-- Referenced by: /autolearn (Step 0), /self_evaluate (Step 0d) -->

Complements the deterministic `.summary.json` digest. That digest is high-precision on
keyword-matched signals (`signals`, `matched_patterns`) but low-recall on IMPLICIT ones —
tonal corrections, the *why* behind a preference, cross-message themes, quiet approvals.
This pass closes that gap over the PRE-COMPACTION segments only (live context already holds
the rest).

Shared by `/autolearn` (Step 0) and `/self_evaluate` (Step 0d). In `/session_end` they run
back-to-back, so the pass is cached per session and reused — see Procedure step 0.

## Gate — run ONLY if any fires
- 3+ compactions for this session_id
- Session involved large system design / architecture refactor
- Session had redesigns, go-backs, or direction changes
- You only have post-compaction context and aren't sure what happened earlier

Clean / scope-1 / single-compaction sessions: SKIP — the regex digest suffices.

## Procedure
0. **Reuse check:** if `logs/nuance_recall_<session_id>.json` exists AND was written this
   session, load it and skip to step 4. (The b2b `/session_end` path means autolearn Phase 2
   usually populates it before self_evaluate Phase 3; re-extracting doubles the worker cost.)
1. From `logs/pre_compact.json`, list the pre-compaction backup `.jsonl` paths for this session_id.
2. For each backup: `extract_session_chat(session_path=<abs .jsonl path>, max_tokens=12000)`
   → clean user+assistant prose temp path (tool calls stripped by default — correct here).
3. ONE bundled call: `read_files(paths=[<all temp prose paths>], question=<PROMPT below>)`.
   For 3+ backups or very long sessions, set `model="kimi"` (larger comprehension budget).
   Write the returned JSON to `logs/nuance_recall_<session_id>.json` (gitignored transient).
4. Merge candidates into the signal pool at **MEDIUM confidence** (inference, not a verbatim
   keyword hit). They are subject to the SAME four-question signal-quality filter and
   Overfit-to-Specific check as every other signal before any proposal reaches review. Dedup
   against the deterministic `.summary.json` signals — drop anything already caught there.

## PROMPT (pass verbatim as the `read_files` question)

You are extracting durable-learning CANDIDATES from a software-engineering session transcript
(user + assistant prose; tool calls already stripped). Goal: RECALL of signals a keyword regex
would miss. Return ONLY a JSON array — no commentary, no markdown fences.

Find EVERY instance of the following across the ENTIRE transcript(s); do not omit any:
1. CORRECTION — user redirected/rejected/refined the approach, INCLUDING implicit or tonal ones
   ("hmm, let's not", "that feels off", "why would you…") with no explicit "use X instead of Y" phrasing.
2. PREFERENCE + REASONING — a stated convention or like/dislike AND the "why" the user gave.
3. RECURRING THEME — a preference or friction emerging across multiple messages, none decisive alone.
4. QUIET APPROVAL — user accepted a non-obvious/unusual choice without pushback, or affirmed it.

For each, emit:
{ "type": "correction|preference|theme|approval",
  "quote": "<verbatim user text — exact, never paraphrased>",
  "context": "<one sentence: what the assistant had done/proposed>",
  "inferred_rule": "<the durable rule implied, as a class-of-things principle>",
  "why": "<the reasoning the user gave, or '' if none stated>" }

Rules:
- Quote MUST be verbatim. If you cannot ground a candidate in an exact quote, DROP it — never invent.
- Cover all provided paths chronologically; you MUST scan every path, omit none.
- EXCLUDE items a keyword would already catch ("use X instead of Y", "we always do Z") — those are
  handled upstream; focus on the implicit/contextual.
- No text outside the JSON array.
