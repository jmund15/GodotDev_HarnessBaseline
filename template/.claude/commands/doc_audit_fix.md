---
disable-model-invocation: true
---

Apply fixes from the documentation architecture audit report.

**This command modifies documentation files.** It reads the audit report produced by `/doc_architecture_audit` and acts on its findings.

## Architecture: 8-Phase Orchestrator (No Sub-Agents)

The fix command is a thin dispatcher — it delegates to existing commands (`/doc_full`, `/doc_start_here_update`) or performs inline Obsidian MCP operations. No parallel agents needed.

```
Phase 1: INGEST          (read audit report, parse findings, validate freshness)
Phase 2: CLASSIFY        (assign action/handler, detect dependencies, group by execution order)
Phase 3: PRESENT         (show FIX/ASK summary, get user confirmation)
Phase 4: EXECUTE FIX     (batch-apply FIX findings via handlers)
Phase 5: WALK ASK        (present each ASK with ranked options, execute per user direction)
Phase 6: VERIFY          (re-read modified files, spot-check fixes)
Phase 7: REPORT          (update audit report with resolution status)
Phase 8: HIERARCHY       (generate folder reorganization plan for user to execute in Obsidian)
```

---

## Arguments

```
$ARGUMENTS — Optional filter:
  (empty)      → process all findings
  --critical   → critical findings only
  --warnings   → critical + warning findings
  C1           → single finding by ID
  W3-W5        → range of findings
  --dry-run    → classify and present (Phase 1-3) but do not execute
```

---

## Phase 1: INGEST

### 1a. Vault tooling
Documentation reads/writes use native `Read`/`Write`/`Edit`/`Grep`/`Glob` on the vault path — see the `obsidian_conventions` skill. No MCP-connectivity gate.

### 1b. Read Audit Report
Use `obsidian_read_note` to read `DevProjects/{{PROJECT_NAME}}/Claude/Documentation/Claude/Audit Reports/Documentation Audit.md`.

If no audit report exists, abort: **"No audit report found. Run `/doc_architecture_audit` first."**

### 1c. Parse Findings

**Primary — Machine Findings block.** Locate the `## Machine Findings` section and `JSON.parse` its fenced `json` block. This is the machine contract written by `/doc_architecture_audit` (`schemaVersion` 1): use its `findings[]` array directly — each object already carries `id`, `severity`, `check`, `agent`, `system`, `description`, `recommendation`, `action`, `handler`, `options`, `scope`, and `status`. Skip any finding whose `status` is `resolved` or `deferred`. If `schemaVersion` is greater than 1, warn ("report written by a newer audit — parsing defensively") and ignore unknown fields rather than aborting. If the block is present but does not `JSON.parse` (malformed), warn and fall back to the legacy prose parse below rather than aborting.

**Fallback — legacy prose parse.** If the report has no `## Machine Findings` block (it predates the JSON contract), parse the finding callouts from the Critical/Warning/Info sections into the shape below, and skip findings marked `✅ RESOLVED` / `⏸ DEFERRED`:

```json
{
  "id": "C1 | W3 | I7",
  "severity": "critical | warning | info",
  "check": "S1-S4 | X1-X4 | D1-D7",
  "agent": "da-structural | da-crossref | da-domain",
  "system": "SystemName or null",
  "description": "What the issue is",
  "recommendation": "Specific actionable fix",
  "action": "FIX | ASK | null (old schema)",
  "handler": "handler string or null",
  "options": ["ranked options"] | null,
  "scope": ["affected paths"],
  "status": "open | resolved | deferred"
}
```

### 1d. Freshness Check
Extract the audit date from the `> [!abstract] Audit Summary` callout. If the audit is **>7 days old**, warn:
> "Audit report is {N} days old (from {date}). Findings may be stale. Recommend re-running `/doc_architecture_audit` first. Proceed anyway?"

### 1e. Apply Scope Filter
If `$ARGUMENTS` specifies a filter, apply it:
- `--critical` → keep only findings with `severity: "critical"`
- `--warnings` → keep findings with `severity: "critical"` or `severity: "warning"`
- `C1` → keep only finding with id `C1`
- `W3-W5` → keep findings W3, W4, W5
- `--dry-run` → flag for Phase 3 early exit (no execution)

---

## Phase 2: CLASSIFY

### 2a. Determine Action and Handler

For each parsed finding, determine `action` and `handler`:

**If the finding has `action` and `handler` fields (new schema):** Use directly.

