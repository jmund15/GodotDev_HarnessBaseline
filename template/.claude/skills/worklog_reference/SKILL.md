---
name: worklog_reference
description: >-
  Decision-time companion to the `/worklog` command — load when a deferral
  surfaces and you must classify or route it: class/scope inference, domain
  pick, regular-vs-future-vs-user-tasks routing, the full trigger catalog,
  completion signals. Triggers: "worklog", "deferred", "defer", "follow-up",
  "out of scope", "park this", "next pass", "future scope", "user-tasks".
  SKIP for ad-hoc todos unrelated to the Obsidian worklog system, and for
  executing a `/worklog` operation — the command is self-contained for mechanics
  (classification heuristics live here).
---

# Worklog Reference

The **decision-time** half of the worklog system: it tells you *whether* a deferral should be logged, *how* to classify it, and *where* it routes. The `/worklog` slash command is the **execution** half — it owns the operation recipes (show / add / complete / sweep / triage / plan / unblock / promote / user-add). Reach for this skill when CLAUDE.md's auto-detect fires on a deferral phrase and you need to compose a correct `Add to Worklog` proposal; reach for `/worklog` when you need to actually perform a write.

The worklog itself is one Obsidian doc with two live buckets — **Active** (in-flight, mirror-visible) and **Future Scope** (distant-horizon `When: future` items, one-liner format, mirror-excluded) — plus two opaque sibling docs: **Worklog-Archive.md** (completed `[x]` items, moved out of Active on completion, read only via `/worklog history`) and **User-Tasks.md** (user-only-addressable, fully agent-opaque). Active soft-caps at 30 items; `/worklog show` alarms over the cap and recommends `/worklog triage`.

## File paths (cite, don't guess)

| Purpose | Path |
|---------|------|
| Source of truth (Obsidian, vault-relative) | `DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog.md` |
| Completed-item archive (Obsidian, opaque — `/worklog history` only) | `DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog-Archive.md` |
| Lightweight title mirror (local, tracked) | `.claude/worklog-titles.md` |
| User-only addressable items (Obsidian, parallel doc) | `DevProjects/{{PROJECT_NAME}}/Claude/TODO/User-Tasks.md` |
| Linked-from per-topic TODO docs | `DevProjects/{{PROJECT_NAME}}/Claude/TODO/*.md` (excluding `PRTesting/`) |

**The mirror is a discovery index, not a spec.** When planning or scoping a logged item, read its full `Context` / `Where` / `Source` block in `Worklog.md` — the title-only mirror (`worklog-titles.md`) can mislead about scope entirely (a title naming one concept whose Context is about a different subsystem). Plan from the source block, not the mirror line.

## Classification (required on every item)

Pick the best match. If genuinely ambiguous, ask the user.

| Class | When to use | Typical scope range |
|-------|-------------|---------------------|
| `fix` | Known bug, known or quickly-determinable repair path | 1–2 |
| `debug` | Unknown root cause, investigation needed before any fix | 1–3 |
| `feature` | New behavior, capability, or content | 2–4 |
| `refactor` | Restructure with no behavior change | 1–3 |
| `test` | Add or expand test coverage | 1–2 |
| `docs` | Documentation-only update (skill, README, design doc tweak) | 1–2 |
| `chore` | Tooling, config, build, data-file maintenance | 1–2 |
| `design` | Brainstorming/planning, no implementation yet | 4 (always paired with a linked doc) |

**Reclassification rule:** when an item's nature changes (e.g., `debug` resolves into a known root cause), update its class in-place — don't open a new entry. Bump the date stamp via `- updated YYYY-MM-DD` if the change is non-trivial.

## Scope (required on every item)

A coarse effort signal, not a deadline.

| Scope | Meaning | Heuristic |
|-------|---------|-----------|
| **1** | Trivial — sub-30 min, single file, mechanical | "I could finish this in the time it took to log it" |
| **2** | Small focused — couple hours, possibly multi-file | "One sitting; no design choices to agonize over" |
| **3** | Multi-file thoughtful — half-day; real design tradeoffs | "I'll need to read related code and pick an approach" |
| **4** | Full feature/system — multi-session; **linked planning doc required** | "This needs its own design doc; can't fit in Active alone" |

