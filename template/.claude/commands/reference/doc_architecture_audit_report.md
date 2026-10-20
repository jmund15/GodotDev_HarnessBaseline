---
disable-model-invocation: true
---

# `/doc_architecture_audit` — Report template and Machine Findings block

Read at `/doc_architecture_audit` Phase 4b when writing the report: the template for the prose report, then the `## Machine Findings` block appended after its Changelog. Entrypoint: [`../doc_architecture_audit.md`](../doc_architecture_audit.md).

## Report Template

```markdown
# Documentation Architecture Audit

> [!abstract] Audit Summary
> **Date:** YYYY-MM-DD
> **Health Rating:** HEALTHY | MINOR DRIFT | MODERATE DEBT | NEEDS OVERHAUL
> **Systems Audited:** N
> **Findings:** X critical, Y warning, Z info
> **Cross-Agent Patterns:** N systems flagged by multiple agents

## Statistics

| Metric | Value | Target |
|--------|-------|--------|
| Template Compliance (4-doc suite present) | X/Y (Z%) | 100% |
| Link Integrity (bidirectional) | X/Y edges (Z%) | 100% |
| Start Here Coverage | X/Y systems (Z%) | 100% |
| Naming Consistency (Designer Usage) | X/Y usage docs (Z%) | 100% |
| Domain Cohesion (no mixed-purpose domains) | X/Y domains cohesive (Z%) | 100% |

## Critical Findings

> [!danger]- C1: {description} ({system})
> **Check:** {check code} | **Agent:** {agent} | **Action:** {FIX|ASK}
> **Handler:** {handler or "User decision"}
> **Recommendation:** {recommendation}
> **Options:** {ranked options if ASK, null if FIX}
> **Scope:** {scope list}

(Repeat for each critical finding. Omit section if none.)

## Invalidated Findings

> [!success]- {id} (INVALIDATED): {what the lens claimed} ({system})
> **Check:** {check code} | **Agent:** {agent} | **Reported severity:** {severity} | **Verified verdict:** NOT A DEFECT | DUPLICATE
> **Claim:** {the assertion, quoted}
> **Verification:** {the command run and what it returned}
> **Action:** none.

(Findings killed by the Phase-4a gate. Omit section if none. These do NOT count toward the Findings totals.)

## Warning Findings

> [!warning]- W1: {description} ({system})
> **Check:** {check code} | **Agent:** {agent} | **Action:** {FIX|ASK}
> **Handler:** {handler or "User decision"}
> **Recommendation:** {recommendation}
> **Options:** {ranked options if ASK, null if FIX}
> **Scope:** {scope list}

(Repeat for each warning. Omit section if none.)

## Info Findings

> [!info]- I1: {description} ({system})
> **Check:** {check code} | **Agent:** {agent} | **Action:** {FIX|ASK}
> **Handler:** {handler or "User decision"}
> **Recommendation:** {recommendation}
> **Options:** {ranked options if ASK, null if FIX}
> **Scope:** {scope list}

(Repeat for each info finding. Omit section if none.)

## Cross-Agent Patterns

| System | Agents | Findings | Summary |
|--------|--------|----------|---------|
| {SystemName} | da-structural, da-crossref | S2, X1 | {one-line summary} |

(Omit section if no cross-agent patterns. The **Summary** cell is Claude-synthesized — join the descriptions of that system's findings into one line; the workflow's `crossAgentPatterns` objects carry only `{system, agents, checks}`, no summary field.)

## Domain Analysis

### {Domain Name}
| System | Template | QR | Arch | Usage | Retro | Start Here | Links |
|--------|----------|----|------|-------|-------|------------|-------|
| {Name} | 4-doc ✓ | ✓  | ✓    | ✓     | ✓     | ✓          | ✓     |

(One table per domain — group systems by their manifest `domain` field. **Links** column: read `linkGraph.unidirectionalBySystem[systemName]` — `✓` if absent or 0; `⚠ N→` if N>0; `—` when `linkGraph` is unavailable. The workflow emits this per-system map, so do not hand-parse finding descriptions, and do not fabricate `N/N ↔` bidirectional ratios. As a guard, the per-system `N→` values should sum to `linkGraph.unidirectional`.)

## Domain Hub Recommendations

> [!tip]- Hub: {Domain Name}
> **Systems:** {list}
> **Rationale:** {why these form a pipeline/chain}
> **Suggested scope:** {what the hub doc should cover}

(Omit section if no hub recommendations.)

## Domain Reorganization Recommendations

> [!warning]- Promote: {New Domain Name} (from {Current Domain})
> **Systems to move:** {list}
> **Remaining in {Current Domain}:** {list}
> **Rationale:** {why these systems are functionally distinct from the parent domain}
> **Proposed structure:**
> ```
> Documentation/
>   {New Domain Name}/
>     _Hub.md
>     {System1}/
>     {System2}/
> ```
> **Migration impact:** {wikilinks to update, Start Here changes needed}

(Omit section if no reorganization recommendations.)

## Domain Merge Recommendations

> [!warning]- Merge: {Domain A} + {Domain B}
> **Combined systems:** {count} ({list})
> **Shared cross-references:** {what links the two domains}
> **Rationale:** {why neither has a strong standalone identity}
> **Options:** {ranked merge options from the D7 finding's `options` field}

(Consumes D7 merge-candidate findings. Omit section if none.)

## Recommended Action Plan

1. **[CRITICAL]** {action description}
   → `Run: /doc_full {SystemName}` or specific manual fix
2. **[WARNING]** {action description}
   → `Run: /doc_start_here_update` or specific manual fix
3. **[INFO]** {action description}
   → Suggestion for future session

## Changelog

| Date | Health | Critical | Warning | Info | Notes |
|------|--------|----------|---------|------|-------|
| YYYY-MM-DD | RATING | X | Y | Z | First audit / Delta from previous |
```

## Machine Findings Block

After the Changelog, append a `## Machine Findings` section whose body is a single fenced `json` block carrying the workflow's structured return. This is the **machine contract** consumed by `/doc_audit_fix` — the prose callouts above are the human view; this block is the source of truth. The literal heading must be `## Machine Findings` so the fix command can locate it. Shape:

```json
{
  "schemaVersion": 1,
  "date": "YYYY-MM-DD",
  "healthRating": "HEALTHY | MINOR DRIFT | MODERATE DEBT | NEEDS OVERHAUL",
  "lensesCompleted": 3,
  "failedLenses": [],
  "counts": { "critical": 0, "warning": 0, "info": 0 },
  "linkGraph": { "edges": 0, "bidirectional": 0, "unidirectional": 0, "unidirectionalBySystem": { "SystemName": 0 } },
  "crossAgentPatterns": [ { "system": "Name", "agents": ["da-structural", "da-crossref"], "checks": ["S2", "X1"] } ],
  "findings": [
    { "id": "C1", "status": "open", "agent": "da-structural", "check": "S1", "severity": "critical",
      "action": "FIX", "system": "Name or null", "description": "...", "recommendation": "...",
      "handler": "delegate:/doc_full | inline:rename | ... | null", "options": null, "scope": ["..."] }
  ]
}
```

Rules for the block:
- Every finding's `id` MUST match the ID used in its prose callout (C1 / W3 / I7) — the two views share IDs.
- `status` is `open` on a fresh audit; `/doc_audit_fix` flips it to `resolved` / `deferred` and regenerates the block.
- Copy each finding **verbatim** from the workflow return (`agent/check/severity/action/system/description/recommendation/handler/options/scope`) and add only `id` + `status` — never re-summarize.
- `schemaVersion` is `1`; bump only if this block's shape changes.
