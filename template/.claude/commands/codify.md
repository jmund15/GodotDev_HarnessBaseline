---
description: Route a correction or repeated workflow to the harness surface that will fire — then prove it.
disable-model-invocation: true
---

Take ONE just-issued correction ("never let this happen again") or ONE procedure you keep running by hand, and land it where it will be in context when the decision recurs. Placement rules are owned by `instruction_quality` §3 and §5; this command orders them into one procedure and adds the duplicate check (Step 0) and the firing proof (Step 7).

`/autolearn` is the sibling, not the overlap: it DETECTS signals across a whole session and owns the quality gates. This command routes one item you already have.

## Arguments

- `/codify <text>` — codify that item.
- **Bare `/codify`** — take the immediately-preceding correction and echo your reading back for confirmation before Step 0. If nothing is recoverable (invoked cold, or after a compaction), run `/autolearn`'s Step 0 compaction recovery; if that yields nothing, say so and stop. Never reconstruct a plausible correction — a fabricated one passes Step 0 triage cleanly, which is the silent failure.
- **Two or more items** — split, confirm the split, run sequentially. One item per pass keeps each independently revertible.

## Step 0 — Does it already exist?

Semantic-search `.claude/` for the rule before writing anything.

**Done: the search query and its top hits (`path:section`) appear in your report.** A claim that it ran is not the done-condition — a skipped Step 0 produces a clean-looking new rule, and nothing flags the duplicate until `/rule_consistency` runs much later.

| Finding | Fix | Wrong fix |
|---|---|---|
| Exists, wasn't loaded at decision time | reachability — re-place it, or add a pointer from a surface that loads then | a new rule |
| Exists, was loaded, was ignored | enforcement — a hook, or a stronger word per §6's no-op test and §18 | restating it louder |
| Exists on 2+ surfaces, contradicting | hand to `/rule_consistency` | a third copy |
| Genuinely absent | Step 1 | — |

The first two rows are the common case. This is `/autolearn`'s *Redesign vs Execute* anti-pattern applied to placement — diagnose adherence before redesigning the rule.

**Division of labour with `/rule_consistency`:** Step 0 *detects* a cross-surface duplicate at save time; `/rule_consistency` *compares and reconciles* across all surfaces as a sweep. Never adjudicate the contradiction here.

## Step 1 — Classify

Judgment the agent must apply → **RULE**. A procedure you re-run by hand → **WORKFLOW** (routing prior: `commands/`, plus a `workflows/*.js` script when it fans out).

**WORKFLOW skips Steps 2–5.** It lands as a command per `instruction_quality` §9–§12 (no-op gate, done-condition per step, argument handling, contract integrity) and its proof is one invocation. A correction that yields both a rule and a workflow is two items — split per Arguments.

**Done: the classification is stated with the one property that decided it.**

## Step 2 — Generalize

Two gates, both required:

- **`/autolearn` *Anti-pattern: Overfit-to-Specific*** — strip the file / PR / spell / SHA out of the principle; it demotes to the `Signal:` evidence line.
- **`feedback_dont_codify_never_from_single_fix.md`** — check whether the correction over-bans a *mechanism*. Litmus: *am I writing never/only/always about a mechanism when the incident was one instance of it?* Overfit-to-Specific does not catch this, and a false `never` blocks legitimate design space.

**Done: the rule reads in class-of-things terms, and any never/only/always in it is named and justified.**

## Step 3 — Mechanizable?

Two mechanization surfaces, checked in order:

- **A tool boundary.** Can a `PreToolUse`/`PostToolUse` matcher *decide* it from a command shape, file path, or tool name? Then it routes to a hook **plus a documented home** — `instruction_quality` §3: a rule whose only home is a hook file is invisible to doctrine, unfollowable when the matcher misses, and structurally exempt from `/rule_consistency`.
- **A gate over an artifact.** Can a script or a read-only lens *decide* it from a diff, a plan file, a PR body, or the repo state at gate time? Then it routes to the gate that already runs there — a lint under `/regression_gate` 1c, a Claude-side grep or lens in `/pr_ready`, a lens in `/plan_check`, or a registry template in `agents/*_agents.md` (via `tools/lens.py`) — and the command that runs the gate is its documented home.

**Done: the matcher condition or the gate predicate is written out, or one line says why the rule needs judgment neither can supply.**

## Step 4 — Route by trigger shape

Each row's rule is owned elsewhere; this table composes them into one ordered procedure and adds the proof column. Work top-down — the order is reach-reliability ÷ passive cost.

| Fires when… | Surface | Owning home | Passive cost | Firing proof |
|---|---|---|---|---|
| a file of a **prefix-anchored class** (below the repo root) is touched | `rules/<name>.md` + `paths:` | `instruction_quality` §5 | zero until matched — but a broad glob matches nearly every session | the glob string is prefix-anchored below the repo root — decidable by reading it, no tooling |
| an agent deliberately enters a domain | `auto-memory/archive/` (cold) | `instruction_quality` §5 | zero | the NL paraphrase an agent would search returns it top-ranked |
| the user types it | `commands/` | `instruction_quality` §5 | zero until invoked | a loaded surface names it at signal-time |
| a tool call matches a condition | `hooks/` **+ documented home** | `instruction_quality` §3 | one interpreter spawn per matched call | pass → deliberate violation → revert, three runs |
| a gate runs over an artifact (diff, plan, PR body, repo state) | lens or lint inside that gate's command (Step 3) | `instruction_quality` §12 | zero until the gate runs | a planted violation yields the finding; a clean input yields none |
| a named task-shape begins | skill | `instruction_quality` §5, §7 | description bytes, every session | a fresh agent given only the description picks it |
| every session, and no trigger exists | CLAUDE.md **or** a MEMORY.md hot topic file | `instruction_quality` §5 | every byte, every session | Step 5 |

