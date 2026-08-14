---
allowed-tools: Bash(bash:*), Bash(node:*), Bash(.claude/scripts/fetch_source.sh:*), Bash(python:*), Glob, Grep, Read, Write, Workflow, WebFetch, mcp__ai-worker__read_web, mcp__plugin_context7_context7__resolve-library-id, mcp__plugin_context7_context7__query-docs
disable-model-invocation: true
description: Answer listed external-fact questions with tiered citations and a version-keyed vault artifact.
---

Establish what an engine, library, runtime, or spec **actually does**, when the codebase cannot settle it. `/research` returns cited claims and leaves a dated artifact; the doctrine it runs on — trust tiers, cite-or-gap, P3 escalation, the stopping criterion — lives in [`source_trust.md`](../rules/source_trust.md) and is read by every shape below.

`/explore` establishes what the repo IS. `/research` establishes what the world outside it IS. Facts are the agent's job; decisions are not — if the output would be a choice rather than a fact, this is the wrong command.

## When to invoke

The next step depends on an external fact: engine or library behavior, a version claim, spec text, a library evaluation, game-design prior art. Mid-execution, during debugging, or from any planning surface.

**SKIP** when the question is answerable from the repo (`/explore`), when one source settles it (the Godot class cache, or `WebFetch` direct — CLAUDE.md §4's fetch order), when the output would be a decision (`/architecture_brainstorm`), or when the question needs open discovery across the whole web rather than this stack's sources — say so and hand off to the built-in deep-research workflow rather than growing toward it.

## Phase 1: Questions & Shape

### 1a. Write the question list

1–5 **answerable** questions, each one API, one behavior, or one version claim — `does Node.Reparent keep the global transform?`, not `research Godot signals`. Store as `QUESTIONS`. A topic without answerable questions is refused the way `/explore` 1a refuses a vague `TOPIC`: ask for the questions rather than dispatching. The list is also the stopping criterion, so a missing question is a dimension nobody will cover.

### 1b. Read the budget band FIRST

Find the most recent `[budget-posture]` line; absent one, read `<TEMP>/cc-cachestat-<session_id>.json`. Record the band. **Surplus → Shape A only**: a three-lens fan-out for a fact lookup is not worth plan quota either. Shape B unlocks at On-pace.

### 1c. Pick the shape

**Shape A (default)** — the questions are settled-fact lookups against P1 sources.

**Shape B (`--wide`)** — pick it only when one of these holds, and name which in the report: the question is version-contested; P1 and P3 are expected to disagree (an engine bug, "docs say X but everyone hits Y"); the answer moves an architectural decision; or Shape A returned `unclear`.

### 1d. Choose the URL set yourself

Name the pages before dispatching, seeded from `source_trust.md`'s P1 list. Orchestrator-chosen URLs are what keep this cheap — an agent that discovers its own reading list has no bound. **≤8 URLs per lens.**

---

## Phase 2: Dispatch

### Shape A — deterministic fetch, then digest

Land the bytes first. Godot class questions resolve against `.claude/cache/godot-docs/doc/classes/<Class>.xml` (`.claude/scripts/godot_docs_cache.sh` builds it; docs.godotengine.org is Cloudflare-gated and unusable). Everything else goes through `.claude/scripts/fetch_source.sh --dir <scratch> <1d urls...>`, which writes the artifacts and the TSV manifest Phase 3 verifies against. Answer `QUESTIONS` by quoting the landed artifacts with bounded `Grep`/`Read`, in `source_trust.md`'s claim shape — `file` = the URL cited, `artifact` = the local path quoted from.

`mcp__ai-worker__read_web(urls=[...], question=...)` only when the questions genuinely need synthesis ACROSS pages that no single artifact answers: it spends real dollars and its extractor silently truncates (measured 101,558 bytes → 71,478 chars), so a claim sourced through it cannot be machine-verified. When used, ask for the answer written to a scratchpad path with a bounded digest returned, so page text never lands in this context.

Sidecar unavailable or Surplus band → CLAUDE.md §9 *Offline fallback* governs the substitution; the bundling rule holds and only the executor changes.

### Shape B — three source-class lenses through the explore engine

`explore_fanout.js` already does corroboration merge, cross-lens contradiction detection, and unbacked-confidence downgrade. That IS the trust machinery — a claim asserted by `res-field` alone and contradicted by `res-official` surfaces as a contradiction the orchestrator must settle, which is exactly what a tier hierarchy is for.

```
Workflow({scriptPath: ".claude/workflows/explore_fanout.js", args: {
  lenses: [{key, promptPath, model, effort}], contextPrefixPath: <ctx>, labelPrefix: "research"}})
```

Write each resolved mandate below to its own scratchpad file and pass `promptPath`. The CONTEXT file carries `QUESTIONS`, the 1d URL set, and the version pins (engine per `.claude/reference/project_stack.md`, .NET 9) — seeds, never caps.

| Lens | Source class | Tier | Primary pin | Anthropic fallback |
|---|---|---|---|---|
| `res-official` | official docs, class reference, spec text | P1 | sidecar `flash·low` | `sonnet·medium` |
| `res-source` | first-party source, release notes, changelog | P1 | sidecar `flash·low` | `sonnet·medium` |
| `res-field` | issues, proposals, forums, prior-art write-ups | P3 | `opus·low` | — |

`res-field` pins `opus·low` on both providers: its whole job is judging noise, which is open-surface judgment.

### Lens mandates

```
You are res-official. You establish what the OFFICIAL documentation says about each listed question — the class reference, the API docs, the spec.

**RULES: Do NOT use TodoWrite. Return the claims schema ONLY. Read `.claude/rules/source_trust.md` and follow it — every claim cites a fetched P1 URL in `file`, names the local path it was quoted from in `artifact` when one exists, quotes verbatim in `evidence`, and ends its `claim` text with `[P1]` plus the version. Never answer from memory; unfetched is a gap, not an inference.**

## Your Scope
1. For each question, fetch the documentation that owns it: Godot classes from `.claude/cache/godot-docs/doc/classes/<Class>.xml` (docs.godotengine.org is Cloudflare-gated and unusable), `mcp__plugin_context7_context7__query-docs` for a resolved library id, otherwise `.claude/scripts/fetch_source.sh <url>...` to land the bytes. `WebFetch` for a single page it cannot reach; `read_web` only when the answer needs synthesis across pages.
2. Quote the sentence that answers the question. If the page discusses the API but not the behavior asked about, that is `polarity: "unclear"` — silence in the docs is not permission to assume.
3. Note deprecations and behavior changes across versions that touch the question.

## What each finding becomes
- Documented behavior → `polarity: "exists"`, `bearing: "constraint"`, evidence = the quoted sentence.
- Docs contradict the question's premise → `bearing: "premise-contradiction"`. This is the highest-value claim you can return.
- Docs silent → `polarity: "unclear"` plus a `gaps` entry proposing the empirical test that would settle it.

## Reporting Filter
- Do NOT return general API tutorials. Only the behaviors the questions name.
- Do NOT paraphrase a doc sentence into `evidence`. Quote it.

{{CONTEXT}}
```

```
You are res-source. You establish what the IMPLEMENTATION does — first-party source, release notes, changelogs — because docs describe intent and source describes behavior.

**RULES: Do NOT use TodoWrite. Return the claims schema ONLY. Read `.claude/rules/source_trust.md` and follow it — every claim cites a fetched P1 URL in `file`, names the local path it was quoted from in `artifact` when one exists, quotes verbatim in `evidence`, and ends its `claim` text with `[P1]` plus the version. Never answer from memory.**

## Your Scope
1. Read the actual source on `raw.githubusercontent.com` via `.claude/scripts/fetch_source.sh` (browse/tree URLs have no raw form — use as-is) so every quote is checkable against landed bytes, plus the changelog or release notes for the version in the CONTEXT pins.
2. Quote the function, signature, or changelog line that answers the question.
3. Say which version tag or branch you read. Source read off a default branch is not source read off the shipped version — state which you have.

## What each finding becomes
- Source confirms the documented behavior → `polarity: "exists"`, evidence = the quoted lines.
- Source and docs diverge → `bearing: "premise-contradiction"`, and quote BOTH so the orchestrator can adjudicate.
- The behavior is not reachable in the source you can read → `polarity: "unclear"` plus a gap naming the file you could not reach.

## Reporting Filter
- Do NOT infer behavior from a function name. Quote the body or say you could not read it.
- DO use the same `subject` string `res-official` would use for the same API — the engine groups on it to detect corroboration and contradiction.

{{CONTEXT}}
```

```
You are res-field. You establish what practitioners REPORT — issues, proposals, forum threads, prior-art write-ups — and every one of those is P3: a pointer, never an answer.

**RULES: Do NOT use TodoWrite. Return the claims schema ONLY. Read `.claude/rules/source_trust.md` and follow it. A P3-only claim is emitted as `polarity: "unclear"` with a gap — NEVER as `exists`. Chase every hit to the P1/P2 source that owns the behavior and cite that instead; if none exists, that absence is the finding.**

## Your Scope
1. For each question, find field reports of the actual behavior — engine bug trackers, proposals, threads describing what people hit.
2. Escalate: from the report, find the commit, the changelog entry, or the doc page that owns it. Cite the owner, tag the claim `[P1]`/`[P2]`, and name the field report as corroboration in the claim text.
3. Separate "several independent reports" from "one report quoted three times" — say which you have.

## What each finding becomes
- A field report escalated to its owning source → `polarity: "exists"`, tier tag of the OWNER, not the report.
- A widely-reported behavior with no owning source → `polarity: "unclear"`, `[P3]`, plus a gap. This is the honest answer and it is useful.
- Reports contradicting the official docs → `bearing: "premise-contradiction"`, both sides quoted.

## Reporting Filter
- Do NOT return a claim whose only backing is a forum post asserted as fact.
- Do NOT report volume of discussion. Report the behavior and what owns it.

{{CONTEXT}}
```

---

## Phase 3: Verify & Record

1. **Audit the citations.** Run `python .claude/tools/verify_claims.py <claims.json> --manifest <manifest.tsv>` over the Phase 2 fetch manifest. VERIFIED needs nothing further. **UNVERIFIED is escalate-and-read, never auto-gap** — a composed or cross-element quote fails a substring test exactly as a fabrication does, so open that artifact yourself and settle it. NOT-APPLICABLE (context7, WebSearch, no local artifact) is out of scope, not a defect. A citation that lands on a summary of the thing rather than the thing means the run failed at its one job — re-dispatch, do not report.
2. **Adjudicate contradictions.** Read both sides, settle first-party, and name the losing lens. Tier breaks the tie: a P1 quote beats any number of P3 reports, and a P3 that survives contradiction by P1 is a gap, not a winner.
3. **Check the stopping criterion.** Every question in `QUESTIONS` is P1/P2-answered or recorded as a gap. An unanswered question that is neither is the failure this command exists to prevent.

### Artifact

Write `Claude/Research/<slug>.md` in the vault — `{{PROJECT_NAME}}/` by default, `Jmodot/` when the finding would be useful in another game built on Jmodot. Frontmatter carries `researched:`, `questions:`, and `expires-with:` (the version the answers are true for — the engine pin from `.claude/reference/project_stack.md`, `.NET 9`, `GdUnit4 5.x`). Every claim line ends `— [title](url) [P1] · fetched <date>`. Gaps get their own section; a research file that hides its gaps is worse than one that reports none. Scratchpad instead of the vault only when the run is consumed in-session and nothing durable comes out of it.

### Lifecycle

The artifact is transient by design. **Promote the durable findings, then the file may be deleted.** A finding that would cost future time becomes a cold `.claude/auto-memory/archive/` gotcha; a finding that shapes design is quoted into the design doc's body. The research file itself is kept by nobody.

A research doc is **stale the moment its `expires-with` version moves** — re-run or delete it, never edit it in place. A stale research file is worse than none: it reads as current and poisons every future repo read that lands on it.

---

## Constraints

- **Page text never enters this context.** Fetch and digest are worker-tier by construction; the return path is a bounded claims array or a digest, never prose pages.
- **Hard caps: ≤8 URLs per lens, ≤3 lenses, one round.** A follow-up round needs the user, not a self-decision. Lenses are read-only and spawn nothing, so a run cannot re-trigger itself.
- **The allowlist is the guardrail.** Sources outside `source_trust.md`'s P1 list get used only when the report names why, and open web discovery is the built-in workflow's job, not this one's.
- **No verdict, no gate.** `/research` reports facts and gaps. What to do about them belongs to the caller.