**If the finding lacks these fields (old schema):** Derive from the check-to-action routing table:

| Check | Default Action | Handler | ASK Override |
|-------|---------------|---------|--------------|
| **S1** (incomplete template) | FIX | `delegate:/doc_full` | If system is design-doc-only with no dev history → ASK: "Generate docs now or wait?" |
| **S2** (template compliance) | FIX | `delegate:/doc_usage` or `delegate:/doc_architecture` | — |
| **S3** (formatting compliance) | FIX | `delegate:/doc_usage` or `delegate:/doc_architecture` | — |
| **S4** (naming: Usage→Designer Usage) | FIX | `inline:rename` | — |
| **S4** (orphan file) | ASK | null | Options: Reference from QR / Archive / Delete / Leave |
| **X1** (unidirectional links) | FIX | `inline:search_replace` | — |
| **X2** (broken wikilinks) | FIX | `inline:search_replace` | — |
| **X3** (boundary overlap) | ASK | null | Options: System A owns / System B owns / Shared |
| **X4** (shared file cross-refs) | FIX | `inline:search_replace` | — |
| **D1** (Start Here coverage) | FIX | `delegate:/doc_start_here_update` | — |
| **D2** (domain misclassification) | ASK | null | Options: Move to Domain B / Keep in Domain A |
| **D3** (entry point drift) | FIX | `delegate:/doc_start_here_update` | — |
| **D4** (hub recommendation) | ASK | null | Options: Create hub now / Defer / Not needed |
| **D5** (archived hygiene) | ASK | null | Options: Remove reference / Update to successor / Keep |
| **D6** (domain promotion) | ASK | null | Options: Full promotion / Hub only / Defer |
| **D7** (domain merge) | ASK | null | Options: Merge into one / Keep separate / Defer |

### 2b. Dependency Detection

Build execution order based on these dependency rules:

1. **S1 → X1/X4**: Complete templates must resolve BEFORE adding return links to those systems (can't add Related Systems entry to a QR that doesn't exist yet)
2. **S4 naming → X2**: Rename files must resolve BEFORE fixing wikilinks targeting renamed files
3. **D6 → D1/D2/D3**: Domain promotion must resolve BEFORE Start Here updates for affected systems

Topological sort findings into execution groups. Within each group, order by: critical first, then FIX before ASK.

### 2c. Group by Handler

Separate into `fixFindings[]` and `askFindings[]`.

Within `fixFindings`, group by handler type for batching:
- **`delegate` group**: batch by command (all `/doc_full` calls together, then `/doc_start_here_update`)
- **`inline` group**: batch by target file (combine `obsidian_search_replace` calls on the same file)

---

## Phase 3: PRESENT

Display the fix plan following the orchestrator action protocol's presentation pattern (adapted for docs):

```
## Documentation Fix Plan

### FIX — Auto-applied on confirmation ({count})

**Delegate: /doc_full** ({count} systems)
1. [S1] {SystemName} — missing {list of missing docs}

**Inline: obsidian_search_replace** ({count} edits across {N} files)
2. [X2] Fix broken wikilink in {SystemA} QR → {SystemB}
3. [X1] Add {SystemA} to {SystemB} Related Systems

**Delegate: /doc_start_here_update** (covers {count} findings)
4. [D1] Add missing By Role entries

### ASK — Needs your input ({count})
5. [D6] {description}
   → Options: (1) {recommended} [Recommended], (2) {option}, (3) {option}
6. [X3] {description}
   → Options: (1) {recommended} [Recommended], (2) {option}, (3) {option}

### Dependencies
- #{N} must complete before #{M} ({reason})
```

**Confirmation prompt:**
> "Ready to proceed? I'll apply {N} FIX changes, then walk through {M} ASK items for your input."

**If `--dry-run` was specified:** Present the plan and exit. Do not execute Phases 4-7.

---

## Phase 4: EXECUTE FIX

Execute in dependency order, respecting the grouping from Phase 2c.

### 4a. Delegate Commands First

Delegate commands create/update files that inline fixes may depend on.

**`/doc_full` (S1 findings):**
- Invoke `/doc_full {SystemName}` for each system needing template completion
- Execute **sequentially** — each `/doc_full` spawns 3 subagents and is context-heavy
- **Cap at 3 systems per invocation.** If more than 3 need `/doc_full`, process 3 and report: "Fixed 3/{N} systems. Run `/doc_audit_fix` again for the remaining {N-3}."