**Scope-4 → linked doc rule:** scope-4 items live as one-line pointers in Active with a `Plan doc:` field; the actual design lives in a sibling `TODO/<name>.md` and is indexed under the worklog's `## Linked Docs`. Don't write multi-paragraph designs in the Active block. When a `TODO/` doc is created, deleted, or merged, its Linked Docs entry should follow.

**Required vs. optional fields:** every item carries class + scope + a one-line `Context`. `Where:` / `Source:` / `When:` are optional. `Plan doc:` is required iff scope 4.

## Timeline / When (optional per item)

A readiness classifier: *can this item be worked on now?* Distinct from scope (effort) — this signals phase-gating and prerequisites.

| Value | Meaning | Where it lives |
|-------|---------|----------------|
| *(omitted)* | **Ready** — actionable now. Default; all items without a `When:` line are implicitly ready. | `## Active > ### Domain` |
| `after <condition>` | Has a prerequisite or phase gate. `<condition>` is free text: another worklog item title, a milestone, or a non-code event. | `## Active > ### Domain` (surfaced in mirror with `[after: <condition>]` suffix) |
| `future` | Distant-horizon deferral — **no specific trigger**, revisited "someday". Stronger threshold than a normal next-session deferral. | `## Future Scope` (collapsible callout, one-liner format, **excluded from mirror**) |

**Rules:**
- `When:` is a sub-bullet, parallel to `Where:` and `Source:`. Omitting it means ready.
- The `condition` in `after <condition>` is free text — a human signal, not a machine gate.
- If you find yourself tagging many items `future`, that's a signal some should be removed entirely or promoted to a Linked Doc.
- `/worklog plan` only scores ready items; `after` items appear in a separate "Waiting" section; `future` items aren't surfaced there at all.

**`future` vs. normal deferral — the litmus.** Most "defer this" / "for now" / "next pass" / "follow up" signals → regular Active item, no `When:` line (ready). Only escalate to `When: future` when ALL of these hold:

- No defined trigger condition. ("Eventually we should X" — not "after Y ships, X.")
- Will not plausibly come up next session.
- Re-visit cadence is months, not days/weeks.
- Tagging it `future` does NOT lose information the agent will need to proactively surface (no condition to watch for in commits).

If even one fails → keep it as a regular Active item. **When in doubt → regular Active.** The cost of a too-visible item is one mirror line; the cost of a hidden-and-forgotten item is a permanent leak.

## Canonical domain list

