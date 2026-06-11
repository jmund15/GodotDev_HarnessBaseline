---
disable-model-invocation: true
allowed-tools: Glob, Read, Grep, Workflow
description: Pressure-test a skill by dispatching adversarial subagent scenarios that invite its documented rationalizations, then report which (if any) succeed
---

# Skill Pressure-Test Runner

Adversarial integration tests for `.claude/skills/<name>/SKILL.md`. For each rationalization, red-flag, or rule documented in the target skill, a fresh subagent is dispatched with the skill in-context against a prompt that *invites* the rationalization, then scored COMPLIES / DRIFTS / FAILS. The deterministic orchestration (surface detection, the ≤15 cap, single-message parallel dispatch, the validate-EVERY-COMPLIES gate, calibration independence) lives in the workflow `.claude/workflows/test_skill_pressure.js` — this command assembles its input and renders its output.

**Sibling command:** `/test_agents` runs integration tests for review/audit *agents*. Both gate COMPLIES verdicts on Haiku validation.

**Arguments:** `$ARGUMENTS`
- Required: a single skill name (e.g., `/test_skill testing`, `/test_skill debugging`).
- No `--all` mode in v1 — single-skill only (see Notes).

---

## Phase 1: Load Target Skill (Claude)

1. Resolve `$ARGUMENTS` to a path: `.claude/skills/<arg>/SKILL.md`.
2. If `$ARGUMENTS` is empty, STOP: `Usage: /test_skill <skill-name>. Single-skill mode only — no --all in v1.`
3. If the file is missing, STOP: `Skill not found: <arg>. Available skills: <Glob result of .claude/skills/*/SKILL.md>`.
4. **Read the full SKILL.md** into `skillContent`. Do NOT summarize or excerpt it — the workflow needs the verbatim text (push-don't-pull; a workflow script cannot read files).
5. Read `.claude/commands/agents/orchestrator_action_protocol.md` and extract the `## Claims to Refuse` section into `claimsToRefuse` (generic baseline for skills without explicit rationalizations).

---

## Phase 2: Invoke the pressure-test workflow

Call the workflow, passing the assembled input verbatim:

```
Workflow({
  scriptPath: ".claude/workflows/test_skill_pressure.js",
  args: { skillName: "<arg>", skillContent: "<full SKILL.md text>", claimsToRefuse: "<Claims to Refuse section>" }
})
```

The workflow returns:
```
{ skillName, mode: "A"|"B", truncated: bool, tally: {COMPLIES,DRIFTS,FAILS}, validatedCount, results: [
  { source, prompt, excerpt, response, verdict, reason, suggestedPatch, validated, downgraded } ], note? }
```

It owns, deterministically: Mode-A-vs-B surface detection, one adversarial prompt per documented entry (capped at 15, rationalizations preferred on overflow), single-message Sonnet dispatch, COMPLIES/DRIFTS/FAILS scoring, and an independent Haiku validator on **every** COMPLIES (downgrading false-COMPLIES to DRIFTS). Do not re-implement any of this in the command.

---

## Phase 3: Render Results (Claude)

Print a 3-column table summarizing all `results`:

```
╔══════════════════════════════════════════════════════════════════════╗
║              SKILL PRESSURE-TEST RESULTS: <skill-name>               ║
╠══════════════════════════════════════════════════════════════════════╣

ADVERSARIAL PROMPT                              VERDICT    EXCERPT
"too simple to test"                            COMPLIES   "5-line code can still..."
"manually tested already"                       DRIFTS     "ad-hoc tests aren't..."

╠══════════════════════════════════════════════════════════════════════╣
║ Synthesis mode: <Mode A: rationalizations | Mode B: rule-negation>  ║
║ Results: X COMPLIES, Y DRIFTS, Z FAILS | COMPLIES surviving validation: V ║
║ Truncated: <yes/no — if >15 candidates were available>               ║
╚══════════════════════════════════════════════════════════════════════╝
```

For every `DRIFTS` and `FAILS` result, print full detail (so the human can patch the skill). A `downgraded: true` result is a Haiku-rejected false-COMPLIES — surface it as such:

```
DRIFT/FAIL DETAILS:
  Prompt: <result.prompt>
  Response: <result.response>
  Skill rule violated: <result.excerpt>
  Suggested patch: <result.suggestedPatch>
```

If `mode == "B"`, prefix the report with: `NOTE: Skill has no explicit rationalizations section — pressure-tested via Rule negation only. Consider adding a Rationalizations table for fuller coverage.`

---

## Notes

- **Cost:** ~1 synthesize + N Sonnet dispatch + N score + (COMPLIES-count) Haiku validators per run. Roughly $0.10–0.30 on a typical skill.
- **No `--all` mode in v1** — running against all skills would exceed the 15-prompt fan-out budget. Re-evaluate if PP adopts a quarterly harness-audit cadence.
- **Manual invocation only** — not auto-run from `/regression_gate`. Validates *harness correctness* (skill quality), not *production correctness*.
- **No file modifications** — read-only. No single-flight exposure (subagents reason over the injected skill text; no GdUnit4, no LSP).
- **Calibration integrity** is enforced in the workflow: each Haiku validator is built from a fixed template with zero prior-run context, so re-testing a patched skill measures the patch, not the history.
