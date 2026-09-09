---
description: >-
  ALWAYS load when reviewing, modifying, or authoring claude code harness files. This includes skills, commands, hooks, rules, reference files, or any other loaded guidance.
---

# Instruction Quality Principles

What makes a harness file — skill, command, hook, rule, reference, CLAUDE.md section — followable at the lowest byte cost. Every edit to a harness file is measured against this; `hooks/harness_growth_guard.py` reports growth against HEAD and the per-surface caps in §5, and `hooks/harness_edit_skill_reminder.py` denies harness edits until this skill is loaded. Section numbers are cited across the harness — never renumber.

## Universal principles

### 1. Specificity over abstraction

Concrete instructions get followed; abstract ones drift.

- Description / opening prose names file types, paths, domains, or trigger shapes.
- Steps are verifiable (`run X, confirm Y`), never `review thoroughly`.
- Each abstract headline carries an inline litmus.
- **Project-genericity test:** a sentence that could sit unchanged in another project's docs changes no behavior here — cut it. Carve-out: `baseline.lock.json` `universal` files are project-agnostic by contract.
- The codebase is the word list: write the real symbol, flag, path, command — not a description of it.

### 2. Internal consistency

No two statements in one artifact recommend opposing actions on the same decision. Check: table "avoid" column vs prose; `do` vs `don't` rules (three override layers on one decision ⇒ collapse to one decision tree); description says auto-fires while the body says manual.

### 3. Single source of truth

Two artifacts covering one rule drift. One canonical home; the rest cross-reference.

- Does the artifact restate what lives in a skill, memory file, CLAUDE.md, or command? Replace with a pointer.
- **Auto-loaded-context duplication:** a SKILL body listing its `paths:` rule files or sister skills duplicates the loader. Inline one load-bearing *fact* if it matters before the rule's glob would fire; never list the file's existence.
- **Hooks ENFORCE, never LEGISLATE.** A hook may inject state freely (git status, budget band, counts). It may inject a *rule* only with a documented home elsewhere, and cites it. A rule whose sole home is a hook is invisible to doctrine, unfollowable when the matcher misses, and exempt from `/rule_consistency`. Audit: every normative sentence a hook emits has a home on a documented surface; none found ⇒ promote, then reduce the hook to a citation.
- **Tier rails by the model that RECEIVES them.** Hooks see only the session model; the dispatch engine sees each job's `model`. Both surfaces tier their guard text; tiering one leaves the other actor unrailed.

### 4. Cross-reference durability

- Section-number, line-number (`lines N–M`) and "see line 42" cites rot silently. Use named anchors or file links.
- Verbatim quotes: re-verify the source still says it; compression paraphrases.
- **Mechanical outbound checks run on every audit:** every cited path resolves (`Glob`), every `§N`/anchor exists in its target (`Grep`), every named skill/command is registered. A cited `.claude/` path is doctrine only if it is tracked — `git log --all -- <path>` non-empty; in a shared checkout a resolving path can be a peer session's transient file.
- **A count or scale owned by another file is a cross-reference, not a mirror.** "The seven-question litmus" restates a cardinality the source owns and matches no mechanical check. Use the countless form; two numbering scales for one concept is the same defect compounded.
- **Inbound rot:** when a target is revised, `Grep` its name across `.claude/{tests,hooks,skills,commands}/` — consumers (fixtures especially) may still encode the pre-revision behavior and reward the obsolete one.

### 5. Size proportional to load mode

**Context load is a spent budget, not a size cap.** Every loaded sentence draws on one attention budget; the question is never *is this true* but *does it outrank what it displaces*.

**Invocation choice spends the same budget.** A skill description is always-loaded in exchange for autonomous discovery; a command, a `paths:` rule, or a `reference/` file costs nothing until reached. Give an artifact a skill description only when an agent must reach it unprompted. Human-invoked work → `commands/`; shared on-demand reference → `rules/`, `reference/`, `guards/`.

**Always-loaded admission standard (A1–A5)** — CLAUDE.md, `MEMORY.md`, anything loading before a trigger:

