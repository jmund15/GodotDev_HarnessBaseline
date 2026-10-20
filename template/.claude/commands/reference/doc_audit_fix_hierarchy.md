---
disable-model-invocation: true
---

# `/doc_audit_fix` — Phase 8 folder hierarchy steps

Read at `/doc_audit_fix` Phase 8, and when Phase 5 defers a D6 promotion to it. Entrypoint: [`../doc_audit_fix.md`](../doc_audit_fix.md).

### 8a. Read Domain Mapping

Parse Start Here's "By Domain" section to build a map: `domain → [system folder names]`. Cross-check against the parsed audit findings: **D2** (domain misclassification) findings flag systems whose Start-Here domain the audit disputed, and **D6** (promote) findings name the reorg targets. Prefer the audit's verdict where it conflicts with a stale Start Here, rather than independently re-judging domain membership here.

### 8b. Read Folder Structure

Use `Glob` on the Documentation folder to get the current folder tree.

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

**Important:** Renames MUST be done through Obsidian's UI (right-click → Rename) for the same reason as moves — auto-link-update only triggers through the UI.

### 8g. No-Op Exit

If all domains are fully aligned AND all names are consistent (no misaligned systems, no naming violations), output:
> "Folder hierarchy and naming are aligned with domain structure. No reorganization needed."
