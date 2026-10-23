---
name: Architecture_Brainstorm
description: >-
  Use to design new systems, family contracts or ownership, framework boundaries or architecture-heavy features once candidates exist. SKIP an unpopulated idea space; use idea_brainstorm.
---

# Architecture Brainstorm

> **Scope:** between [`/idea_brainstorm`](../idea_brainstorm/SKILL.md) (*what could exist?*, produces the candidate pool) and plan production ([`/part_drive`](../../commands/part_drive.md), *how do we ship it?*). This skill answers *how should we build it?*. `/doc_full` is post-hoc, written after a system ships.

## The Hard Gate

```
DO NOT INVOKE ANY IMPLEMENTATION SKILL, WRITE ANY CODE, OR
SCAFFOLD ANY PROJECT UNTIL THE DESIGN HAS BEEN APPROVED.
```

If you find yourself reaching for `Edit`, `Write`, or implementation-flavored skills (the project's authoring and refactor procedures, or `architecture_philosophy`'s code patterns) before the user has approved the design — **stop**. The skill's job is to make the user's intent and the design's shape *both* precise before code writes start. Skipping ahead loses the design conversation.

---

## 1. When to Use

**Strong trigger phrases** — auto-fire when the user says:
- "how would we approach X?"
- "should we build Y? what would that look like?"
- "let's design X" / "design X" (when candidates exist)
- "I want to design X but need to think it through"
- "design a stamina system for the player" (or any new-system request with candidates)
- "what would Z look like architecturally?"
- "we need a Z system but I'm not sure how to structure it"

**Weak trigger phrases** — ask user first ("is this an architecture brainstorm, or do you want to brainstorm ideas first?"):
- "add feature X" (could be scope 2 or scope 3+)
- "refactor Y" (could be mechanical or architectural)
- "implement the Q system" (depends whether design has happened)

**Fire when ALL of these hold:**

- [ ] Task scope is genuinely 3+ (multi-file or design-choice-heavy) **OR** the user describes the feature in open-ended terms.
- [ ] No existing design doc covers the topic (verified via common.md §1 existing-doc check, run in Step 1).
- [ ] Task is not in the SKIP categories below.
- [ ] Candidate ideas EXIST — either from `/idea_brainstorm` (read its idea-bank doc), from a mature domain with canonical patterns, or from the user's own framing. If not, route to `/idea_brainstorm` first.

Whether this skill runs follows [`execution_depth.md`](../_brainstorm_shared/execution_depth.md) §Admission table, Design row.

**Autonomous alternative — [`/design_drive`](../../commands/design_drive.md):** satisfies the [design contract](../_brainstorm_shared/design_contract.md) through its required artifact gates with free-form process and taste batched to one design-lock gate. Use this skill when the user's taste enters continuously.

---

## 2. When to Skip

The skip cases follow `execution_depth.md` §Fields, Design litmus for settled.

**Other skip categories:**

- [ ] **Mechanical changes** — typo fixes, single-line tweaks, simple renames, file moves, mechanical migrations.
- [ ] **Bug fixes with known root cause** — handled by the [`debugging`](../debugging/SKILL.md) skill's phased procedure. Brainstorming is for forward design, not retrospective root-causing.
- [ ] **Existing design doc covers the work** — §1 surfaces this. If a doc exists, redirect: *"A design doc covering this exists at `<path>`. Load it and follow `doc_before_writing` discipline; brainstorming would duplicate effort."*
- [ ] **Greenfield with no candidates** — route to `/idea_brainstorm` first; come back here when an idea bank exists.

---

## 3. Three-Phase Composition

```
/idea_brainstorm           /architecture_brainstorm        /part_drive + impl
─────────────────          ─────────────────────────        ──────────────────
"What could exist?"        "How should we build it?"        "How do we ship?"

• Scope-framing Socratic   • Architecture-narrowing         • Entry: /plan_part
• Per-cluster diverge        Socratic                         <part> → /part_drive
  → filter → hone          • 2–3 approaches with              (verbatim design
  → cluster → present        trade-offs                       surface + codebase
• Idea-bank doc            • Commitment review                drift)
  Status: ideation-          before design-lock             • Bounded file list
  complete                 • Parts authored on              • Function sigs
• /update_roadmap            roadmap.md (per                • Test names
  applies Per-Cluster        common.md §6) —                • Step-by-step
  Routing → Parts on         5-criterion gate for           • Verification
  roadmap.md                 plan-pending state               commands
                           • Design doc + 
                             /update_roadmap apply
                             Status: brainstorming-
                             complete
```

