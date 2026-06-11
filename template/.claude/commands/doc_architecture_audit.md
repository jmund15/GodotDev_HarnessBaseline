---
disable-model-invocation: true
---

Audit the documentation landscape — produces a prioritized report in Obsidian.

**This command is read-only and advisory — it NEVER modifies existing docs.** Findings are recommendations, not auto-fixes.

## Architecture: Claude assembles → workflow audits → Claude writes

```
Phase 1: INVENTORY      (Claude — folder structure, read Start Here, build manifest + CONTEXT)
Phase 2+3: AUDIT        (workflow .claude/workflows/doc_architecture_audit.js — 3 parallel lens-agents + deterministic consolidation)
Phase 4: REPORT         (Claude — write audit report to Obsidian from the workflow's structured output)
```

The 3 lens-agent prompts (checks S1-S4 / X1-X4 / D1-D7), the parallel barrier, the dedup, the cross-agent-pattern detection, and the health-rating thresholds all live canonically in the workflow — do not re-implement them here.

---

## Phase 1: Inventory (Claude)

### 1a. Vault tooling
Documentation reads/writes use native `Read`/`Write`/`Edit`/`Grep`/`Glob` on the vault path — see the `obsidian_conventions` skill. No MCP-connectivity gate.

**Pre-hydrate the vault FIRST (MANDATORY — do this before 1b).** The vault lives under OneDrive (Files-On-Demand), so docs are frequently cloud-only placeholders: native git-bash reads return "No such file or directory" and the Phase-2 lens-agents (which read docs natively) then report **false absences → a fabricated audit**. Recall every doc to local disk and confirm zero remain dehydrated:
```powershell
$root = "{{VAULT_ROOT}}\DevProjects\{{PROJECT_NAME}}\Claude\Documentation"
Get-ChildItem -LiteralPath $root -Recurse -Filter *.md -Force | ForEach-Object { Get-Content -LiteralPath $_.FullName -Raw | Out-Null }
@(Get-ChildItem -LiteralPath $root -Recurse -Filter *.md -Force | Where-Object { $_.Attributes.value__ -band 4194304 }).Count  # MUST print 0
```
If a doc "looks corrupted/all-null/missing," suspect dehydration before corruption — see `gotcha_onedrive_dehydration_breaks_vault_reads` in auto-memory.

### 1b. Build System Manifest
List `DevProjects/{{PROJECT_NAME}}/Claude/Documentation/` (recursion depth 2). Classify every subfolder by content per the **Folder Classification** rules in `agents/documentation_structure.md` (six kinds: archived, structural, domain, system, entity-doc, everything-else). Skip `archived` and `structural` folders entirely. For each remaining subfolder, build a manifest entry that carries its classification verdict:

```json
{ "systemName": "FolderName", "classification": "system | domain | entity-doc",
  "domain": "ParentDomainName or null", "files": ["..."],
  "hasQuickReference": true/false, "hasArchitecture": true/false,
  "hasUsage": true/false, "hasDesignerUsage": true/false, "hasRetrospective": true/false, "hasDesignDocument": true/false,
  "templateFormat": "4-doc | design-doc-only | mixed | empty" }
```

`classification` and `domain` are load-bearing: the manifest is the only channel that carries skip/grouping intent to the workflow's agents (push-don't-pull). In particular, `entity-doc` folders (e.g. `NPC/` with per-entity docs + a `BuildingBlocks/` subfolder) follow `/doc_npc` conventions and are **exempt from template-compliance checks (S1-S4)** — the structural lens honors that exemption only if the manifest tags them. There is **no hand-maintained exclusion list** — skipping is derived from classification (the canonical rules are content-based and stateless).

### 1c. Read Start Here
Read `DevProjects/{{PROJECT_NAME}}/Claude/Documentation/Start Here.md`. Parse all `[[wikilinks]]` + domain-table assignments, Role table entries (Designer/Developer), and entry-point links (Quick Reference vs Design Document).

