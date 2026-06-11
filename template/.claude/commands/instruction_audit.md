---
disable-model-invocation: true
---

Audit a single skill, command, or CLAUDE.md against instruction-loading principles.

Static structural review — sibling to `/claudemd_compact`. Does NOT measure trigger accuracy (use `anthropic-skills:skill-creator`) or adversarial pressure (use `/test_skill`). Principles live in the `instruction_quality` skill.

## Argument

`/instruction_audit <path>` where `<path>` is:
- A skill file: `.claude/skills/<name>/SKILL.md` (or just `<name>` — auto-resolves)
- A command file: `.claude/commands/<name>.md` (or just `<name>` — auto-resolves)
- A hook file: `.claude/hooks/<name>.py` (or just `<name>` — auto-resolves)
- The CLAUDE.md file: `.claude/CLAUDE.md` (whole-file audit; for compression, prefer `/claudemd_compact`)

**No argument:** Print usage and the 3 most-recently-modified skill/command files as suggested targets.

**Multi-target:** `/instruction_audit <path> <path> ...` or `--all` (every skill + command). Fan the mechanical phases (Phase B quantitative inputs, C, C.5, C.75) out to parallel read-only verification agents per the `parallel_agents` skill — one agent per target cluster, with the claims to verify pushed into each prompt. Phase-B principle verdicts and the report stay with the orchestrator; agents return evidence, not verdicts.

**Malformed argument:** If path doesn't resolve, search for skills/commands with names containing the string and offer up to 3 matches.

## Procedure

### Phase A — Load and classify

1. Resolve the argument to an absolute path.
2. Read the target file.
3. Classify:
   - Has YAML frontmatter with `description:` field → skill
   - In `.claude/commands/` → command
   - In `.claude/hooks/` → hook (sub-classify: event hook = registered in settings.json `hooks`; library = imported by other hooks; CLI = invoked by a command — grep `.claude/` for consumers)
   - Equals `.claude/CLAUDE.md` → claudemd
4. **Invoke `Skill(instruction_quality)`** to load the principle checklist. MANDATORY tool call — do not paraphrase the checklist from memory or skip this step on the grounds of "I recall the principles." The skill is the single source of truth; section numbers and carve-outs drift between sessions.

### Phase B — Audit (principle-by-principle)

Run each applicable principle from the checklist against the target. Apply only what's applicable: skill-specific principles skipped for commands; command-specific principles skipped for skills; both apply to CLAUDE.md only as relevant; hooks get the hook-specific principles plus universal principles applied to their docstrings. (Exact section numbers come from the loaded `instruction_quality` skill — do not hardcode them here; they drift.)

**Compute the quantitative inputs first** (so the size/length principles gate on numbers, not eyeballing): line count of the target; for a skill, the `description` char count AND its word count vs. the median word count of sibling `description:` fields in the same `.claude/skills/` directory (Glob the siblings, count). Feed these into the size-proportional and description-as-trigger findings.

**Hook targets — measure, don't trust prose:** read the target's settings.json registration (event, matcher, timeout); verify each output path against the channel matrix (canon: `archive_hook_gotchas.md`); stat any log/state files the hook appends to (bounded-state principle gates on on-disk size, not intent); grep the hook for expiry-marked diagnostics ("Remove after/once") and judge whether the condition is met.

For each principle, output one finding:
- **✅ Pass** — meets the principle, no action.
- **⚠️ Concern** — advisory; could be tighter but functional.
- **❌ Fail** — concrete action recommended.

Each finding includes:
1. Principle name (§N from `instruction_quality` skill)
2. Verdict (✅ / ⚠️ / ❌)
3. Evidence (specific quote with line number)
4. Recommended fix (concrete edit, or "no change" for passes)

### Phase C — Cross-reference rot scan

Run a focused Grep for inbound references to the target across the harness:

- Target is a skill `<name>`: `Grep("<name>")` across `.claude/CLAUDE.md`, `.claude/skills/`, `.claude/commands/`.
- Target is a command `<name>`: `Grep("/<name>\\b")` across the same.
- Target is a hook `<name>.py`: `Grep("<name>")` across `.claude/` (settings.json registration, importing hooks, invoking commands, rule files citing it) — an event hook with zero settings.json hits is dead wiring; a hook whose importers vanished may carry dead back-compat shims.
- Target is CLAUDE.md: skip Phase C (every artifact references CLAUDE.md; signal-to-noise too low).

For each inbound reference, classify:
- **Live** — references content that still exists and is correctly named.
- **Stale** — references moved/renamed content (e.g., a skill file renamed, or `CLAUDE.md §7` after path-scope extraction).

This catches *other artifacts referencing this one* (inbound rot).

**Outbound rot (this one referencing moved content) — run the cheap subset every time, don't punt it:**
- Every cited file path (`agents/foo.md`, `.claude/rules/bar.md`, `gotcha_*.md`) → `Glob` to confirm it resolves.
- Every cited `§N` / named section anchor → confirm it still exists in the target it points at.
- Every named skill/command (`/foo`, `` `bar` skill ``) → confirm it's still registered.

Each is one mechanical check. Only *semantic* outbound rot (a cite whose meaning drifted while the path still resolves) is the harder manual pass — flag as a follow-up if the target is large.

### Phase C.5 — Orchestration-contract scan (conditional)