When deciding which `### Header` an item belongs under, pick the closest match (matches CLAUDE.md's risk table where applicable):

| Long form (Active section header) | Short form (mirror line) |
|-----------------------------------|--------------------------|
| AI / Critters | `ai` |
| Wizard | `wizard` |
| Spell Architecture | `spell` |
| Synergies / Ingredients | `synergy` |
| Inventory / Crafting | `crafting` |
| Encounter | `encounter` |
| Currency | `currency` |
| UI / UX | `ui` |
| VFX / Animation | `vfx` |
| Testing | `testing` |
| Tooling / Workflow | `tooling` |
| Documentation | `documentation` |
| Jmodot Framework | `jmodot` |
| Other | `other` |

If an item genuinely doesn't fit, use `Other` — never invent a new domain without surfacing it to the user first.

## Full Trigger Catalog (auto-detect signals)

CLAUDE.md has the compact list. This is the expanded version. Treat any of these as *candidate* signals — propose adding via `/worklog add`, then let the user confirm.

### Trivial-do-now branch (preempts logging entirely)

**This is the FIRST gate** — before evaluating regular vs. conditional vs. future routing, ask: should this item even be logged?

**Fires when ALL of these hold:**
- Inferred scope == 1.
- Inferred class is `fix`, `refactor`, `docs`, or `chore` (NOT `debug`, `design`, `feature`, or `test`).
- Phrasing is mechanical and single-action — words like "remove X reference", "rename Y to Z", "sweep `<single thing>`", "add line/paragraph to `<file>`", "fix typo", "delete dead code at `<path>`".

**Does NOT fire when:**
- Investigative phrasing: "investigate", "audit", "find out", "trace why".
- Open-ended decisions: "decide", "consider", "convention for", "policy for".
- Multi-file or multi-domain scope (even if individually small).
- Any `debug` / `design` class signal — those need investigation/exploration, not "just do it".

**Propose-and-confirm shape:**
```
This looks trivial — do now (y), or add to worklog (a)?
```
- `y`: do the work this turn (skip logging entirely).
- `a`: fall through to the Regular-deferral path (log normally).
- `n`: skip both (user wants neither).

**Rationale:** Items like "Remove deleted FreezeState reference from project_subsystems SKILL.md" or "Sweep `subagent` vs `subagent` terminology" are 30-second jobs. Logging them costs more attention long-term (mirror line, eventual completion-sweep, JSONL chatter) than just doing them. The worklog should hold *deferrals*, not *items the agent could finish in the time it took to log them*.

**Explicit `/worklog add` bypasses this gate.** When the user types `/worklog add ...` directly, treat it as an intentional log-it-anyway signal and skip the trivial check. The user already decided.

### Regular-deferral triggers (→ Active section, no `When:` line)

These signal "not now, but could come up next session". Default routing.

#### My-side triggers (things I might say)
- **Hedging on cleanup:** "this is a bit duplicated", "could be cleaner", "not pretty but works", "smells off"
- **Scope-trim language:** "for now I'll just...", "skipping this for this PR", "out of scope", "not addressing this here", "deferring this"
- **Future-tense intentions:** "we could also...", "we should also...", "worth noting...", "we'll come back to this", "next pass should..."
- **Plan-output markers:** `[Deferred]`, `DEFERRED:`, `TODO:`, `FIXME:` appearing in plan files, summaries, or review notes
- **Implicit deferral:** noting a smell, a refactor opportunity, a dead-code candidate, a missing test, a stale doc — and *not* fixing it in this turn

#### Your-side triggers (things the user might say)
- **Direct verbs:** "defer", "punt", "park", "later", "follow-up", "follow up"
- **Postponement:** "remind me to...", "don't forget...", "make sure we come back to..."
- **Scope-shift:** "next pass", "another time", "different session", "not now", "save that for"
- **Plan-trim signals:** explicitly removing items from a plan during scoping discussion

### Conditional-gate triggers (→ Active section, `When: after <condition>`)

These signal "ready when X happens". Item stays in Active mirror with `[after: X]` suffix.

- **Phrase patterns:** "after X ships", "once Y is done", "when Z lands", "blocked on W", "we can do this once...", "as soon as we have..."
- **Inferred from context:** if a deferral mentions a specific PR/spell/system as prerequisite (`"do this after Core Elemental tier-1 ships"`), capture the prereq as the `<condition>`.
- **Litmus:** can you write a one-line condition that, when true, would make this item ready? Yes → `after`. No → either ready (regular) or `future`.

### Future-scope triggers (→ `## Future Scope` section, `When: future`)

**Stronger threshold.** Only fire on these phrases — they signal distant-horizon intent with no defined trigger:

- **My-side:** "eventually we should...", "long-term we'd want...", "someday this should...", "down the road...", "if/when we ever...", "no urgency on this...", "passive watch — no trigger yet...", "worth revisiting in N months..."
- **Your-side:** "eventually", "someday", "long-term", "long-haul", "down the road", "if/when we ever", "in the distant future", "way out there", "wishlist"
- **Inferred shape:** items whose `Context` reads like a *standing observation* (e.g., "watch X landscape", "re-evaluate when Y matures") rather than an actionable task. The action is *re-evaluate later*, not *do now-ish*.

**Anti-trigger (do NOT route to Future Scope on these):** "later", "next pass", "follow-up", "for now", "out of scope", "not addressing this here". These are normal deferrals — the item could come up next session and should stay visible in the mirror.

When in doubt → regular Active. The cost of a too-visible item is one mirror line; the cost of a hidden-and-forgotten item is a permanent leak.

If unsure whether something qualifies as Future Scope → **ask, don't auto-route**. The propose-and-confirm shape is: `Add to Worklog Future Scope: <title> — <domain> · <class> · scope <n>?` (note the explicit "Future Scope" in the prompt so user knows it'll be hidden from mirror).

### User-Tasks triggers (→ parallel `User-Tasks.md`, fully excluded from agent context)