### 1d. Assemble CONTEXT Block
Build a single CONTEXT string: the full System Manifest (JSON — including each entry's `classification` + `domain`) + the full Start Here content (raw markdown) + today's date + total system count. Archived/structural folders are excluded by construction in 1b; do not assemble a separate "exclusion list" component (the canonical rules are content-based — see `documentation_structure.md`).

---

## Phase 2+3: Invoke the audit workflow

> **Ordering gate — do NOT batch this with Phase 1.** The `args.context` here is built FROM the Phase-1 inventory results (manifest + Start Here). That makes this call **dependent**, not independent: it must run in a *separate turn*, after the Phase-1 reads have returned and you have SEEN the real folder structure. Batching the `Workflow(...)` launch in the same tool block as the inventory `Glob`/`find`/`Read` forces you to pass a guessed manifest — the workflow then audits systems that don't exist. The harness's "parallelize independent calls" guidance does not apply to a call whose arguments are another call's output. See `feedback_dont_batch_dependent_call_with_its_inputs` in auto-memory.

```
Workflow({
  scriptPath: ".claude/workflows/doc_architecture_audit.js",
  args: { context: "<the CONTEXT block from 1d>", docRoot: "<absolute path to .../Claude/Documentation>" }
})
```

The workflow spawns **exactly 3** lens-agents in parallel (`da-structural`, `da-crossref`, `da-domain`) over the shared CONTEXT — each reads vault docs natively within its tool budget and returns schema-validated findings (it does not re-read Start Here). It then deduplicates by `system+check` (keeping higher severity), sorts by severity, detects cross-agent patterns (systems flagged by 2+ agents — including null-system D-level/S4 findings attributed to a concrete system when their scope names exactly one already-flagged system), and computes the health rating (X1 unidirectional-link warnings are de-weighted, so a pile of missing back-links alone caps at MINOR DRIFT — MODERATE DEBT requires genuine structural debt).

It returns:
```
{ findings: [ {agent, check, severity, action, system, description, recommendation, handler, options, scope} ],
  crossAgentPatterns: [ {system, agents, checks} ], counts: {critical, warning, info},
  healthRating: "HEALTHY|MINOR DRIFT|MODERATE DEBT|NEEDS OVERHAUL",
  ratingBasis: {x1Warnings, structuralWarnings},
  linkGraph: {edges, bidirectional, unidirectional, unidirectionalBySystem: {SystemName: count}},
  lensStatus: {structural, crossref, domain}, lensesCompleted: N, failedLenses: [...] }
```

(No single-flight exposure — agents read only vault `.md`; no GdUnit4, no LSP. No user gate — this command is advisory; the user-confirmed FIX/ASK walkthrough lives in the separate `/doc_audit_fix`.)

---

## Phase 4: Report (Claude)

Compute the Statistics-table metrics from your Phase-1 manifest + the workflow's findings/linkGraph using these explicit formulas (denominators count only `classification=="system"` folders unless noted):
- **Template Compliance** = (# systems with the full 4-doc suite — `templateFormat` is `4-doc` **OR** `mixed`) / (# system folders). A `mixed` system carries all four canonical docs PLUS a legacy Design Document, so it IS compliant; only `design-doc-only` and `empty` fail. (Counting `templateFormat=="4-doc"` alone would wrongly mark suite-complete `mixed` systems as non-compliant.)
- **Link Integrity** = `linkGraph.bidirectional` / `linkGraph.edges` (or `N/A` when `linkGraph` is null — see Report Rules)
- **Start Here Coverage** = (# systems appearing in Start Here "By Domain") / (# system folders)
- **Naming Consistency** = (# systems whose usage doc is named "Designer Usage") / (# systems with any usage doc)
- **Domain Cohesion** = (# domain folders with no D6 mixed-purpose finding) / (# domain folders)

Then write the report to Obsidian with a single overwrite:

**Path:** `DevProjects/{{PROJECT_NAME}}/Claude/Documentation/Claude/Audit Reports/Documentation Audit.md` (`createIfNeeded: true`). This lives under `Claude/` deliberately — that folder classifies as **Structural** (`documentation_structure.md`) and is skipped by future audits, so the report never self-ingests. Do not relocate it outside `Claude/`.

### Report Template

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

### Machine Findings Block

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

### Report Rules
- **Partial-audit banner:** if the workflow return reports `lensesCompleted < 3`, prepend a `> [!danger] PARTIAL AUDIT — only N/3 lenses completed ({failed lens names})` banner above the Audit Summary. A 2-of-3-lens run must never be presented as complete.
- **Null `linkGraph`:** if the workflow returns `linkGraph: null` (cross-ref lens failed or omitted it), render the **Link Integrity** statistics row as `N/A (link graph unavailable)` — never fabricate edge counts.
- **Changelog handling:** `Read` any existing report at the audit path first. If present, re-emit every prior Changelog row above the new row (new row's Notes = `Δ critical {±n}, warning {±n}, info {±n} vs {prev date}`). If absent, emit the Changelog header + a single first row (Notes = `First audit`). The full report body is overwritten each run — only the Changelog rows accumulate.
- Use collapsible callouts for all finding details (not `###` headers).
- Keep recommendations concrete and imperative ("Add bidirectional link from X to Y", not "Consider updating links").
- Include `/doc_full SystemName` commands in the action plan wherever a system needs template migration or doc regeneration.
- Domain Analysis tables should show the complete picture — every system in every domain, whether it has issues or not.
- The `## Machine Findings` JSON block is the machine contract for `/doc_audit_fix` — emit it on every run and keep its finding `id`s in sync with the prose callouts.

---

## Constraints

- **Read-only.** NEVER modify existing documentation files. Only write to the audit report path.
- **Advisory.** Present findings for user decision — do not auto-fix anything (the user-confirmed FIX/ASK walkthrough is `/doc_audit_fix`).
- **Push-don't-pull.** Claude reads Start Here + folder structure and injects them into the CONTEXT; the workflow's agents do NOT re-read Start Here.
- **No hallucinated findings.** Only report issues verified by reading actual files. If uncertain, skip it.
- **3-lens delegation lives in the workflow.** The workflow spawns exactly 3 agents — do not perform the audit inline or combine lenses.
- **Time-bounded.** Full audit (inventory → report) should complete in under 15 minutes.