**`/doc_usage` or `/doc_architecture` (S2/S3 findings):**
- Invoke the appropriate command for the non-compliant doc
- Sequential, same cap applies (counts toward the 3-delegate limit)

### 4b. Inline Fixes Second

After delegate commands complete (new files exist), apply inline fixes.

**Rename (S4 — Usage → Designer Usage):**
1. `obsidian_read_note` — read the old file
2. `obsidian_update_note` — write content to new path (`Designer Usage.md`), `createIfNeeded: true`
3. `obsidian_delete_note` — delete old file
4. `obsidian_global_search` — find all references to old filename
5. `obsidian_search_replace` — fix references in each found file

**Search-replace (X1, X2, X4 — link fixes):**
- Group all replacements targeting the same file into a single `obsidian_search_replace` call
- **Idempotent**: Before replacing, verify the old text exists. If the fix is already applied (old text not found), skip silently.

**X1 (add missing Related Systems entry):**
- Read the target QR, find the `> [!info] Related Systems` callout
- Add the missing `> - [[{System}/Quick Reference|{System}]] — {relationship}` line
- Use `obsidian_search_replace` with the existing callout content as search, expanded content as replace

**X2 (fix broken wikilink):**
- Replace the broken link text with the corrected link
- Use `obsidian_search_replace` on the file containing the broken link

### 4c. Start Here Reconciliation Last

After all file moves, renames, and link fixes:
- Invoke `/doc_start_here_update` **once** — it covers all D1 and D3 findings by reconciling against the folder structure
- This is always last because it needs to see the final state of all files

### 4d. Build Verification

After all FIX findings are applied, report what was done:
> "Applied {N} FIX changes: {summary}. Moving to ASK items."

---

## Phase 5: WALK ASK

Process each ASK finding following the [Orchestrator Action Protocol](agents/orchestrator_action_protocol.md):

### For Each ASK Finding:

1. **Present** the description with ranked options (recommended first, marked `[Recommended]`):
   > **[{id}] {description}**
   > (1) {recommended option} [Recommended]
   > (2) {option}
   > (3) {option}

2. **Wait for user input.** If user responds with:
   - A number → apply that option
   - "yes" or bare confirmation → apply option (1) (the recommended one)
   - "skip" → mark as deferred, continue to next

3. **Execute the chosen option immediately** before moving to the next ASK:

   **D2 (reclassify domain):** Run `/doc_start_here_update` which handles domain table moves.

   **D4 (create hub):** Write the Hub document using the template below, then `obsidian_update_note`:

   ```markdown
   # {DomainName} — Domain Hub

   > [!abstract] Domain Overview
   > 2-3 sentences about this domain and its role in the project.

   ## Systems in This Domain

   | System | Entry Point | Description |
   |--------|-------------|-------------|
   | {SystemName} | [[{SystemPath}/Quick Reference|{DisplayName}]] | One-line description |

   ## Pipeline / Dependency Flow

   Brief description of how systems in this domain interact.

   ## Cross-Domain Interfaces

   > [!info]- {DomainName} → {OtherDomain}
   > **Integration point:** {what connects them}
   > **Contract:** {interface, resource type, signal}
   > **Key files:** `{paths}`

   ## Related Domains
   - [[{OtherDomain}/_Hub|{OtherDomain}]] — How they relate
   ```

   **D5 (archived hygiene):**
   - "Remove reference": `obsidian_search_replace` to delete the stale link from the active doc
   - "Update to successor": `obsidian_search_replace` to replace the archived link with the successor link

   **D6 (domain promotion):** Deferred to Phase 8 (Folder Hierarchy).
   - Record the promoted domain and its systems for inclusion in the Phase 8 reorganization plan
   - Do NOT move files programmatically — Bash `mv` and Obsidian MCP read/write/delete do NOT trigger Obsidian's auto-link-update feature
   - File moves must be done by the user via Obsidian's UI (drag-drop or right-click → Move) to get automatic wikilink updates
   - Phase 8 generates the exact move instructions

   **X3 (boundary overlap):**
   - Add ownership note to the designated system's QR (in Core Types or a new callout)
   - Add cross-reference to the other system's Related Systems if missing

4. **Cascade check**: If a resolved ASK unblocks downstream FIX findings (from Phase 2b dependency graph), execute those FIX findings before the next ASK.

---

## Phase 6: VERIFY

After all fixes and ASK resolutions:

1. **Spot-check modified files** — for each file that was modified, `obsidian_read_note` and verify:
   - Wikilinks use correct format: `[[{SystemPath}/Quick Reference|{DisplayName}]]`
   - Related Systems callout is present and non-empty
   - File naming follows convention (`Designer Usage.md`, not `Usage.md`)
   - No duplicate entries in tables

2. **Cross-reference manifest** — run `obsidian_list_notes` on the Documentation folder to verify:
   - No orphaned files from renames (old file still exists)
   - New Hub documents are in the correct location

3. **Report verification results** — do NOT auto-fix verification failures. Present them to the user:
   > "Verification found {N} issues: {list}. These may need manual attention."

---

## Phase 7: REPORT

Update the audit report to reflect what was resolved.

### 7a. Read Current Report
Use `obsidian_read_note` to get the current report content.

### 7b. Mark Resolved Findings
For each finding that was successfully fixed or resolved via ASK:
- Append `✅ RESOLVED ({date})` to the finding's description line in its callout

For findings the user explicitly deferred ("skip"):
- Append `⏸ DEFERRED ({date})` to the finding's description line

In the `## Machine Findings` JSON block, set each affected finding's `status` to `resolved` or `deferred` to match — the block and the prose markers must always agree (the block is the source of truth for the next run).

### 7c. Recompute Statistics
Based on remaining unresolved findings, recalculate:
- Health rating (HEALTHY / MINOR DRIFT / MODERATE DEBT / NEEDS OVERHAUL)
- Finding counts by severity
- Metric percentages where computable

### 7d. Append Changelog Row
Add a new row to the Changelog table:
```
| {date} | {new health} | {remaining critical} | {remaining warning} | {remaining info} | Applied {N} FIX, resolved {M} ASK, deferred {K} |
```

### 7e. Write Updated Report
Use `obsidian_update_note` with `wholeFileMode: "overwrite"` to write the complete updated report in a single call. Regenerate the `## Machine Findings` block from the in-memory findings (updated `status`, recomputed `counts`/`healthRating`) so it remains the authoritative contract for the next run.

### 7f. Final Summary
Present the session summary:
> **Documentation Fix Complete**
> - FIX applied: {N}
> - ASK resolved: {M}
> - Deferred: {K}
> - Remaining findings: {X} ({breakdown by severity})
> - Health rating: {old} → {new}

---

## Phase 8: FOLDER HIERARCHY

Generate a folder reorganization plan so the user can align the physical folder structure with the domain groupings in Start Here.

**Why user-action, not automated:** Obsidian's auto-link-update only triggers when files are moved through its UI (drag-drop or right-click → Move). Programmatic moves (Bash `mv`, Obsidian MCP read/write/delete) are seen as "delete + create" and do NOT update wikilinks. This was empirically tested and confirmed.

### 8a. Read Domain Mapping

Parse Start Here's "By Domain" section to build a map: `domain → [system folder names]`. Cross-check against the parsed audit findings: **D2** (domain misclassification) findings flag systems whose Start-Here domain the audit disputed, and **D6** (promote) findings name the reorg targets. Prefer the audit's verdict where it conflicts with a stale Start Here, rather than independently re-judging domain membership here.

### 8b. Read Folder Structure

Use `obsidian_list_notes` on the Documentation folder at `recursionDepth: 1` to get the current folder tree.

### 8c. Detect Misalignment

This is Phase-8-specific detection — the audit judges doc *navigation*, not physical disk layout, so folder-nesting is decided here. Use the **D6** promote findings (from Phase 1c) as the priority targets: those are the domains the audit already wants promoted to their own folder.

For each domain with 2+ systems, check whether its system folders are nested under a domain parent folder:
- **Aligned:** `Documentation/{Domain}/{SystemFolder}/` — system is inside its domain parent
- **Misaligned:** `Documentation/{SystemFolder}/` — system is a top-level folder but belongs to a domain

**Never moved** (per Folder Classification in `agents/documentation_structure.md`):
- Structural folders (`Claude/`, `Prototypes/`) and `Start Here.md`
- `(Archived)` folders
- Domain parent folders themselves (they are the move TARGETS, not sources)

### 8d. Create Missing Domain Folders

For each domain that has misaligned systems but no parent folder yet:
- Create the folder on disk via Bash `mkdir -p` at the vault path
- This is safe — empty folders have no wikilink impact

Domain folder naming convention: match the Start Here domain header name, using PascalCase without spaces for multi-word names (e.g., "Visual Effects" → `VisualEffects/`, "Physics & Movement" → `PhysicsAndMovement/`, "Jmodot Framework" → `JmodotFramework/`).

