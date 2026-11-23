---
name: worklog_reference
description: >-
  Use to decide worklog routing for deferrals, follow-ups, out-of-scope candidates or completion signals. SKIP executing worklog operations; use /worklog.
---

# Worklog Reference

The decision half of the worklog: whether surfaced work is logged, and its class, scope, readiness and domain. `/worklog` executes every write and owns the files, tiers and forms; reach for it to act.

**Plan from the block, never the mirror line.** The mirror (`.claude/worklog-titles.md`) is a title index. When planning or scoping a logged item, read its full `Context` / `Where` / `Source` block in Worklog.md: a title naming one concept can sit over a Context about another subsystem.

**Overlap discovery is a dispatch, not a skim.** The backlog is not in session context. To learn whether an open item overlaps work about to start, run `.claude/workflows/worklog_relevance.js`; `CLAUDE.core.md` §The Worklog says when.

## Classification

Required on every item. Pick the best match; ask the owner when it is genuinely ambiguous.

| Class | When to use |
|-------|-------------|
| `fix` | Known bug, known or quickly-determinable repair path |
| `debug` | Unknown root cause, investigation needed before any fix |
| `feature` | New behavior, capability, or content |
| `refactor` | Restructure with no behavior change |
| `test` | Add or expand test coverage |
| `docs` | Documentation-only update (skill, README, design doc tweak) |
| `chore` | Tooling, config, build, data-file maintenance |
| `design` | Brainstorming/planning, no implementation yet |

When an item's nature changes (a `debug` resolves into a known cause), update its class in place; never open a second entry.

**Title the diagnosis, not the presumed fix.** Without direct evidence isolating the cause, the item is `debug` and its title names the bisection, not a hypothesized remedy. A title like "isolate X" commits a future session to one mechanism; put the candidate mechanisms in Context as branches. Check whether the project already attributes a symptom code or error string to more than one cause before naming a fix.

Class cues at log time: "investigate / why does X / pre-existing failure" → `debug`; "refactor / extract / consolidate" → `refactor`; "add test for" → `test`; "document / fix doc reference" → `docs`; "wire X / extend Y / new system" → `feature`; "plan / design / brainstorm" → `design`; "breaks / regression" with a known cause → `fix`.

## Scope

Required on every item: a coarse effort signal, never a deadline. Scope comes from this table, not from the class.

| Scope | Meaning | Heuristic |
|-------|---------|-----------|
| **1** | Mechanical, locally verifiable | A small change with an obvious check |
| **2** | Focused work with no unsettled design fork | A coherent change with a clear approach |
| **3** | A bounded change with real cross-file trade-offs | The approach needs evidence and trade-off resolution |
| **4** | A design-track feature or system with a linked planning doc | The work needs a separately approved design and implementation track |

A scope-4 item is a one-line pointer with a `Plan doc:` field; its design lives in a sibling TODO/<name>.md listed under the worklog's `## Linked Docs`, never in the block. When a TODO/ doc is created, deleted or merged, its Linked Docs entry follows.

## Readiness (`When:`)

Whether the item can be worked now; distinct from scope.