**High-confidence threshold.** These trigger items Claude fundamentally cannot tackle — production art, feel-driven tuning, open-ended brainstorms, cross-doc design audits. The propose-line is `Route to User-Tasks: <title> — <domain>?` (note the explicit "User-Tasks" so user knows the item will leave Worklog awareness entirely). User overrides with `y, active not user-tasks` to keep it Claude-tackable.

#### Production art / animation / shader / VFX
- **My-side:** "needs visuals for X", "needs the X sprite", "needs the rise animation", "art pass on X", "shader work for X", "this scene needs proper visuals"
- **Your-side:** "I'll handle the visuals for X", "art is on me", "I'll make the sprite"
- **Litmus:** does the work require an art tool / animation timeline / shader graph? Yes → User-Tasks.

#### Feel-driven tuning / playtest passes
- **My-side:** "needs a playtest pass to dial in X", "calibrate by playing", "tune by feel", "values feel off but only you can tell"
- **Your-side:** "I'll playtest this and tune the numbers", "let me feel this out", "I need to play it"
- **Litmus:** is the missing input "what feels good" rather than "what is correct"? Yes → User-Tasks. (Note: "deterministic stat balance" with measurable criteria stays a `design` worklog item, not User-Tasks.)

#### Open-ended brainstorms without a Claude-anchor
- **My-side:** "brainstorm synergies between X and Y" *when* the output is a creative direction, not technical alternatives; "design the flavor of X", "decide what the lore around X should be"
- **Your-side:** "I want to brainstorm X", "let me think on X synergies"
- **Litmus:** does "brainstorming this with Claude" produce a design Claude can implement? Yes → `design` worklog item, scope 4, paired Plan doc. No (output is *your* preference, not technical) → User-Tasks.
- **Anti-trigger:** "brainstorm a unified status-effect blackboard schema" is technical — Claude can run `architecture_brainstorm` (routed via `idea_brainstorm` first if greenfield) and produce a design doc. → `design` worklog, NOT User-Tasks.