- **A1 — Budget bytes, not lines** (`wc -c`). Bands: CLAUDE.md per `/claudemd_compact`; `MEMORY.md` per CLAUDE.md §2.
- **A2 — Admission is an ordering test.** Content earns always-loaded status only if its decision is made before any trigger (description, `paths:` glob, matcher, command name) could surface it. Same-moment ⇒ pointer only.
- **A3 — Enforcement tier sets DEPTH, not presence.** Hard deny: the rule, one line. Advisory: rule + cost of getting it wrong. Unenforced: rule + why + example.
- **A4 — Never restate a SessionStart injection.** Grep `hooks/*.py` for unconditional stdout carrying the line; confirm the injection is not model-gated before cutting the doctrine copy.
- **A5 — Density is the audit target: census + delta.** Per-section bytes, sorted, AND delta against the previous audit commit.

**Conditionally-loaded files: size is free, density is governed.** A skill with 180 rules is legitimately larger than one with 40; a fixed byte cap cannot tell them apart. The unit is bytes per rule-unit (bullet, numbered step, table row, bold-lead rule — `harness_growth_guard.py` counts them mechanically and reports B/unit on every edit): **≤~300 B/unit is the ideal; >350 is the audit trigger** — the excess is narrative, restatement or evidence, and the fix is §6, not deletion of rules. A rule-count invariant is what makes cutting safe: count units before and after; the count may not drop. **32KB is the split trigger** regardless of density — past it, detail layers (templates, pattern catalogs, per-step mechanics) move to `skills/<name>/reference/*.md` pointed at from the exact step that reads them, and SKILL.md keeps the verdict layer. Any edit that grows a file by >1.5KB or >10% names the line the bytes outrank, or trims to match.

**The read pattern picks the split axis, and a flat catalog has no verdict layer to keep.** Reader arrives knowing which section they want ⇒ split by domain. Reader must scan every unit to learn which few bear — incident catalogs, rule registries, failure indexes ⇒ split index-from-detail: entries in one `reference/` file, plus a `tools/` script emitting a selector index (one line per entry: id, name, trigger) and fetching bodies by id. Consumers inject the index and fetch what fires; the invoked surface keeps only the access contract. Generate the index at read time — a stored one drifts on the first append. Splitting a scan-all catalog by domain buys nothing: the reader opens every sub-file anyway. Instance: `commands/checklists/known_failure_modes.md` + `tools/kfm.py`, 49KB per lens → 7KB index + ~1KB per hit.

**Verdict vs evidence — the split that stops bloat.** A harness surface carries the *verdict*: the rule, its trigger, the one clause that makes it non-obvious. Measurements, dates, campaign narrative, run counts, "observed/measured/found <date>" provenance, and worked incident stories are *evidence* and live in Obsidian (`Claude/Meta/`) or `auto-memory/archive/`, cited by name. Litmus: delete the sentence — does any runtime decision change? No ⇒ evidence ⇒ out. A `±` table cell or one parenthetical clause is the maximum evidence a verdict may carry inline.

**`paths:` rules are free only on a prefix-anchored glob** below the repo root (`Tests/**/*.cs`, `Jmodot/**`). Extension-only globs (`**/*.cs`, `**/*.md`) fire nearly every session — deferred cost, not removed. Decide width by reading the glob string, not by counting files.

**Promoting a memory rule to `rules/` is a split, never a move:** the one-line rule goes to `rules/`, the evidence file stays in `auto-memory/` (cited bare-by-name). Check first whether an auto-loading skill already owns the domain — then it is an index cut, not a promotion.

Which surface a rule belongs on: [`/codify`](../../commands/codify.md) §Step 4, sole home of the destination table.

**Audit checks:** every added always-loaded line names what it outranks; a skill that only ever fires by name is a command; skill >800 lines / command >400 lines ⇒ split. Don't apply CLAUDE.md's band to skills — they are not always-loaded.

### 6. Conciseness — no editorial padding

Every sentence changes behavior. Run the no-op test: **model-relative** (the default to beat is the weakest model that loads the file), **settled by running** the instruction with and without the sentence, **whole sentences deleted** (word-trimming optimizes length, not behavior), **vocabulary graded too** (a word too weak to beat the default is a no-op — §18).

Cut on sight: lampshading ("this is critical", "(NARROW — read carefully)"); walls of NEVER/ALWAYS that should be bullets; editorial inside table cells; narrative restating an adjacent table; a summary of a summary (the middle layer is the verbosity); provenance residue (*what debate produced this*, "What this is — and is NOT" preambles); forced groups of three; negative scope that does not pre-empt a real, tempting wrong action (keep the misfire-preventing kind once, at temptation time).