**Key invariants:**
- Plan production is owned by [`/part_drive`](../../commands/part_drive.md). This skill describes the *handoff*, not its internals.
- The user invokes the drive command (or chooses inline implementation). This skill does NOT start plan production for them.
- A plan file should reference the design doc AND the corresponding Part on roadmap.md (e.g., *"Implements Part 'Foundation+Refactor' per `BrainstormingDesigns/YYYY-MM-DD-stamina-system/arch.md#session-1`"*).
- Plan production's first action must be **bounded file enumeration** — if files can't be enumerated, drafting HARD-STOPS (per [`doc_and_review.md`](reference/doc_and_review.md) §Plan-production handoff requirement).
- An `ideation-complete` idea-bank doc upstream is GENUINELY useful context — load it during §1 so the approaches step (Step 4) draws from a curated pool rather than generating candidates from thin air.

**When the user wants to skip plan production and go straight from brainstorming to inline implementation** — fine, that's their call. The design doc + roadmap Part landed; future maintenance has the design context.

**Cardinality and flow topology** — full vocabulary (forward fan-out, cluster merge, ideation skip, reverse signal via `*-rework`, zero-impl outcome, workshop terminate) lives at [`_brainstorm_shared/common.md §7`](../_brainstorm_shared/common.md). The above diagram is the slim "you are here" view for this skill; common §7 is the canonical reference. Note that this skill in particular often produces a zero-`plan-pending` outcome (the design IS the answer — usage convention, naming standard, etc.); §7 covers when that's valid.

---

## 4. The Procedure (8 Steps)

Each step names the reference file it reads; read it in full at that step, and again after a compaction:

| File | Read at | Holds |
|---|---|---|
| [`reference/red_team.md`](reference/red_team.md) | Start, only with `--red_team` or `--auto` | The opt-in red-team passes and their per-step hooks (Steps 2, 4, 5) |
| [`reference/part_authoring.md`](reference/part_authoring.md) | Step 5, after the design sections are approved | Part authoring: the readiness gate, every Part litmus and guard, API + Test Pin, the MVP recommendation, and the Part anti-patterns |
| [`reference/doc_and_review.md`](reference/doc_and_review.md) | Step 6 | Save paths, the roadmap guardrail, doc structure, the Step 7 self-review checklist, Step 8's `/update_roadmap` procedure and the plan-production handoff |

### Step 1: Existing-doc check + spawn-placement decision

**[Shared]** See [`_brainstorm_shared/common.md` §1](../_brainstorm_shared/common.md) — the three required asks (existing design doc / memory gotchas / does a family already own it), dispatched as [`/explore`](../../commands/explore.md) lenses. Step 3 below still owns the FULL abstraction inventory; §1's `exp-prior-art` is what surfaces an obvious owner early enough to reshape the topic.

**Architecture-specific consideration:** the digest should ALSO surface any `ideation-complete` idea-bank doc (`ideas.md` in a topic folder) covering the same topic. If one exists, load it — it's the curated candidate pool that Steps 2 and 4 will draw from. If not (and the topic isn't a mature domain), redirect to `/idea_brainstorm` first.

**Cluster-scoped consumption (when upstream `ideas.md` has Per-Cluster Routing):** Scope this invocation to the named cluster only.

- Read the cluster section: survivors (Step 4 sourcing), Findings (priors), Open Questions (Socratic seeds), Routing (timing).
- Other clusters → SEPARATE invocations producing separate `arch-<cluster>.md` files in the SAME parent topic folder.
- Cross-cluster hybrids → SEPARATE arch session per hybrid (`arch-<hybrid-slug>.md`, same parent folder).
- Cross-cluster Open Questions are NOT this session's responsibility.
- Frontmatter: `derived_from: ../ideas.md`, `cluster: <cluster-slug>`.

**Default (no Per-Cluster Routing):** Whole-doc consumption (mature-domain, user-direct framing, or legacy doc). Output: a fresh topic folder with `arch.md` inside per `common.md §5`.

**Spawn-placement decision (apply if this session is spawning a child OR is itself a child of a parent brainstorm):**

