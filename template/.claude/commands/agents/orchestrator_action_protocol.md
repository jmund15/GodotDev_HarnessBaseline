---
disable-model-invocation: true
---

# Orchestrator Action Protocol

<!-- Single source of truth for how orchestrators handle agent findings. -->
<!-- Referenced by: /review-pr (Phase 3-4), /review-prs (Phase 2), /session-audit (Phase 3) -->
<!-- If you update this protocol, all orchestrators pick up the change automatically. -->

## Finding Schema

Every agent finding MUST conform to this JSON schema:

```json
{
  "agent": "agent-name",
  "action": "FIX | ASK | PLAN",
  "category": "bug | rule | improvement",
  "critical": false,
  "file": "path/to/file.cs:line",
  "description": "What the issue is",
  "old": "exact code to replace (FIX only, null otherwise)",
  "new": "replacement code (FIX only, null otherwise)",
  "question": "specific question for the user (ASK only, null otherwise)",
  "options": ["Option A (Recommended) — reason", "Option B — reason", "Option C — reason"],
  "scope": ["file1.cs", "file2.cs"] ,
  "rationale": "Why this matters — cite specific rule or explain the risk"
}
```

### Action Tiers

| Action | Meaning | Required Fields | Agent Responsibility |
|--------|---------|-----------------|---------------------|
| `FIX` | Clear, mechanically applicable fix | `old`, `new` with exact code snippets | Provide precise OLD/NEW. Orchestrator applies on confirmation. |
| `ASK` | Beneficial change needing user input | `question` with a specific, answerable question; `options` with ranked alternatives (best guess first) | Describe the issue, propose ranked options (recommended option first), formulate one clear question. First option is always the agent's best-guess recommendation. |
| `PLAN` | Systemic issue requiring multi-file redesign or architectural discussion | `scope` listing affected files/areas; `options` with ranked approaches if enumerable | Describe the problem, propose ranked approaches if possible, explain scope and impact. |

**Omission rule:** If a potential finding would NOT prevent a bug, enforce an explicit project rule, or meaningfully improve correctness/safety — do not report it. There is no "low priority" tier. Either it's worth acting on or it's not worth reporting.

### Category Tags

| Category | Meaning | Decision Heuristic |
|----------|---------|-------------------|
| `bug` | Will cause incorrect behavior, silent failure, or data corruption at runtime | "Does this break something?" |
| `rule` | Violates an explicit project rule (CLAUDE.md, Architecture Philosophy, conventions) | "Is there a written rule this breaks?" |
| `improvement` | Beneficial change not mandated by any specific rule | "Would this make the code better even though nothing requires it?" |

### Critical Flag

The `critical` field is an optional boolean (default: `false`). Set to `true` ONLY for findings that represent:
- Runtime crashes or data corruption
- Silent failures that mask bugs (e.g., `?.` skipping required cleanup)
- Security vulnerabilities
- Pool state corruption or lifecycle violations that cause cross-spell contamination

The flag is a **positive signal** — its absence simply means "normal finding." Do not agonize over the threshold.

---

## Orchestrator Action Protocol

When an orchestrator receives agent findings, it processes them in this order:

### Step 1: Merge & Deduplicate

- Parse each agent's JSON findings array
- Deduplicate findings referencing the same `file:line` from different agents (keep the one with more specific `old`/`new` snippets, or the one with `critical: true`)
- Sort: critical findings first, then by action tier (FIX → ASK → PLAN), then by category (bug → rule → improvement)

### Step 1.5: Verify FIX Findings Against Actual File Content

**Why:** Sub-agents (especially Haiku) occasionally hallucinate file paths, line numbers, or quote paraphrased rather than literal code in the `old` field. Applying a FIX with a hallucinated `old` either fails the `Edit` tool's exact-match requirement (best case) or — if the hallucinated string happens to match unrelated code elsewhere — silently mangles the wrong location (worst case). This step catches both classes before the user is asked to confirm.

**Procedure** (run on every FIX-tier finding; skip ASK and PLAN — they describe systemic issues, not specific lines):

For each finding where `action == "FIX"` and `old != null`:

