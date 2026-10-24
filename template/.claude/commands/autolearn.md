---
description: Find session learning signals; use /codify for a known item
---

Phase 2 of `/session_end`. This command owns discovery, the retirement pass and the packet; `/codify` owns every decision and landing.

## When to activate

Trigger on `/session_end` Phase 2 or an explicit request:
- "autolearn", "learn from this session", "update skills from these corrections"
- "remember this pattern", "make sure you do X next time"

Do NOT activate for one-off corrections or when the user declines skill modifications.

## Step 0: Session digest (MANDATORY FIRST STEP)

Signal detection runs over the digest, never over what survived compaction. In `/session_end` Phase 0 already printed it; standalone, run it now:

```bash
python3 .claude/tools/session_digest.py --prompt-tail autolearn --handoff
```

Use the handoff only as the bounded orientation packet. Before claiming whole-session coverage, page every
prompt and friction row in the saved evidence index until each kind's reported `pages` count is exhausted:

```bash
python3 .claude/tools/session_digest.py --digest-file <full-json> --evidence-page prompts --page <N>
python3 .claude/tools/session_digest.py --digest-file <full-json> --evidence-page friction --page <N>
```

The digest rebuilds the whole session from the live transcript (append-only across compactions). JSON at
`logs/session_digest_<sid8>.json`; older pre-compaction backups (`logs/pre_compact.json` and its summary companion) are
the same schema and only needed if the live transcript is gone. Recovered command-argument rows follow the
`unattributed` rule in `session_digest.md` §Caveats.

The pass is done when it states whether early corrections held across compaction segments.

**Nuance recall (conditional):** apply [Transcript Nuance Recall](agents/transcript_nuance_recall.md), which owns the gate, the quote check and the cached pass. Candidates enter at MEDIUM confidence through the normal filter. `/self_evaluate` reads this pass instead of repeating it.

## Surface routing

Run each surviving signal through [`/codify`](codify.md) §Steps 0–5, its `/autolearn` caller contract, and decide its surface there, not here. A signal its §Step 0 rules no change gets no packet row.

## Retirement pass (runs beside signal detection)

Codified rules may carry a retirement trigger (`/codify` Step 6). A missing marker is intentional when no invalidating condition is known; it is not malformed and implies no permanence. Scan the declared triggers and propose only rows whose supplied evidence says they fired, so the pass removes as well as adds:

```bash
python3 .claude/tools/load_census.py --budgets --json .claude/logs/load_census.json
python3 .claude/tools/rule_retirement.py --client-version <this client's version> \
  --tools <comma list of the tool names in your live schema> \
  --census .claude/logs/load_census.json --json logs/rule_retirement.json
```