See [`common.md §5.1`](../_brainstorm_shared/common.md) — three placements (same-folder sibling / child subfolder / sibling folder) discriminated by a three-criterion test. Criteria map to outcomes per the §5.1 table (NOT "all three must hold"). Resolve at this step so Step 6's save path is unambiguous.

**Quick decision:**
- Just an extension paragraph → **same-folder** (no new roadmap).
- Own brainstorm session + independent maturity, parent-confined audience → **child subfolder** (submap; criterion 3 is *expected* to fail — parent-confined IS the definition).
- All three pass (multi-parent audience) → **sibling folder**.

**Most common gotcha:** failing a legitimate submap candidate against criterion 3 ("only serves this parent"). Criterion 3 is the submap-vs-sibling distinguisher, NOT a submap gate. See §5.1 *Common gotchas* table.

### Step 2: Ask clarifying questions (Socratic, one at a time)

**Rule:** Multiple-choice preferred over open-ended. One question per turn.

- Don't open with *"great question!"* / *"absolutely!"* — see `archive/feedback_no_performative_agreement.md`. Ask the question directly.
- Multiple-choice example: *"For the stamina system: should it be (A) a finite resource that depletes and regenerates, (B) a cooldown stack that gates abilities, or (C) a combination — light actions deplete, heavy actions consume cooldowns?"*
- One question per turn so the user can think and respond without juggling multiple decisions.
- Stop asking when the design space is narrow enough to propose 2–3 concrete approaches.

**Sourcing options:** If an upstream idea-bank doc exists, draw multi-choice options from its honed candidates. If not, draw from canonical patterns + existing codebase abstractions.

**Red-team hook (`--red_team` only):** [`red_team.md`](reference/red_team.md) §Step 2 hook.

### Step 3: Inventory existing abstractions (cross-link, don't duplicate)

**Rule:** Before proposing approaches, grep for existing 2+ subclass families in the relevant domain.