**Don't:** compress dense lookup tables; compress decision-gate prose past clarity (§1 outranks §6); shorten skill descriptions below the §7 floor.

### 6b. Prose engineering — parseable clause structure

Dense prose fails when a reader resolves a clause storm by dropping a clause — usually the conditional.

- **The imperative lands in the first ~15 words**; conditions become leading if-clauses or their own bullets.
- **One em dash per sentence, one job.**
- **Parentheticals flatten to one level**; a load-bearing paren becomes its own clause.
- **Headline-then-restate is a summary of its own summary.** State it once.
- **Provenance is residue** ("(user directive <date>)", "Why:" paragraphs). One compressed clause survives only when it combats a counterintuitive default.
- **Active voice, actor named.** Governs authored files; response prose follows the session output style.
- **A classification note appended as a semicolon tail splits the action from its completion** — give it its own clause after the action.

Telegraphic register ("Macro drift → halt (a).") and spec-list density are fine; flatten storms, don't prettify lists.

## Skill-specific

### 7. Description-as-trigger discipline

Descriptions ARE the trigger. 50–500 chars and within ~150% of the sibling median (`/instruction_audit` Phase B computes it). Imperative scope (`Use when X` / `SKIP for Z`) stated as *when and why*, never a keyword list. SKIP clauses exclude only true non-uses — a case the body routes belongs in positive scope. A generic appealing verb-noun name (`prototype`, `explore`, `improve`) over-triggers: state the precondition that must already hold and SKIP the competing surface by name.

### 8. Frontmatter convention

Skills: `description: >-` block scalar. Commands: single-line `description:`, action-first, ~90 chars. **A command with no `description:` publishes its first body line as catalog text** — check the whole `commands/` directory in one pass.

## Command-specific

### 9. Idempotency / no-op gate

A command measures current state before acting and has an explicit "no action needed" exit.

### 10. Procedure verifiability

Each step is a concrete tool invocation with arguments or a delegation to a named sub-procedure. Applies to skills whose steps start work too.

**Every work-starting step states what done looks like**, with two properties: **clarity** (done vs not-done is decidable — a fuzzy bound invites premature completion; sharpen it before splitting the sequence) and **demand** ("every modified file accounted for" forces legwork "produce a change list" does not; demand is the missing half of a delegate spec). An artifact that dispatches work without each job's done-condition moves its own premature-completion risk onto the delegate.

### 11. Argument handling

Commands taking arguments state the format, the no-argument behavior, and the malformed-argument behavior.

## Orchestration-specific (artifacts that invoke Workflow / Agent / scripts)

### 12. Orchestration-contract & known-failure-mode integrity

A prose-clean artifact can still fail at runtime because its contract is wrong.

