---
description: Audit the documentation landscape and produce a prioritized report in Obsidian.
disable-model-invocation: true
allowed-tools: Bash(python3 .claude/tools/model_registry.py:*), Workflow
---

Audit the documentation landscape — produces a prioritized report in Obsidian.

**This command is read-only and advisory — it NEVER modifies existing docs.** Findings are recommendations, not auto-fixes.

## Architecture: Claude assembles → workflow audits → Claude writes

```
Phase 1: INVENTORY      (Claude — folder structure, read Start Here, build manifest + CONTEXT)
Phase 2+3: AUDIT        (workflow .claude/workflows/doc_architecture_audit.js — 3 parallel lens-agents + deterministic consolidation)
Phase 4: VERIFY+REPORT  (Claude — kill false-absence findings against the filesystem, then write the report)
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
If a doc "looks corrupted/all-null/missing," suspect dehydration before corruption.

### 1b. Build System Manifest
List `DevProjects/{{PROJECT_NAME}}/Claude/Documentation/` (recursion depth 2). Classify every subfolder by content per the **Folder Classification** rules in `agents/documentation_structure.md` (six kinds: archived, structural, domain, system, entity-doc, everything-else). Skip `archived` and `structural` folders entirely. For each remaining subfolder, build a manifest entry that carries its classification verdict:

```json
{ "systemName": "FolderName", "classification": "system | domain | entity-doc",
  "domain": "ParentDomainName or null", "files": ["..."],
  "hasQuickReference": true/false, "hasArchitecture": true/false,
  "hasUsage": true/false, "hasDesignerUsage": true/false, "hasRetrospective": true/false, "hasDesignDocument": true/false,
  "templateFormat": "4-doc | design-doc-only | mixed | empty" }