Each `fired` row becomes one proposed retirement in the review packet, naming the rule, its trigger and the reason it fired. A fired `review-by` row is proposed as keep (renew the date to when the rule's evidence could next plausibly change, stating that reason in the renewal row), retire, or demote to `.claude/auto-memory/archive/`, citing the file's citing-session count from `memory_hit_counts` in `/tmp/eval_out/stats.json` (`tools/analyze_eval_archive.py`; memory files) and `git log -1 --format=%cs -- <path>`. A fired `evidence` row is proposed as confirm (delete the marker), revise (rewrite the rule and re-date `since:`), or retire, citing the rows `rule_retirement.py` counted. Each `malformed` row becomes a proposed trigger repair — a trigger no kind matches retires nothing. Report the `undecidable` count with the evidence that was not supplied; do not read it as "still needed".

## Signal detection

Scan the session for:

**Corrections** (highest value) — a user statement that changes what you do next.
**Repeated patterns** (high value) — the same feedback or choice recurring across files or prompts.
**Approvals** (supporting evidence) — a user statement confirming an approach.

**Missed delegation** (orchestrator-tier sessions)
- `DELEGATION:` entries in the self-evaluate archive, or user feedback about work that should have been dispatched
- Each carries a rationalization + proposed harness edit (per `/self_evaluate`'s delegation calibration); when the same rationalization recurs across sessions, propose the named edit against the ladder (`reference/model_ladder_evidence.md`) or the grain/litmus rules (`orchestration` §5/§11) (or the recording session's proposed file) rather than re-describing the failure

**Friction** (mandatory disposition — the class that is otherwise glossed over and never fixed)
- Every digest friction row: a denied tool call, an error that needed a workaround, a blocking wait, a user interrupt, a user complaint about process ("this used to not happen", "why do you keep…")
- Each row → `fixed` / `harness` (name the file and the edit shape: allow rule, hook fix, skill line, launcher flag) / `worklog` / `accepted: <class>: <why>` (`guard-working` | `outside` | `owner-held` | `one-off`). Friction that recurred in this digest, or that already sits in a prior archive entry, routes to `harness` unless `/codify` §Step 0 rules no change; then it is `accepted` with the target's quoted text
- Unlike corrections, friction needs no user words to count: the tool error is the signal

**Fixed defects** — each commit this session made whose subject starts `fix` (`git log -F --grep='<this session's Claude-Session URL>' --format='%h %s'`) is a signal with no correction or friction row needed: `/codify` §Step 0 names the choice upstream of the defect, and the commit is its `Signal:` evidence.

**Predecessor tail** — when this session resumed another session's work (a handoff, `/overnight_review`, a digest pickup), that session's friction rows after its own `/autolearn` ran are signals here, because its run could not see them.

**Packet outcomes** — run `python3 .claude/tools/autolearn_outcomes.py lookup --target <target>` before proposing: a declined row whose reason and signal still hold is not proposed again.

**Ignore:** context-specific one-offs ("use X here" without "always"), ambiguous feedback, and contradictory signals (ask for clarification instead).

## Signal quality filter

Before proposing any change, ask:
1. Was this correction repeated, or stated as a general rule?
2. Would this apply to future sessions, or just this task?
3. Is it specific enough to be actionable?
4. Is this **new information** I wouldn't already know? Advice you would give any project does not belong here.
5. **Falsifiable mechanism claim?** If the entry asserts how a tool/engine/API *behaves* ("X isolates Y", "Z is case-sensitive", "A causes B") — not a preference or observed event — it needs **direct evidence that isolated it**. A fix that changed more than one thing cannot isolate a mechanism (isolation means varying one factor), and a single observation is NOT verification. Run the falsification check now, or save only the observed fact — never the inferred mechanism. A saved mechanism claim MUST carry a `**Verified:** <the check that confirmed it>` line; a mechanism entry lacking it is a defect (greppable by the memory-claim audit).

Only propose changes that pass questions 1–4, and 5 when the entry asserts a mechanism, then `/codify` §Step 2's generalization gates.

## Review packet

Proposals reach the owner as one packet (`CLAUDE.core.md` §Preferences), retirements included. Each row names its target file and section, the change (a memory entry's text; a skill's current and proposed text), the signal quote, its confidence and recommendation; recommended rows come first. It also carries `/codify`'s §Step 0 verdict with the target's quoted text and its §Step 2 model scope. It prices the trade: what each occurrence cost and how often it recurs, against the edit's cost in loaded bytes, new code, its proof and any state its guard would need recorded. Recommend a row only when that benefit is larger (`feedback_price_the_tradeoff_before_recommending_it`). Ask once: apply the recommended rows, choose rows, or apply none. Under an armed `/overnight`, each row becomes a `Q` row parked `owner-domain` in its Close doc, and `/overnight_review` applies the answers.

## Applying changes

Run `/codify` §Steps 6–7 on each approved row, satisfying its [Step 0 self-check](codify.md#step-0--does-it-already-exist) before landing: it lands the change and proves it fires. Commit as `chore(autolearn): <brief description>` and report what changed.

Then record each packet row with `python3 .claude/tools/autolearn_outcomes.py record --date <YYYY-MM-DD> --session <id> --row <R#> --target <target> --signal <signal> --recommended <apply|choose> --outcome applied|declined --reason <text>`, with the owner's stated reason or `none given`. The tool rejects a malformed row and caps the file.