- **Invocation shape matches the script's arg guard** (read it: `review_fanout.js` destructures `args.agents=[{key,prompt,model?}]` + `args.contextPrefix`). A missing key no-ops silently.
- **Output contract matches the engine schema** (`review_fanout`'s `FINDINGS_SCHEMA` fields and enum values).
- **Screened against memorialized gotchas** — semantic-search `.claude/auto-memory` for Workflow/agent failure modes: `gotcha_workflow_args_generation_fidelity` (large × nested × escape-dense `args` ⇒ 4ms/0-agent death; push bulk via one `contextPrefix` or a file), `gotcha_workflow_fanout_search_false_absence` (fanned `Grep`/`Glob` false-empties; push, don't pull), `gotcha_workflow_single_flight_concurrency` (never fan out GdUnit4 or csharp-ls across `parallel()`).
- **Matching shape ⇒ cite the gotcha and prescribe mitigation + recovery** so a cold executor does not rediscover it.
- **Substituting `Agent()` for a Workflow trades schema enforcement for prompt-and-parse** — deliberate, stated.

## Hook-specific (`.claude/hooks/` + settings.json)

§1–§6 apply to docstrings and every model-facing string a hook emits: each names an action or a condition, never a slogan. §3 binds hardest (hooks enforce, never legislate; tier by receiving model). Verify against live `settings.json` and the official hooks docs, never memory; channel canon: `archive_hook_gotchas.md`.

### 13. Channel-contract validity

stdout→model only for UserPromptSubmit/SessionStart (exit 0); stderr→model only on exit 2; `hookSpecificOutput.additionalContext` (stdout, exit 0) is the ONLY model-visible advisory channel on PreToolUse/PostToolUse; `systemMessage` reaches the user. stderr + exit 0 on PreToolUse/PostToolUse is a dead channel. Block paths: exit 2 + stderr, or `permissionDecision` JSON. A PreToolUse `allow` approves the ENTIRE command string — validate everything it executes.

### 14. Registration & liveness

**Prove the rule bites.** An enforced rule is finished when it has been *observed firing*: clean case passes, a planted violation fails with the expected message, the reverted case passes. Registration proves wiring, not matching. **Absence-assertions fail open by construction** — a broken query and a clean surface return the same nothing; every such check states what a non-zero result would look like, or runs once against a planted violation.

Audit: every hook file is registered or has an identified consumer; no orphans (`__pycache__` without source, logs no hook writes, settings entries pointing at deleted files — which brick every matched tool); temporary diagnostics state an expiry and have not outlived it.

### 15. Bounded state & cost

Hooks fire per call; costs multiply invisibly — measure on disk. Every append-only log/state file has rotation or a cap; per-call matchers stay subprocess-light (N hooks on one matcher = N spawns per call — consolidate shared-library hooks); repeated injected context dedupes per session/turn; internal timeouts fit inside the settings.json timeout.

### 16. Fail posture & docstring/behavior parity

Enforcement gates may fail closed; advisory hooks fail open (exit 0, silent). An advisory hook can see what makes it right (cwd from the payload, operands not just flags) or narrows its trigger until it can — each misfire spends a credibility budget that trains the reader to skim true positives. Shared state on one matcher: atomic writes, writer-first ordering (`gotcha_posttooluse_hook_read_after_write_ordering`). Docstrings advertise only live checks. Hardcoded environment facts are flagged for silent breakage on upgrade.

## Reference-content (artifacts asserting codebase or harness facts)

### 17. Claim freshness

Sample up to ~10 load-bearing codebase claims (paths a procedure writes to, types it instantiates, conventions it propagates) and verify each with one Glob/Grep/LSP call; empty Glob/Grep ≠ absence — confirm with `ls`. Harness-behavior claims (tool params, agent caps, "the harness can't X") are re-checked against the current tool schema on every audit. Checks are mechanical and independent — delegate multi-file audits to parallel read-only agents; synthesis stays in the orchestrator.

## Word choice (all artifacts)

### 18. Leading words, and the negation trap

A **leading word** (*adversarial*, *litmus*, *invariant*, *seam*, *loud*) is a pretrained concept repeated as a token, never re-explained — it anchors behavior in the fewest bytes. A coinage pays definition cost at every site. Audit: every coined term earns its cost against a pretrained near-synonym (prefer renaming the concept over documenting the name); a definition restated per site is §3 duplication; a triad spelled out three times collapses into one token.

**Weak-word replacements:** `utilize`/`leverage`→`use`, `facilitate`→`help`, `numerous`→`many`, `in the event that`→`if`, `serves as`/`stands as`/`boasts`/`features`→`is`/`has`. **Named no-ops** (replace with the concrete claim): `additionally`, `crucial`, `delve`, `enhance`, `fostering`, `interplay`, `intricate`, `landscape`, `pivotal`, `showcase`, `testament`, `underscore`, `vibrant`.

**Negation drags the forbidden behavior into context.** State the target behavior where one exists. Hard guardrails keep their `NEVER` — the rule is pairing, not replacement: every prohibition names the positive target in the same line or the next.

## Anti-patterns

- Adversarial resistance is `/test_skill`'s eval, not this skill's.
- Path-scoped rule size is amortized across only the sessions that load them.
- Numeric targets are heuristics; a specific 220-line file beats a vague 180-line one.
- Don't audit actively-edited targets — diff churn fakes positives.

## Composition with other tools

| Concern | Tool |
|---|---|
| Static structural quality; hook validity; claim freshness | `/instruction_audit` |
| Cross-surface rule consistency | `/rule_consistency` |
| Adversarial content resistance | `/test_skill` |
| CLAUDE.md size + compression | `/claudemd_compact` |
| Auto-memory cleanup | `/memory_audit` (dedup pass by hand) |
| Size/density report on harness edits | `hooks/harness_growth_guard.py` (automatic) |