| Value | Meaning | Tier |
|-------|---------|------|
| *(omitted)* | Ready, actionable now | `## Active` |
| `after <condition>` | Waits on a prerequisite or phase gate, written as free text (another item's title, a milestone, an outside event) | `## Ledger` until the condition clears |
| `future` | A distant-horizon deferral with no trigger | `## Future Scope`, one line, outside the mirror |

**`future` only when all four hold:** no trigger condition can be written; it will not plausibly come up next session; the revisit cadence is months; and parking it loses nothing an agent would need to surface it. When one fails, it is a ready Active item. A hidden, forgotten item is a permanent leak; a too-visible one costs one mirror line. Many `future` items mean some should be deleted or promoted to a Linked Doc.

## Canonical domain list

The domain is the item's `### <Domain>` heading in Active and Ledger. Its rows are this table's cross-cutting rows plus the project's own, printed by `python3 .claude/tools/adaptation.py get worklog_domains` as `[long form, short form]` pairs. Pick the closest row; use `Other` before inventing a domain, and surface a new one to the owner first.

| Long form (Active section header) | Short form (mirror line) |
|-----------------------------------|--------------------------|
| Tooling / Workflow | `tooling` |
| Documentation | `documentation` |
| Other | `other` |

## Full Trigger Catalog

### Do-now gate (runs before any logging)

**Relevant work surfaced mid-session is done now by default.** Do it inline when it is smaller than its handoff; otherwise dispatch it through `orchestration` in the background and keep the main task moving. Never ask "do now or log?". Needing an agent, touching many files, taking longer than expected or sitting off the session's topic are not defer reasons.

Defer only for one of these, and name it in the entry's Context:
1. **Owner judgment** — ask the owner about a fork blocking the current task. If they defer owner-only work, route its concrete action to User-Tasks (or leave a roadmap-owned Part in `user-owned`); do not create a Claude-ready Worklog item for the unanswered question.
2. **External block** — git topology (merge order, another branch or worktree), an unreleased model, quota budget, or a peer's file under live edit. Route with `When: after <condition>`.
3. **Design first** — scope 4 with no design doc. Route it to `/plan`.
4. **Owner deferral** — the owner said defer, or typed `/worklog add`.
5. **Collision** — the work writes into the current task's write set or a gate in progress. Do it once that closes, in this session when it can.
6. **Own plan** — the work, below scope 4, needs its own design or plan and lies outside the session's goal. Log it with its measured size and name it in the reply; it joins the current drive only when the owner adds it. Work the goal covers is done or dispatched in this session, however much design it needs.

A defect named in a turn ends that turn fixed, dispatched, logged with one of these reasons, or declined with a reason and an offer.

### Regular deferral (→ Active, no `When:`)

Each phrase sends the item through the Do-now gate first; it is logged only when a defer reason holds.

- **Said by the agent:** hedged cleanup ("could be cleaner", "not pretty but works"); scope trims ("for now I'll just…", "out of scope", "deferring this"); future intentions ("we should also…", "we'll come back to this", "next pass should…"); plan markers (`[Deferred]`, `DEFERRED:`, `TODO:`, `FIXME:` in plans, summaries or review notes); and a noted smell, refactor opportunity, dead-code candidate, missing test or stale doc left unfixed this turn.
- **Said by the owner:** "defer", "punt", "park", "later", "follow up"; "remind me to…", "don't forget…"; "next pass", "another time", "not now"; removing items from a plan while scoping.

### Conditional gate (→ Ledger, `When: after <condition>`)

"After X ships", "once Y is done", "when Z lands", "blocked on W", or a deferral naming a prerequisite PR or system: capture the prerequisite as the condition. Litmus: can a one-line condition, once true, make the item ready? Yes → `after`; no → ready or `future`.

### Future Scope (→ `When: future`)

A stronger threshold: only distant-horizon phrases with no trigger. Agent: "eventually we should…", "long-term we'd want…", "down the road…", "if/when we ever…", "no urgency…", "worth revisiting in N months". Owner: "eventually", "someday", "long-term", "down the road", "wishlist". Also an item whose Context reads as a standing observation ("watch X", "re-evaluate when Y matures").

**Not Future Scope:** "later", "next pass", "follow-up", "for now", "out of scope". These are regular deferrals that stay visible. When unsure, ask; the proposal names "Future Scope" (`/worklog` §add).

### User-Tasks (→ User-Tasks.md, outside every agent pass)

A high-confidence threshold: work Claude fundamentally cannot do, such as production art, feel-driven tuning, brainstorms whose output is the owner's taste, or design audits against the owner's vision. Read [`reference/user_tasks_triggers.md`](reference/user_tasks_triggers.md) to classify a candidate; it holds each class's signals and litmus, the route for user-executed roadmap Parts and the anti-triggers.

A change that landed on the default branch without a PR, and whose open question is feel, gets one short playtest item listing its checks; a change its tests settle gets none.

**When in doubt, a regular Active item.** Routing to User-Tasks by mistake is a permanent leak, since sweep, triage and drive never read it; routing to Active by mistake costs one mirror line. When unsure whether something belongs in the worklog at all, ask: a redundant prompt is cheap and a missed entry is lost.

**`add` or `user-add`.** Litmus: if Claude wrote a perfect plan for it and the owner said go, would the result be a working artifact? Yes → `add`, including a `design` item with technical output. No, the output is taste, art or a personal decision → `user-add`.

### Not triggers

- A one-shot decision waiting on the owner's action, such as a denied action the owner will run by hand.
- An item this turn addresses in a later step; judge at the end of the turn.

## Completion signals

Run `/worklog complete` in the turn the resolution lands, on any of: a test gating the item passes and the item is its cause; a commit names the item; the owner says it is done or fixed; a PR resolving it merges. Work that shipped through a separate track (a `/plan` for a scope-4 item) completes the same way, with that track's ref.