`rules/` + `paths:` leads **on a prefix-anchored glob** — the only surface both guaranteed-to-load and genuinely free until matched. On a broad glob (`**/*.cs`, `**/*.tscn`, `**/*.md`) the cost is deferred, not avoided, and the row re-ranks below cold memory; width is about session shape, not file count (`Tests/**/*.cs` stays narrow because a gameplay session never opens `Tests/`). Hooks sit mid-table despite the strongest enforcement: they cost a spawn per matched call and can never stand alone.

**Done: the chosen row is named, and every row above it has a stated reason it was rejected.**

## Step 5 — Always-loaded cost gate

Applies only when Step 4 lands on CLAUDE.md or a MEMORY.md hot topic file.

Write a MEMORY.md hook line with no path and no link. The path costs more than the hook it points at.

**Name the line the new bytes outrank** — the always-loaded admission standard, `instruction_quality` §5 A1–A5. Not whether the line is true or useful — a true line ranking below its neighbours still costs behavior on the lines it dilutes. **No nameable displacement → it goes behind a pointer.** "The cluster already exists" is not a displacement.

Every hot add pairs with an equal-bytes trim regardless of headroom — the ceiling (CLAUDE.md's per `/claudemd_compact`, MEMORY.md's per CLAUDE.md §2) is a backstop, not a license. The arithmetic is `wc -c` before and after: the net delta is ≤ 0, or the report quotes the displaced line and the bytes it freed. For MEMORY.md also state why the decision fires before a search would run; if it does not, the row was wrong — go back to Step 4 and land cold.

**Done: a specific displaced line is quoted, or the item is re-routed behind a pointer.**

## Step 6 — Write, or queue

Direct `Edit` for skills, rules, commands, hooks, cold memory.

**Write the verdict, not the incident.** The landed text is the rule, its trigger, and the one clause that makes it non-obvious — no date, no "observed/measured" narrative, no session story; that evidence goes to `auto-memory/archive/` or Obsidian and is cited by name (`instruction_quality` §5 *Size proportional to load mode*). Every landing is a byte delta: state it, and if the target grew by >1.5KB or >10%, name the line it outranks or cut to match (`harness_growth_guard.py` reports it after the edit — a report you cannot answer means the edit is too big).

**Record the retirement trigger with the rule.** One per rule: `retire_when:` in the memory file's frontmatter, or a `<!-- retire-when: ... -->` comment on the line below a rule landing in a command, skill or `rules/` file. Four kinds — `claude >= X` (a client version), `tool:<name> absent`, `load_census budget <name> under X`, `review-by: YYYY-MM-DD`. Name the condition that would make the rule wrong or pointless; when nothing but time applies, set a review date. `tools/rule_retirement.py` evaluates them, `/autolearn` proposes the retirement once one fires, and a trigger no kind matches is reported as malformed — it retires nothing.

CLAUDE.md and a `rules/` file may carry one file-level `review-by` comment covering every rule in the file; per-rule comments there would grow the always-loaded bundles.

**Queue non-load-bearing CLAUDE.md edits** under `/apply_harness_edits` and its load-mode contract. Append to `.claude/pending_harness_edits.md`, quoting each `old` string verbatim and anchoring by heading, never by line number. Applying a disk edit does not evict the loaded text. A `MEMORY.md` pointer lands with its topic file (CLAUDE.md §2).

Target listed in `baseline.lock.json` → it is shared doctrine (CLAUDE.md §10). Flag for `/sync_baseline` classification, and check whether a companion new file must upstream in the same operation — a cite pushed without its target dangles in every consuming project.

**Done: the diff is applied or the queue entry written, its retirement trigger is recorded, and the baseline status of every touched path is stated.**

## Step 7 — Prove it fires

Run the Step-4 proof for the chosen surface and report the result. A cold-memory or `rules/` proof searches the index, so run `/reindex_search` first — a file written this session is not in it, and the proof fails for the wrong reason.

`instruction_quality` §14: registration proves wiring, not matching. A rule never seen firing is a hope, and it reads in every audit as enforcement that does not exist.

## Report

```
## codify — <one-line rule>

Step 0: <query> → <hits, or "absent">   [branch taken]
Class:  RULE | WORKFLOW  (<deciding property>)
Rule:   <generalized statement>
Surface: <row>  — rejected above it: <row: reason>, ...
Cost gate: <displaced line> | n/a
Landed: <path> (<±bytes>, outranks: <line> | n/a)  | queued: <entry>   [baseline: tracked | untracked]
Retire-when: <trigger>
Proof:  <check run> → <result>
```