1. **Parse `file:line`.** Split the `file` field on the last `:`; left side is `path`, right side parses to `line_num` (int). If parse fails, mark `unverified: true` with reason `"unparseable file field"`.
2. **Read context window.** Use the `Read` tool with `file_path=path`, `offset=max(1, line_num - 3)`, `limit=8`. This gives ~3 lines before the cited line through ~4 lines after.
3. **Normalize both texts** (whitespace-tolerant compare):
   - For each line in the read window: strip the `Read`-tool line-number prefix (format: `<n>\t<content>`), collapse runs of whitespace to single spaces, strip leading/trailing whitespace.
   - Apply the same normalization to `finding.old`, splitting on `\n` if multi-line.
4. **Match check:**
   - Single-line `old` → look for normalized `old` as substring of any single normalized window line.
   - Multi-line `old` → look for the normalized `old` lines as a contiguous run within the normalized window.
   - Match found → tag `verified: true`. Proceed to Step 2 normally.
   - Match NOT found → tag `verified: false`, `likely_hallucination: true`. Move to the dedicated UNVERIFIED section in Step 2 (rendered ABOVE the FIX section, since the user must triage these before approving auto-apply).

**Latency budget:** ~50ms per `Read` tool call × N findings. For typical PR review (≤30 findings), this adds ~1-2 seconds to the orchestrator phase. Acceptable.

**False-positive handling:** A `verified: false` finding is NOT auto-rejected — the user reviews it with the actual file context displayed and can override. The verification flag is *advisory*, surfacing suspect findings for human triage rather than silently dropping them.

### Step 2: Present Unified Report

Display all findings grouped by tier in a single comprehensive overview. **The ⚠️ UNVERIFIED section appears FIRST when present** — these are findings whose `old` text could not be located at the cited `file:line` and may be hallucinated. The user must triage them before any auto-apply runs.

```
## ⚠️ UNVERIFIED — likely agent hallucination, review before applying (<count>)
<N>. [<agent>] <description>  [<category>] [CRITICAL]
   → CITED: <file>:<line>
   → CITED OLD: `<exact text the agent claimed exists>`
   → ACTUAL CONTEXT (lines <a>-<b>):
     <8 lines from the file as actually read>
   → <rationale>

These findings cite code that doesn't appear at the cited location after
whitespace-tolerant comparison. Common causes: line drift since the agent
read the file, paraphrased quoting, or subagent hallucination. Treat as
suspect — confirm against the actual context before approving auto-apply.

## FIX — Auto-applied on confirmation (<count>)
<N>. [<agent>] <description>  [<category>] [CRITICAL]
   → <file>:<line>
   → OLD: `<exact code>`
   → NEW: `<replacement code>`
   → <rationale>

## ASK — Needs your input (<count>)
<N>. [<agent>] <description>  [<category>] [CRITICAL]
   → <file>:<line>
   → Question: <specific question>
   → Options: (1) <recommended option> [Recommended], (2) <option>, (3) <option>
   → <rationale>

## PLAN — Needs discussion (<count>)
<N>. [<agent>] <description>  [<category>]
   → Scope: <affected files/areas>
   → Options: (1) <recommended approach> [Recommended], (2) <approach>, (3) <approach>
   → <rationale>
```

The `[CRITICAL]` tag appears inline only when `critical: true`. The ⚠️ UNVERIFIED section is omitted entirely when no findings fail verification (the common case on healthy reviews).

### Step 3: NOTE Synthesis (Orchestrator-Only)

After presenting agent findings, the orchestrator MAY add a `## Notes` section if it observes cross-agent patterns that individual agents couldn't see:
- Multiple agents flagging related concerns in the same subsystem
- A pattern of similar issues across different files suggesting a systemic convention gap
- Contradictory findings from different agents that need reconciliation

**This is orchestrator-generated, NOT an agent output tier.** Agents never produce NOTE findings. This prevents agents from using it as an escape hatch to avoid committing to FIX/ASK/PLAN.

### Step 4: Execute Actions

**Default flow: Proceed through ALL tiers in order.** Do NOT suggest deferring ASK or PLAN findings. The default is to address everything now — apply FIX, walk through ASK for user input, then present PLAN items for user decision. Only defer if the user explicitly requests it.