Run ONLY if the target's procedure invokes `Workflow`, parallel `Agent()`, or a fan-out engine (`Grep` the target for `Workflow(`, `Agent(`, `scriptPath`, `review_fanout`). This operationalizes §12 from the `instruction_quality` skill.

1. **Resolve the invoked artifact.** For `Workflow({scriptPath: X})`, Read X. Identify its arg contract (the `args` parse-guard / destructuring — e.g. `review_fanout.js` reads `A.agents = [{key, prompt, model?}]` + `A.contextPrefix`) and any enforced output schema.
2. **Diff the invocation shape.** Does the target build exactly the keys the script consumes? Flag missing/misnamed keys (silent no-op, not a warning) and schema-required mandate fields the target's prompt omits.
3. **Gotcha screen.** `semantic-search(query="workflow agent args fan-out failure", restrictToDir=".claude/auto-memory")`. For each load-bearing hit (`gotcha_workflow_args_generation_fidelity`, `gotcha_workflow_fanout_search_false_absence`, `gotcha_workflow_single_flight_concurrency`), judge whether the target's design matches the failure shape. If it does and the procedure neither cites the gotcha nor prescribes its mitigation → ❌ Fail (§12).
4. Emit findings in the same ✅/⚠️/❌ format as Phase B, tagged §12.

### Phase C.75 — Claim-freshness spot-check (conditional)

Run when the target asserts codebase or harness facts: file paths a procedure writes to, type/member names it instantiates, test-tree locations, data-file conventions, tool contracts, concurrency caps, capability negations ("X is not possible"). Most reference skills qualify. Operationalizes the *Claim freshness* principle from the loaded `instruction_quality` skill (cite its current §N from the skill — don't hardcode it here).

1. Extract up to ~10 load-bearing verifiable claims.
2. Verify each with one mechanical call (Glob the path / Grep the type name / check the current tool schema). Empty Glob/Grep ≠ absence — confirm with `ls` before flagging (`gotcha_grep_glob_miss_tracked_files.md`).
3. Emit ✅/⚠️/❌ findings tagged with the principle. A claim that routes work to a nonexistent location (e.g. a dead test tree) is ❌ regardless of how clean the prose reads.

### Phase D — Report

Produce a structured report. Output format:

```
## Audit: <target path>
Type: skill | command | claudemd
Lines: <count>
Description length: <count> chars (skill only)

### Findings (Phase B)
1. ✅ §1 Specificity: ... — no change
2. ⚠️ §2 Internal consistency: line 47 says X, line 89 says ¬X — recommend collapsing to a single rule
3. ❌ §4 Cross-reference durability: line 12 cites "CLAUDE.md §7" but §7 was extracted to .claude/rules/csharp_lsp.md — update reference
...

### Orchestration contract (Phase C.5 — only if the target dispatches Workflow/Agent)
- §12 invocation-shape / output-schema / gotcha-screen findings, ✅/⚠️/❌

### Claim freshness (Phase C.75 — only if the target asserts code/harness facts)
- per-claim verify results, ✅/⚠️/❌

### Inbound references (Phase C)
- .claude/CLAUDE.md:67 — "See [Testing Skill]..."  [Live]
- .claude/skills/spell_authoring/SKILL.md:42 — "per testing skill section X"  [Stale: section X renamed]

### Suggested follow-ups (concrete edits)
1. Update line 12 cross-reference: "CLAUDE.md §7" → ".claude/rules/csharp_lsp.md"
2. Collapse lines 47 and 89 internal conflict: ...
3. ...
```

### Phase E — Do NOT auto-apply

The command is **advisory**. Surface findings; user decides which to act on. If user wants to apply a finding, they ask explicitly or invoke a follow-up edit.

## When to invoke

- After major edits to a skill or command (catches drift before it ships).
- Before promoting a skill to broader use (catches description-trigger issues).
- Periodic hygiene: spot-check 1–2 skills/commands per session_end run.
- When a skill seems to misfire (audit description before reaching for `skill-creator` evals — the structural fix often resolves the trigger issue).

## Don'ts

- **Don't audit trigger accuracy here.** Use `skill-creator` (Anthropic-shipped) — it has actual eval tooling.
- **Don't audit adversarial content resistance here.** Use `/test_skill`.
- **Don't audit a target actively being edited in this session.** Diff churn produces false positives.
- **Don't apply CLAUDE.md size principles to skills.** The 200-line cap is for always-loaded content; skills load on description match — different threshold.
- **Don't auto-apply findings.** This is a soft gate; user reviews each before acting.

## Pairs with

- `/claudemd_compact` — applies these principles + CLAUDE.md-specific compression. Both reference `instruction_quality` skill for shared principles.
- `instruction_quality` skill — the shared checklist (single source of truth).
- `skill-creator` (Anthropic) — runs trigger-accuracy evals; complementary, not overlapping.
- `/test_skill` — runs adversarial pressure tests; complementary, not overlapping.
- `/rule_consistency` — audits cross-SURFACE drift (this command is per-FILE); complementary, not overlapping.

## Future extensions (not in v1)

- `--apply <finding-id>` flag: apply a specific finding directly (skip the manual pass).
- Integration with `/session_audit`: if a session edited skills/commands, optionally run `/instruction_audit` on each at session end.