This step **applies the discipline documented in [`architecture_philosophy`](../architecture_philosophy/SKILL.md) — *Coupling & Discovery* sections (*Interface Usage*, and the engine skill's retrieval rules where one applies)**. Don't restate the rules; surface them as a step in the design process:

- **First move:** run `mcp__plugin_semantic-search_semantic-search__search` against the relevant abstraction. Works for known type names (`"BBDataSig"`, `"IIdentifiable"`, `"MovementProcessor3D"` — returns the class declaration in one shot) AND for fuzzy domain queries (`"combination resolution"`, `"effect lifecycle"`, `"modifier composition"`). Especially useful for abstractions whose names don't follow `abstract*` / `I*` conventions. See CLAUDE.md §9.
- **Then:** LSP `findReferences` on the candidate base type to enumerate all subclasses / implementers (semantic-search returns the declaration; LSP returns every usage — the latter is what reveals the 2+ subclass family).
- **Backup:** Grep for `abstract class` and `interface I` patterns in `{{PROJECT_NAME}}/` and `Jmodot/` if semantic-search misses something.
- Identify which existing hierarchies a new design could extend instead of inventing parallel abstractions.

**Cross-reference:** `feedback_inspect_existing_abstractions_first.md` — *"if a base + 2+ subclasses exist, extend it. Inventing a parallel abstraction is usually wrong."*

**Inventory-currency check (gate before Step 4).** Re-run inventory subset if any risk-factor holds: session has spanned >24 hours from initial Step 3 fetch; `git status` shows uncommitted upstream work or behind-count; submodule pointers may have drifted (`git submodule status` — verify Jmodot HEAD matches parent-repo expected). Stale inventory produces Step 4 approaches modeled on deleted/renamed types or missing newly-shipped infrastructure → Section 4 rewrite cost when the divergence surfaces mid-Section-5. Trust test-file `using` imports over directory existence when verifying greenfield-vs-shipped status.

### Step 4: Propose 2–3 approaches with trade-offs

**Rule:** Each approach gets explicit trade-offs and a recommendation. Never present a single "obvious" answer — the comparison is what makes the choice legible.

Format:

```
Approach A — <one-line summary>
  Trade-offs: <pros> // <cons>
  Code shape: <high-level — 2-3 file paths or class names>
  Recommendation: <yes/no/conditional>

Approach B — ...
Approach C — ...
```

If you can only think of one approach, that's a signal — either the design is well-constrained (no real alternatives) or you haven't explored the space enough. Push for at least one alternative; if genuinely none, say so explicitly.

**Sourcing options:** When an upstream idea-bank doc exists, the 2–3 approaches are typically *clusters of related candidate ideas* (e.g., "Approach A: data-driven Resource-family per category" might bundle 4–5 idea-bank entries; "Approach B: registry + dispatcher" bundles 4–5 different ones). The approaches are architectural framings; the idea-bank entries are the specific instances those framings would generate.

**Red-team hook (`--red_team` only):** [`red_team.md`](reference/red_team.md) §Step 4 hook.

### Step 5: Present the design for review, approve taste-bearing or changed commitments

**Rule:** Do not present the full design as one wall of text. Present each load-bearing commitment in a form the owner can review, keeping related fields together (e.g., Data Model → Lifecycle → BB Keys → Tests → Integration Points). Obtain approval for taste-bearing or changed commitments before design-lock, and propagate any reopened decision to dependent commitments.

**Red-team hook (`--red_team` only):** [`red_team.md`](reference/red_team.md) §Step 5 hook.

**Verbatim port discipline:** when sections are agreed in chat, port them **1:1** into the design doc. Do NOT silently digest the chat-form into a tighter file-form. See `feedback_no_unilateral_condensation.md` — *"chat content IS the spec when the user says 'save to file'."*

If the user pushes back on a commitment, treat it as a decision to make, not a clarification to dismiss. Update it, obtain approval when it changes a taste-bearing commitment, and propagate it to dependent commitments.

**Expect 1-2 substantive upstream reopenings per session.** Late-section content (Sections 4-5 — architecture detail, Parts decomposition) routinely surfaces assumptions baked into early-section (1-3) identity locks. Reopening Sections 1-3 from Section 5 feedback is correct behavior, not failure — the alternative is shipping a self-contradicting design. Update the upstream section, get re-approval, propagate downstream implications. If a session reopens 3+ times on the same Section 1 lock, the locked decision was wrong — escalate to a Socratic re-pass rather than continuing to patch.

**Checkpoint to scratch ledger.** **[Shared]** See [`_brainstorm_shared/common.md §8`](../_brainstorm_shared/common.md) for path, append cadence, format rules, consumption, and lifecycle. Per-section bullet schema for this skill:

   ```markdown
   ## Section <name>
   - Approach chosen: <A/B/C — one-line rationale>
   - Key commitments: <2–4 bullets — concrete decisions made (file paths, class names, lifecycle choices)>
   - Open follow-ups: <Parts demoted to *-pending or *-rework state, if any>
   ```

   Append after each design section is approved. Worker reads it as `reference_files` in the final `write_doc` call (Step 6).

**Final mandatory phase: author Parts for the roadmap.** After all design sections are approved, read [`reference/part_authoring.md`](reference/part_authoring.md) and run its sub-procedures in their stated order. Every architectural claim in the design body maps to exactly one of: {a Part on the roadmap, an explicit worklog deferral, abandoned with stated reason}. Get approval on the full Parts list before Step 8.

### Step 6: Save the design doc

Read [`reference/doc_and_review.md`](reference/doc_and_review.md) §Step 6. The save path follows the Step 1 spawn-placement decision. Step 6 saves the design doc only: `roadmap.md` at every depth is written by `/update_roadmap` in Step 8, never here.

### Step 7: Spec self-review

Run every check in [`doc_and_review.md`](reference/doc_and_review.md) §Step 7 before declaring the design complete, then its Mode A red-team pass for architecturally-loaded designs and the shared user review gate.

### Step 8: Invoke `/update_roadmap`

After the owner approves both the doc and the Parts list, invoke `/update_roadmap` per [`doc_and_review.md`](reference/doc_and_review.md) §Step 8; a child-subfolder spawn takes its two-invocation sequence. `/update_roadmap` is the single executor for `roadmap.md` edits.

---

## 5. MCP-Offline Policy

**[Shared]** Obsidian MCP offline → non-event (native vault `Read`/`Write`/`Edit`). **ai-worker MCP offline → fallback to a Haiku subagent for any `read_files` / `read_web` call** (Step 1 existing-doc digest, Step 2 multi-section spot-check, final `write_doc` synthesis). Do NOT degrade to chained native reads. See [`_brainstorm_shared/common.md` §3](../_brainstorm_shared/common.md) for the substitution shape.

---

## 6. Anti-Patterns

Refuse the premise and cite the rule. Inline deliberately — a rationalizing agent never opens a file whose trigger is "when rationalizing". The Part-authoring rows sit at the end of [`part_authoring.md`](reference/part_authoring.md), the file open when they arise.

| Rationalization | Reality |
|---|---|
| "The design is clear enough; let me just start coding" | The Hard Gate exists for a reason. If the design *is* clear, Step 5 will move fast. Don't skip the doc. |
| "I'll write the code first and document the design after" | That's `/doc_full`, which documents a shipped system. The brainstorming doc is upfront — it *constrains* the implementation, not the other way around. |
| "Reviewing related commitments together is too broad — approve each one separately" | Step 5: group related commitments for review, and obtain approval for taste-bearing or changed ones before design-lock. |
| "Only one approach makes sense here" | If you can't articulate alternatives, you haven't fully explored the design space. Find at least one alternative, even if you reject it. |
| "Let me also implement a small part to see if it works" | That's not brainstorming — that's prototyping. Different mode. If you want to prototype, say so explicitly and switch modes. |
| "The user already approved the topic; I can skip clarifying questions" | The user approved the topic, not the shape. Clarifying questions narrow the shape. |
| "Existing design doc is outdated; let me start fresh" | Don't. Read the existing doc, identify what's stale, propose updates per `doc_before_writing` discipline. Starting fresh discards prior decisions. |
| "I'll hand-edit `roadmap.md` while I'm in the design doc anyway" | `/update_roadmap` is the single executor for roadmap edits — it owns Mermaid regen, derived-view recomputation, validator checks, and revision log append. Hand-edits drift these layers silently. Always route roadmap changes through the command. |
| "Topic is greenfield with no candidates — I'll generate ideas inline during Step 4" | No. Idea generation belongs in `/idea_brainstorm`. Generating candidates inline here skips the divergence pipeline (filter / hone / cluster), produces inferior approaches, and loses the idea-bank artifact. Route to `/idea_brainstorm` first, then come back. |
| "Idea-bank doc exists but the candidates feel thin — let me regenerate during Step 4" | If the idea bank feels thin, that's signal to RE-RUN `/idea_brainstorm` (its revision discipline applies in-place), not to silently regenerate here. Architecture brainstorm consumes idea-bank output, doesn't replace it. |

---

## 7. Cross-references

Links already given at their step are not repeated here.

- [`_brainstorm_shared/common.md`](../_brainstorm_shared/common.md) — §1 existing-doc check, §1.1 resume table, §1.2 stale-roadmap remediation, §2 rationale spot-check, §3 MCP-offline, §4 user-review gate, §5 doc path / frontmatter conventions, §5.1 spawn-placement convention, §6 roadmap.md schema (including §6.10 Trigger semantics), §7 workflow phase cardinality, §8 scratch-ledger checkpoint discipline
- [`project_subsystems`](../project_subsystems/SKILL.md) — the subsystem registry behind the readiness gate's bounded-scope criterion and Integration touch points
- The project's authoring skills and refactor procedure — invoked AFTER brainstorming approval, when the approved design maps to one of their authoring surfaces
- The project's test-first policy (CLAUDE.md §Development Philosophy: Hybrid TDD) and its testing skill, where it ships one — design docs MUST include test-plan section (Logic-Domain TDD applies to design-mandated logic)
- `/doc_full` — POST-HOC documentation of a shipped system; complementary to this skill, not overlapping. Run *after* implementation ships, not instead of brainstorming.
- `/test_skill architecture_brainstorm` — adversarial validation of this skill (Mode B rule-negation).
- [`agents/doc_before_writing.md`](../../commands/agents/doc_before_writing.md) — §1 existing-doc check pattern
- `obsidian_conventions` skill — wikilink & heading-anchor discipline, MCP connectivity, vault write locations
- `feedback_recommended_fix_means_implement.md` — post-approval inline-implementation path
- `archive/feedback_session_start_hook_does_not_override_skill_procedure.md` — Step 2 / Step 5 procedural-gate discipline
- CLAUDE.md: Planning Phase Checklist (this skill sits upstream of it; output feeds INTO it), Obsidian section (Read/Write constraints, tiebreaker, MCP-offline policy), Worklog section (small deferrals vs tracked items in TODO/)
- Plan production is owned by [`/part_drive`](../../commands/part_drive.md) — `--plan-only` to an approval-ready plan, unflagged through to shipped code. This skill describes the **handoff**, not its internals.