```

`classification` and `domain` are load-bearing: the manifest is the only channel that carries skip/grouping intent to the workflow's agents (push-don't-pull). In particular, `entity-doc` folders (e.g. `NPC/` with per-entity docs + a `BuildingBlocks/` subfolder) follow the entity-doc conventions `agents/documentation_structure.md` §Folder Classification assigns them and are **exempt from template-compliance checks (S1-S4)** — the structural lens honors that exemption only if the manifest tags them. There is **no hand-maintained exclusion list** — skipping is derived from classification (the canonical rules are content-based and stateless).

### 1c. Read Start Here
Read `DevProjects/{{PROJECT_NAME}}/Claude/Documentation/Start Here.md`. Parse all `[[wikilinks]]` + domain-table assignments, Role table entries (Designer/Developer), and entry-point links (Quick Reference vs Design Document).

### 1d. Assemble CONTEXT Block
Build a single CONTEXT string: the full System Manifest (JSON — including each entry's `classification` + `domain`) + the full Start Here content (raw markdown) + today's date + total system count.

**Inject source material verbatim — never annotate it inline.** A parenthetical inference you add (tagging a Start Here row `(BROKEN? not in tree)`) is indistinguishable from source to the lenses: they consume it as evidence, escalate on it, and then cite Start Here as independently corroborating a claim you invented. State the manifest's exclusions (`Archived/`, `Claude/`) explicitly so agents don't read manifest-absence as disk-absence; any other orchestrator observation goes in its own labelled section, never interleaved with quoted source.

---

## Phase 2+3: Invoke the audit workflow

> **Ordering gate — do NOT batch this with Phase 1.** The `args.context` here is built FROM the Phase-1 inventory results (manifest + Start Here). That makes this call **dependent**, not independent: it must run in a *separate turn*, after the Phase-1 reads have returned and you have SEEN the real folder structure. Batching the `Workflow(...)` launch in the same tool block as the inventory `Glob`/`find`/`Read` forces you to pass a guessed manifest — the workflow then audits systems that don't exist. The harness's "parallelize independent calls" guidance does not apply to a call whose arguments are another call's output. See `feedback_dont_batch_dependent_call_with_its_inputs` in auto-memory.

Resolve the three audit lenses' `fanout` work shape with `python3 .claude/tools/model_registry.py for-role fanout` and the effort cell in `reference/model_ladder_evidence.md`. Pick a native model ID for this session; the workflow validates it against the provider guard's injected vocabulary before starting a lens. Do not copy a literal pin from an earlier session.

```
Workflow({
  scriptPath: ".claude/workflows/doc_architecture_audit.js",
  args: { context: "<the CONTEXT block from 1d>", docRoot: "<absolute path to .../Claude/Documentation>",
          model: "<native model ID>", effort: "<legal ladder rung>", tier: "fanout" }
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

(No single-flight exposure — agents read only vault `.md`; no GdUnit4, no LSP.)

Consume the checked findings per [`orchestrator_action_protocol.md`](agents/orchestrator_action_protocol.md) Step 1.5 before `/doc_audit_fix` reads the report.

---

## Phase 4: Report (Claude)

### 4a. Absence-Claim Verification Gate (MANDATORY — before computing anything)

Lens agents produce **false absences**: they read a bounded slice and report the unread remainder as missing. Confirming presence needs one hit; confirming absence needs exhaustive coverage — and an agent cannot distinguish "I looked everywhere" from "I stopped early." Treat every absence-shaped finding as unverified until you check it yourself.

**Qualifying shape** — the description asserts something does not exist: *missing, absent, broken, unresolvable, does not link back, not found, orphaned, no longer exists*. Usual carriers: X1, X2, S1, S2, D1, D5.

| Claim | Verification | Trap it catches |
|---|---|---|
| "wikilink `[[Title]]` resolves to nothing" | `Get-ChildItem -LiteralPath <VAULT ROOT> -Recurse -Filter "Title.md"` — search the **whole vault**, never just `Documentation/` | Obsidian resolves bare-title wikilinks vault-wide; targets legitimately live in `Claude/Design/`, `Claude/Archived/` |
| "file/doc does not exist" | `ls` the containing folder on disk | `Archived/` + `Claude/` are excluded from the manifest **by construction** — manifest-absence ≠ disk-absence |
| "System A's Related Systems omits B" | `grep -n "Related Systems" -A 40 <A's QR>`, read to the callout's last `>` line | Lens enumerations truncate; the "missing" entry usually sits just past where it stopped counting |
| "section/heading is missing" | `grep -n "^#" <file>` | Same truncation |

**Disposition:**
- **Verified present** → the finding is FALSE. Do not emit it as critical/warning/info. Record it under a `## Invalidated Findings` section (claim + verification + verdict) and set `status: "resolved"` in the Machine Findings block.
- **Verified absent** → keep at the lens's severity.
- **Cannot verify** → drop it (per *No hallucinated findings*).

**Recompute after the gate, never before.** Invalidations move the numbers: an invalidated X1 returns its edges to `linkGraph.bidirectional` (decrementing `unidirectional` and `unidirectionalBySystem`), which changes Link Integrity. Statistics, counts, `healthRating`, and `ratingBasis` are all computed from the **post-gate** finding set, re-applying the workflow's own threshold definitions (canonical there — do not invent new ones).

**Two lenses disagreeing about one file+line is a gate trigger, not a cross-agent pattern.** Dedup keys on `system+check`, so contradictory verdicts filed under different check codes both survive the merge. Verify, then keep at most one.

### 4b. Statistics and report

Compute the Statistics-table metrics from your Phase-1 manifest + the workflow's findings/linkGraph using these explicit formulas (denominators count only `classification=="system"` folders unless noted):
- **Template Compliance** = (# systems with the full 4-doc suite — `templateFormat` is `4-doc` **OR** `mixed`) / (# system folders). A `mixed` system carries all four canonical docs PLUS a legacy Design Document, so it IS compliant; only `design-doc-only` and `empty` fail. (Counting `templateFormat=="4-doc"` alone would wrongly mark suite-complete `mixed` systems as non-compliant.)
- **Link Integrity** = `linkGraph.bidirectional` / `linkGraph.edges` (or `N/A` when `linkGraph` is null — see Report Rules)
- **Start Here Coverage** = (# systems appearing in Start Here "By Domain") / (# system folders)
- **Naming Consistency** = (# systems whose usage doc is named "Designer Usage") / (# systems with any usage doc)
- **Domain Cohesion** = (# domain folders with no D6 mixed-purpose finding) / (# domain folders)

Then write the report to Obsidian with a single overwrite:

**Path:** `DevProjects/{{PROJECT_NAME}}/Claude/Documentation/Claude/Audit Reports/Documentation Audit.md` (`createIfNeeded: true`). This lives under `Claude/` deliberately — that folder classifies as **Structural** (`documentation_structure.md`) and is skipped by future audits, so the report never self-ingests. Do not relocate it outside `Claude/`.

### Report Template

Write the report in the shape of [`doc_architecture_audit_report.md`](reference/doc_architecture_audit_report.md) §Report Template.

### Machine Findings Block

After the Changelog, append the `## Machine Findings` section that `/doc_audit_fix` parses. [`doc_architecture_audit_report.md`](reference/doc_architecture_audit_report.md) §Machine Findings Block owns its shape and rules.

### Report Rules
- **Partial-audit banner:** if the workflow return reports `lensesCompleted < 3`, prepend a `> [!danger] PARTIAL AUDIT — only N/3 lenses completed ({failed lens names})` banner above the Audit Summary. A 2-of-3-lens run must never be presented as complete.
- **Null `linkGraph`:** if the workflow returns `linkGraph: null` (cross-ref lens failed or omitted it), render the **Link Integrity** statistics row as `N/A (link graph unavailable)` — never fabricate edge counts.
- **Changelog handling:** `Read` any existing report at the audit path first. If present, re-emit every prior Changelog row above the new row (new row's Notes = `Δ critical {±n}, warning {±n}, info {±n} vs {prev date}`). If absent, emit the Changelog header + a single first row (Notes = `First audit`). The full report body is overwritten each run — only the Changelog rows accumulate.
- Use collapsible callouts for all finding details (not `###` headers).
- Keep recommendations concrete and imperative ("Add bidirectional link from X to Y", not "Consider updating links").
- Include `/doc_full SystemName` commands in the action plan wherever a system needs template migration or doc regeneration.
- Domain Analysis tables should show the complete picture — every system in every domain, whether it has issues or not.

---

## Constraints

- **Read-only.** Only write to the audit report path.
- **Advisory.** Present findings for user decision — do not auto-fix anything (the user-confirmed FIX/ASK walkthrough is `/doc_audit_fix`).
- **Push-don't-pull.** Claude reads Start Here + folder structure and injects them into the CONTEXT; the workflow's agents do NOT re-read Start Here.
- **No hallucinated findings.** Only report issues verified by reading actual files. If uncertain, skip it.
- **3-lens delegation lives in the workflow.** The workflow spawns exactly 3 agents — do not perform the audit inline or combine lenses.
