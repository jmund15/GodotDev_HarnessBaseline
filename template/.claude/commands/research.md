---
allowed-tools: Bash(bash:*), Bash(node:*), Bash(.claude/scripts/fetch_source.sh:*), Bash(python:*), Glob, Grep, Read, Write, Workflow, WebFetch, mcp__ai-worker__read_web, mcp__plugin_context7_context7__resolve-library-id, mcp__plugin_context7_context7__query-docs
disable-model-invocation: true
description: Answer listed external-fact questions with tiered citations and a version-keyed vault artifact.
---

Establish what an engine, library, runtime, or spec **actually does**, when the codebase cannot settle it. `/research` returns cited claims and leaves a dated artifact; the doctrine it runs on — trust tiers, cite-or-gap, P3 escalation, the stopping criterion — lives in [`source_trust.md`](../reference/source_trust.md) and is read by every shape below.

`/explore` establishes what the repo IS. `/research` establishes what the world outside it IS.

## When to invoke

The next step depends on an external fact: engine or library behavior, a version claim, spec text, a library evaluation, game-design prior art. Mid-execution, during debugging, or from any planning surface.

**SKIP** when the question is answerable from the repo (`/explore`), when one source settles it (the local docs cache, or `WebFetch` direct — CLAUDE.md §4's fetch order), when the output would be a decision (`/architecture_brainstorm`), or when the question needs open discovery across the whole web rather than this stack's sources — say so and hand off to the built-in deep-research workflow rather than growing toward it.

## Phase 1: Questions & Shape

### 1a. Write the question list

**Bounded questions.** List every external fact the next step consumes, one API, one behavior, or one version claim each, and drop questions nothing consumes — `does Node.Reparent keep the global transform?`, not `research Godot signals`. Store as `QUESTIONS`. A topic without answerable questions is refused the way `/explore` 1a refuses a vague `TOPIC`: ask for the questions rather than dispatching. The list is also the stopping criterion, so a missing question is a dimension nobody will cover.

### 1b. Pick the shape

**Shape A (default)** — the questions are settled-fact lookups against P1 sources.

**Shape B (`--wide`)** — pick it only when [`execution_depth.md`](../skills/_brainstorm_shared/execution_depth.md) §Mandate admission admits it, and name the fired fact in the report.

### 1c. Choose the URL set yourself

Name the pages before dispatching, seeded from the P1 lists in `source_trust.md` and its stack sibling file. Orchestrator-chosen URLs are what keep this cheap — an agent that discovers its own reading list has no bound.

---

## Phase 2: Dispatch

### Shape A — deterministic fetch, then digest

Land the bytes first. A question the project's version-pinned local docs cache covers resolves against that cache, at the path and with the builder the stack sibling of `source_trust.md` names. Everything else goes through `.claude/scripts/fetch_source.sh --dir <scratch> <1c urls...>`, which writes the artifacts and the TSV manifest Phase 3 verifies against. Answer `QUESTIONS` by quoting the landed artifacts with bounded `Grep`/`Read`, in `source_trust.md`'s claim shape — `file` = the URL cited, `artifact` = the local path quoted from.

Use `mcp__ai-worker__read_web(urls=[...], question=...)` when the answer needs cross-page synthesis. Consume its bounded answer and per-URL preflight; a summary is not a verbatim citation. The tool cannot write an arbitrary requested artifact. Preserve quotable page bytes through `fetch_source.sh` first.

If that route is unavailable, choose another eligible route explicitly or report the coverage gap. `orchestration`'s model table owns the pick.

### Shape B — three source-class lenses through the explore route

The explore engine already merges corroboration, flags cross-lens contradictions and checks evidence. That is the trust machinery: a claim `res-field` asserts alone and `res-official` contradicts surfaces as a contradiction the orchestrator must settle, which is what a tier hierarchy is for.

Dispatch the lenses as [`/explore`](explore.md) Step 2 does: one `Workflow` call to the explore engine, run directory `.claude/scratch/research_<slug>/`. Write each mandate below to its own file there and name it as that lens's `promptPath`, with `"kind": "lens"`. The context file (`contextPrefixPath`) carries `QUESTIONS`, the 1c URL set and the version pins `.claude/reference/project_stack.md` names: seeds, never caps. Consolidate as `/explore` Step 3 does; Phase 3 step 1 below settles the claims the engine could not verify.

| Lens | Source class | Tier |
|---|---|---|
| `res-official` | official docs, class reference, spec text | P1 |
| `res-source` | first-party source, release notes, changelog | P1 |
| `res-field` | issues, proposals, forums, prior-art write-ups | P3 |

Pin each lens from `orchestration`'s model table. The P tier in the table is the source class, never the model. `res-field` needs a model that can weigh conflicting reports.

### Lens mandates

```
You are res-official. You establish what the OFFICIAL documentation says about each listed question — the class reference, the API docs, the spec.

**RULES: Do NOT use TodoWrite. End your final message with the claims object as one ```` ```json ```` fence, nothing else. Read `.claude/reference/source_trust.md` and follow it — every claim cites a fetched P1 URL in `file`, names the local path it was quoted from in `artifact` when one exists, quotes verbatim in `evidence`, and ends its `claim` text with `[P1]` plus the version. Never answer from memory; unfetched is a gap, not an inference.**

## Your Scope
1. For each question, fetch the documentation that owns it: the version-pinned local docs cache where it covers the question (its path is in the stack sibling of `source_trust.md`), `mcp__plugin_context7_context7__query-docs` for a resolved library id, otherwise `.claude/scripts/fetch_source.sh <url>...` to land the bytes. `WebFetch` for a single page it cannot reach; `read_web` only when the answer needs synthesis across pages.
2. Quote the sentence that answers the question. If the page discusses the API but not the behavior asked about, that is `polarity: "unclear"` — silence in the docs is not permission to assume.
3. Note deprecations and behavior changes across versions that touch the question.

## What each finding becomes
- Documented behavior → `polarity: "exists"`, `bearing: "constraint"`, evidence = the quoted sentence.
- Docs contradict the question's premise → `bearing: "premise-contradiction"`. This is the highest-value claim you can return.
- Docs silent → `polarity: "unclear"` plus a `gaps` entry proposing the empirical test that would settle it.

## Reporting Filter
- Do NOT return general API tutorials. Only the behaviors the questions name.
- Do NOT paraphrase a doc sentence into `evidence`. Quote it.
```

```
You are res-source. You establish what the IMPLEMENTATION does — first-party source, release notes, changelogs — because docs describe intent and source describes behavior.

**RULES: Do NOT use TodoWrite. End your final message with the claims object as one ```` ```json ```` fence, nothing else. Read `.claude/reference/source_trust.md` and follow it — every claim cites a fetched P1 URL in `file`, names the local path it was quoted from in `artifact` when one exists, quotes verbatim in `evidence`, and ends its `claim` text with `[P1]` plus the version. Never answer from memory.**

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
```

```
You are res-field. You establish what practitioners REPORT — issues, proposals, forum threads, prior-art write-ups — and every one of those is P3: a pointer, never an answer.

**RULES: Do NOT use TodoWrite. End your final message with the claims object as one ```` ```json ```` fence, nothing else. Read `.claude/reference/source_trust.md` and follow it. A P3-only claim is emitted as `polarity: "unclear"` with a gap — NEVER as `exists`. Chase every hit to the P1/P2 source that owns the behavior and cite that instead; if none exists, that absence is the finding.**

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
```

---

## Phase 3: Verify & Record

1. **Audit the citations.** Write every claim that has an `artifact` as one `{"file": "<artifact>", "quote": "<evidence>"}` item in a findings file shaped as [`/review`](review.md) §The findings file describes, then run `python3 .claude/tools/quote_check.py <findings file> --root <repo root>`. The artifact paths are relative to that root, and the fetch manifest maps each one to its URL. A quote it does not print is verified. **A printed miss is escalate-and-read, never auto-gap**: a composed or cross-element quote fails a substring test exactly as a fabrication does, so open that artifact yourself and settle it. A claim with no artifact (context7, `WebSearch`) has no machine check. That is not a defect, and the claim is never reported as verified. A citation that lands on a summary of the thing rather than the thing means the run failed at its one job: re-dispatch, do not report.
2. **Adjudicate contradictions.** Read both sides, settle first-party, and name the losing lens. Tier breaks the tie: a P1 quote beats any number of P3 reports, and a P3 that survives contradiction by P1 is a gap, not a winner.
3. **Check the stopping criterion.** Every question in `QUESTIONS` is P1/P2-answered or recorded as a gap. An unanswered question that is neither is the failure this command exists to prevent.

### Artifact

Write `<vault_root>/DevProjects/<project_name>/Claude/Research/<slug>.md`, each value printed by `python3 .claude/tools/adaptation.py get <key>`. When the finding concerns a repo in `adaptation.json` `paired_repos`, write it under that repo's vault folder instead, so other projects built on it find it. Frontmatter carries `researched:`, `questions:`, and `expires-with:` (the versions the answers are true for, from the pins `.claude/reference/project_stack.md` names). Every claim line ends `— [title](url) [P1] · fetched <date>`. Gaps get their own section; a research file that hides its gaps is worse than one that reports none. Scratchpad instead of the vault only when the run is consumed in-session and nothing durable comes out of it.

### Lifecycle

The artifact is transient by design. **Promote the durable findings, then the file may be deleted.** A finding that would cost future time becomes a cold `.claude/auto-memory/archive/` gotcha; a finding that shapes design is quoted into the design doc's body. The research file itself is kept by nobody.

A research doc is **stale the moment its `expires-with` version moves** — re-run or delete it, never edit it in place. A stale research file is worse than none: it reads as current and poisons every future repo read that lands on it.

---

## Constraints

- **Full pages stay in fetched artifacts.** Read the exact spans needed to verify claims; return a bounded claims array or digest, not whole pages.
- **Pages own questions.** Give each lens only pages that own a listed question; drop a page that answers none. A second round runs only for a listed question still gapped that a named new source can close. Lenses never spawn, so a run cannot re-trigger itself.
- **The allowlist is the guardrail.** Sources outside the P1 lists of `source_trust.md` and its stack sibling get used only when the report names why.
- **No verdict, no gate.** `/research` reports facts and gaps. What to do about them belongs to the caller.
