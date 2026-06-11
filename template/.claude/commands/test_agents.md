---
disable-model-invocation: true
allowed-tools: Glob, Read, Grep, Workflow
description: Run integration tests for review/audit agents using synthetic fixtures
---

# Agent Integration Test Runner

Run synthetic integration tests that verify review and audit agents produce correct findings when given code with known violations. The deterministic orchestration (the fixture loop, CONTEXT+prompt assembly, the 5-clause assertion matcher, malformed-JSON-as-FAIL, and the PASS-only validator gate) lives in the workflow `.claude/workflows/test_agents_fixtures.js`. This command parses the fixtures, supplies the shared resources, and renders the result.

**Arguments:** `$ARGUMENTS`
- Empty or `all`: run all smoke fixtures
- Agent name (e.g., `error-hunter`): run only fixtures targeting that agent
- Specific fixture path (e.g., `smoke/pp_error_hunter`): run one fixture

---

## Phase 1: Load + Parse (Claude)

Workflow scripts cannot read files, so assemble everything here and pass it via `args` (push-don't-pull — keeps fixture content faithful).

1. **Glob the fixtures** per `$ARGUMENTS`:
   - empty/`all`: `.claude/tests/agent_fixtures/smoke/*.md`
   - agent name: glob smoke, keep fixtures whose `agent` metadata matches
   - path: that single fixture file
   If none match, STOP and report. **If any required file cannot be read, STOP and report the error.**

2. **Parse each fixture** into a structured object (the fixture format is fixed):
   - Metadata bullets → `agent`, `model`, `agent_source`, `checklistSection` (from `checklist_section`)
   - `## Synthetic Code` → `filePath` (the `File:` line) + `code` (the ```csharp block)
   - `## Synthetic Diff` → `diff` (the ```diff block)
   - `## Expected Findings` → `expected: { minCount, maxCount, required: [...], forbidden: [...] }`. Each Required/Forbidden entry parses `**action:** | **category:** | **file_contains:** | **keywords:**` into `{ action, category, fileContains, keywords: [split on comma] }`.
   - `## Transcript Corrections` (if present) → `transcriptCorrections`
   Build the `fixtures` array with `id` = fixture filename stem.

3. **Read the shared resources** and extract only what the in-scope fixtures need:
   - `.claude/commands/agents/review_agents.md` + `.claude/commands/agents/session_audit_agents.md` → for each distinct `agent`, extract its prompt-template code block (under its `### <agent-name>` header) into `agentTemplates[<agent>]`.
   - `.claude/commands/agents/checklists/code_quality.md` + `test_quality.md` → extract the sections referenced by the fixtures' `checklistSection` values into `checklists` keyed by `C+D+S` / `R+P` / `I` / `full` / `test` (per the section map: `C+D+S`=Compliance→Semantics, `R+P`=Robustness→Performance, `I`=Intuitiveness→EOF, `full`=whole code_quality, `test`=whole test_quality). Only include keys actually used.
   - `.claude/commands/agents/orchestrator_action_protocol.md` → full content into `findingSchema`.

---

## Phase 2: Invoke the fixture workflow

```
Workflow({
  scriptPath: ".claude/workflows/test_agents_fixtures.js",
  args: { fixtures: [...], agentTemplates: {...}, checklists: {...}, findingSchema: "..." }
})
```

The workflow assembles each agent prompt (CONTEXT = DIFF + full file + abbreviated arch rules + finding schema [+ transcript corrections]; static test-mode preamble forbidding Read/Grep/Glob), dispatches each fixture's agent at its fixture `model` (fan-out is safe — agents analyze only injected context, no file/LSP access), extracts the JSON findings array (malformed → FAIL per the old Step 2f), runs the 5-clause matcher (count bounds, every Required matched, no Forbidden matched, every finding's `agent` field correct), and on PASS-only dispatches a Haiku validator that overrides to FAIL on REJECT.

It returns: `{ total, passed, failed, results: [ { id, agent, pass, reason, findingCount, findings, validated, response } ] }`.

---

## Phase 3: Report Results (Claude)

**If the workflow returns `{ error }`** (e.g. no fixtures matched, or it was invoked with an empty set), print the error string and stop — do NOT render the table. Otherwise:

Present the summary table from `results`:

```
╔═══════════════════════════════════════════════════════════╗
║              AGENT INTEGRATION TEST RESULTS               ║
╠═══════════════════════════════════════════════════════════╣

FIXTURE                   AGENT                    RESULT  FINDINGS  VALIDATED
pp_code_reviewer          code-reviewer         PASS    2         YES
...

╠═══════════════════════════════════════════════════════════╣
║ Results: X passed, Y failed | Fixtures: Z total          ║
╚═══════════════════════════════════════════════════════════╝
```

For any FAIL (`pass: false`), show detail:

```
FAILURES:
  <id>:
    Reason: <reason>
    Raw findings: <findings or "none">
```

---

## Notes

- **Cost:** Smoke tier (~10 fixtures) ≈ $0.33–0.50 per run; validator adds ~$0.01/fixture (Haiku).
- **Parallel execution:** fixtures fan out concurrently in the workflow (no shared writes, no single-flight resource — agents never run tests or touch the LSP). The legacy "sequential for debugging" note no longer applies.
- **No file modifications:** read-only — never edits project files.
- **Fixture exclusion:** the `pattern_enforcer` hook excludes `.claude/tests/agent_fixtures/` so fixtures can contain intentional violations.