**Confirmation prompt** (after presenting the report): State that you will apply all FIX changes, then walk through ASK items for input, then discuss PLAN items. Example:
> "Ready to proceed? I'll apply the N FIX changes, then walk through M ASK items for your input, then we'll discuss K PLAN items."

**FIX tier:**
1. Apply ALL FIX findings automatically using OLD/NEW snippets (user already confirmed via the prompt above)
2. If user chose "apply selectively" — ask which to skip, then apply the rest
3. Build to verify fixes compile
4. Move on to ASK tier

**ASK tier:**
1. Walk through EACH ASK finding one at a time (or in small related groups)
2. Present the question with ranked options (best guess first, marked as "Recommended")
3. If the user responds with just "yes" or a number, apply the corresponding option (default: first/recommended)
4. Apply the fix per user's answer immediately before moving to the next ASK item
5. If user says "skip" on a specific item — note as deferred, continue to next

**PLAN tier:**
1. Present EACH PLAN finding directly to the user with ranked options if available
2. For each, ask how they want to address it:
   - Address now (enter plan mode or implement immediately)
   - Create an Obsidian TODO note (deferred)
   - Dismiss as not relevant

**PLAN-to-ASK promotion:** Agents SHOULD prefer ASK with ranked options over PLAN whenever the issue has a finite set of actionable alternatives (2-4 concrete options). PLAN is reserved ONLY for truly open-ended issues that cannot be resolved by choosing from options (e.g., "this subsystem needs a fundamental redesign — here are the 5 affected files"). If an agent can enumerate concrete options, it MUST use ASK, not PLAN.

---

## Claims to Refuse

When summarizing agent findings or producing the verdict, refuse to write any of:

- "Should work now" / "Probably fixed" / "Seems to be passing"
- "Looks correct to me" / "I think this resolves it"
- Any predicted outcome of a verification step that hasn't run yet
- Any opener that performs agreement instead of acting on it ("you're absolutely right!", "great point!")

**Either:**

- Cite concrete evidence (test output line, file diff hunk, `JmoLogger` log line, exit code, `dotnet build` summary), OR
- Use future-tense honestly: *"Phase N will verify this."*

**Confidence ≠ evidence.** This complements the 3-tier verification in `/regression_gate` (silent-skip sentinel + baseline drift + explicit failures): that command *produces* the evidence; this rule prevents agents from pre-announcing what the evidence will say.

**Related:** `feedback_no_performative_agreement.md` — same family of discipline applied to feedback reception. `/regression_gate` Tier 1 (silent-skip sentinel) — the architectural floor that makes "tests passed" a falsifiable claim.

---

## Verdict Logic

After processing all findings:

| Verdict | Criteria |
|---------|----------|
| **APPROVE** | 0 critical findings, ≤2 total findings |
| **APPROVE WITH NOTES** | 0 critical findings, 3+ findings (all addressable) |
| **REQUEST CHANGES** | 1+ critical findings, or unresolved ASK findings the user declined to address |

---

## Migration Notes

This protocol replaces:
- The numeric 0-100 scoring rubric (formerly in `review_agents.md`)
- The AUTO/GUIDE fix classification (formerly in `review_agents.md`)
- The FIX_NOW/FIX_LATER/DISCUSS classification (formerly in `session_audit.md`)

All orchestrators (`/review-pr`, `/review-prs`, `/session-audit`, `/doc-audit-fix`) reference this file as their shared action protocol.

Changes:
- Added optional `options` field to Finding Schema (ranked alternatives for ASK/PLAN, best guess first)
- Added PLAN-to-ASK promotion rule (prefer ASK with ranked options over PLAN when alternatives are enumerable)
- Updated ASK presentation format to show ranked options with recommended marker
- Added Step 1.5 (Finding Verification) — orchestrator grep-checks every FIX `old` field against actual file content with whitespace tolerance; unverifiable findings surface in a dedicated ⚠️ UNVERIFIED section above FIX. Counters subagent hallucination (Haiku `mooyum_milk.tres` class). See `parallel_agents` skill §5 *Model Selection*.