### 8e. Present Reorganization Plan

Output a formatted table showing exactly which folders to drag into which domain parent:

```
## Folder Reorganization Plan

The following system folders should be moved into their domain parent folders.
**Action:** In Obsidian's file explorer, drag each system folder into its domain parent.
Obsidian will automatically update all wikilinks.

### SpellPipeline/ (already exists)
- [ ] ReactionSystem/
- [ ] WaveSpellOverhaul/
- [ ] FabledVariantSystem/
- [ ] SiblingCollisionSystem/
- [ ] Pooling/
- [ ] SpellCollision/
- [ ] EmitterTimingSystem/

### AI/ (created)
- [ ] AI Steering System/
- [ ] Affinity System/
- [ ] HSM-BT Critter AI/

### {Domain}/ ({status})
- [ ] {SystemFolder}/
...

### Stays Top-Level (no action needed)
- Start Here.md
- Claude/
- Prototypes/
- SpellReactionSystem (Archived)/
```

**Skip aligned domains:** If all systems in a domain are already nested under their parent folder, omit that domain from the plan.

**Empty orphan detection:** If the folder listing reveals folders that are NOT in any domain's system list and NOT classified as Structural/Archived (per `agents/documentation_structure.md` — content-based classification, no exclusion list), flag them:
```
### Possible Orphan Folders (verify before deleting)
- AIFramework/ — not assigned to any domain
- JmodotModifiers/ — not assigned to any domain
```

### 8f. Naming Consistency Audit

After the hierarchy plan, audit system folder names for consistency. The naming convention should be:

**Rules:**
1. **PascalCase, no spaces** — `ReactionSystem/` not `Reaction System/`
2. **Descriptive but not verbose** — name should identify the system's scope
3. **Consistent suffix pattern** — use `System` suffix when the folder represents a self-contained system (e.g., `ReactionSystem/`, `MovementSystem/`). Omit when the name is already a clear noun (e.g., `Pooling/`, `CombatSubsystem/`)
4. **No redundant prefixes** — don't prefix with the parent domain name (e.g., `AI/SteeringSystem/` not `AI/AISteeringSystem/`)

**Procedure:**
1. List all system folder names from the domain mapping
2. Flag names that violate the rules above
3. Present a rename recommendation table:

```
### Naming Recommendations

| Current Name | Recommended Name | Reason |
|-------------|-----------------|--------|
| AI Steering System | AISteeringSystem (or SteeringSystem under AI/) | Contains spaces |
| HSM-BT Critter AI | CritterAI | Verbose, implementation detail in name |
| Affinity System | AffinitySystem | Contains space |
| {current} | {recommended} | {reason} |

**Action:** Rename in Obsidian (right-click → Rename). Obsidian auto-updates wikilinks on rename.
```

**Important:** Renames MUST be done through Obsidian's UI (right-click → Rename) for the same reason as moves — auto-link-update only triggers through the UI. Programmatic renames do not update wikilinks.

### 8g. No-Op Exit

If all domains are fully aligned AND all names are consistent (no misaligned systems, no naming violations), output:
> "Folder hierarchy and naming are aligned with domain structure. No reorganization needed."

---

## Constraints

- **Obsidian MCP required**: See the `obsidian_conventions` skill for connectivity rules.
- **Idempotent**: Before each inline fix, read the target file and check if the fix is already applied. Skip silently if present.
- **Sequential `/doc_full`**: Never run multiple `/doc_full` invocations in parallel (each spawns 3 subagents and is context-heavy).
- **Cap at 3 delegates**: If >3 systems need `/doc_full` or `/doc_architecture` or `/doc_usage`, process 3 and report "run again for remaining." This prevents context exhaustion.
- **Backward compatible**: Supports both old schema (derive action/handler from check code via routing table) and new schema (read `action`/`handler`/`options` fields directly from the finding).
- **Freshness-aware**: Warn if audit report is >7 days old.
- **No subagents**: This command runs inline as an orchestrator. It delegates to existing commands via `/doc_full`, `/doc_start_here_update`, etc. — those commands handle their own subagent orchestration.
- **Single-report writes**: Always overwrite the full report in one `obsidian_update_note` call — never patch line by line.
- **Preserve report structure**: When updating findings, preserve the prose report content (statistics, domain analysis tables, cross-agent patterns) and regenerate the `## Machine Findings` JSON block with updated `status`/counts. The prose RESOLVED/DEFERRED markers and the JSON `status` must always agree.