#### Cross-doc design audits driven by user vision
- **My-side:** "your game vision changed — your design docs need an audit pass", "the lore and spell docs disagree on X — needs your reconciliation"
- **Your-side:** "audit my design docs for X", "reconcile my docs against the new vision"
- **Litmus:** is the missing input *your* intent / *your* taste? Yes → User-Tasks. (Claude can audit *for inconsistencies* — that stays `docs` worklog. But audit *for vision-alignment* requires the user's vision as input.)

#### User-executed Parts (roadmap-resident, distinct from User-Tasks)

Parts on a brainstorm `roadmap.md` whose execution is inherently user-domain (spatial design, manual content authoring, taste-driven tuning) but carry roadmap deps and dependents. Examples: "design 10–15 static prototype floor scenes." Route to roadmap State=`user-owned` (see `_brainstorm_shared/common.md §6.3`), NOT `User-Tasks.md` — the Part must stay on the roadmap to preserve the dependency graph.

#### Anti-triggers (do NOT route to User-Tasks on these)
- Technical tasks that *touch* art (e.g., "wire the visuals component to the new spell") — Claude can do this; the art existing is a prerequisite, not the work.
- Bug fixes that *block* feel-tuning (e.g., "fix the shake parameter not respecting amplitude") — the fix is Claude's; only the post-fix tuning is yours.
- Brainstorms with defined technical output ("brainstorm the IBlackboardProvider migration order") — Claude tackles those via `architecture_brainstorm`.

**When in doubt → regular Active.** False-routing to User-Tasks is a permanent leak (the doc isn't surveyed by sweep/triage/plan); false-routing to Active just costs one mirror line.

If unsure whether something qualifies for the worklog at all → **ask, don't skip**. A redundant prompt is cheap; a missed entry is a permanent leak.

### Inferring class + scope at log time

When the auto-detect rule fires `Add to Worklog: <title> (<domain>)?`, also propose a class + scope inferred from context:

> `Add to Worklog: <title> — <domain> · <class> · scope <n>?`

Inference heuristics:
- "Investigate / why does X happen / pre-existing failure" → `debug`, scope 1–2 unless symptom touches multiple systems.
- "Refactor / extract / consolidate / sweep" → `refactor`, scope 2–3.
- "Add test for / cover X" → `test`, scope 1–2.
- "Document / update SKILL / fix doc reference" → `docs`, scope 1.
- "Wire X / extend Y / new system" → `feature`, scope 2–4 (use 4 only if a planning doc would help).
- "Plan / design / brainstorm" → `design`, scope 4 (always paired with a doc).
- Bug-class language ("breaks", "regression", "not working") with known cause → `fix`, scope 1–2.

User's `y` accepts your proposed class+scope. User can override mid-confirm: `y, scope 3` or `y, refactor not feature`.

### Non-triggers (don't fire on these)
- **Policy-blocked one-shot decisions** waiting on user action — e.g., a denied harness action waiting for the user to push manually. That's a single decision, not a tracked todo.
- **In-progress turns** where the deferred item is being addressed *this turn* but in a later step. Wait until end-of-turn to evaluate.

## Completion Detection signals

Mark-complete (via `/worklog complete`) fires on:
- A test that gates a logged item passes (and the item is the cause)
- A commit lands that names the item (commit message reference)
- User explicitly says "done with X", "X is fixed", "marked X complete"
- A PR merges that resolves the item

Mark **immediately**, in the same turn the resolution lands. The Active section's `[ ]` items must always reflect reality — on completion the item moves out of `Worklog.md` into `Worklog-Archive.md` as a `[x]` one-liner and drops from the mirror. The archive is opaque (never loaded passively); review past completions via `/worklog history`.

## Choosing the right operation

The `/worklog` command's Forms table is the full operation list. Three routing decisions worth internalizing before you reach for the command:

- **`/worklog add` vs. `/worklog user-add`** — use `user-add` when the item needs user judgment Claude fundamentally can't produce: production art, feel-tuning, open-ended brainstorms whose output is the user's preference, cross-doc audits requiring the user's vision. Use `add` (default) for everything Claude can plausibly tackle — including `design`-class items with technical output. **Litmus:** *"If Claude wrote a perfect plan body for this and the user said 'go', would the result be a working artifact?"* Yes → `add`. No (output is taste / art / a personal decision) → `user-add`. When in doubt → `add` — false-routing to User-Tasks is a permanent leak (the doc isn't agent-surveyed); false-routing to Active just costs one mirror line.
- **`/worklog plan` — survey vs. draft mode** — `plan` with no target *surveys* candidate batches (pick later); `plan scope:N` / `items:N` (or the `tackle` alias for `plan scope:3`) *drafts* — fills the target with the highest-scoring ready items and produces a ready-to-execute plan body. Both share the Step 1–3 scoring engine; they differ only in what they emit.
- **Worklog item vs. `spawn_task`** — the worklog holds deferrals that fit a *future session*. If an item grows beyond inline scope (typically a scope-4 that must be acted on *now*), graduate it via `mcp__ccd_session__spawn_task`, then run the COMPLETE recipe so it moves to `Worklog-Archive.md` — with a `(spawned task <id>)` ref in place of a commit hash.

## MCP-offline

Non-event — `Worklog.md` is a vault file edited with native `Read`/`Edit`/`Write`. Obsidian MCP offline does not block `/worklog`; only the `last_updated:` frontmatter bump prefers the MCP (`obsidian_manage_frontmatter`), with a native `Edit` as the fallback. See CLAUDE.md §3 / `obsidian_conventions` skill.

## Cloud fallback (CLAUDE_CODE_REMOTE=true)

Distinct from MCP-offline: on cloud the **entire Obsidian vault** is unreachable (not just the MCP), so there is no native-edit fallback — `Worklog.md` simply does not exist in the sandbox. Mutating ops queue to the tracked file `.claude/worklog-pending.md` for replay in a later local session. Full recipe (detection, entry format, replay flow, skip-and-audit-trail conflict policy, idempotency) lives in `/worklog` → *Cloud fallback*. Decision-time summary:

- **On cloud:** `add` / `complete` / `promote` / `unblock` **append** to `worklog-pending.md` under a per-session header instead of writing Obsidian. Read ops use the local mirror where it suffices (`show`); full-doc ops (`show all`, `sweep`, `plan`/`tackle`) defer to local. Commit the pending file — the handoff crosses machines via git.
- **On local:** any `/worklog` invocation first offers to replay un-struck pending entries into Obsidian, strikes through (not fails) conflicts, then truncates the queue body (keeping its header). Header-only body → no-op. The SessionStart hook surfaces `Cloud worklog: N pending` as the awareness anchor.
