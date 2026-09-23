# Existing-doc check (§1)

> Detail body of the brainstorming shared surface, read at the step named in [`common.md`](common.md).
> Section numbers are stable across the split; a § cited here that is not in this file is mapped to its file by the `common.md` index.

---

## §1. Existing-doc check (FIRST step in both skills)

**Rule:** Before any other procedure step, establish what already exists. This section is the **spec** — the asks that must be answered and why each needs its own surface. [`/explore`](../../commands/explore.md) is the **execution**: it dispatches them as parallel lenses with explicit pins, an evidence contract, and a falsifiable-empty guarantee.

**Required asks, and the lens that owns each** (roster + trigger rules: [`explore_agents.md`](../../commands/agents/explore_agents.md)):

| Ask | Why it needs its own surface | Lens |
|---|---|---|
| Does a brainstorm/design doc already cover this topic? | The vault is the design source of truth; a covering doc makes brainstorming-from-scratch duplicated effort | `exp-design-source` |
| Which memorialized gotchas and `arch_rule_*` invariants bear on it? | Memory lives in `.claude/auto-memory/`, which no vault digest can see | `exp-memory` (floor) |
| Does an existing 2+ family already own the concern? | Families live in code, not docs — and this is the ask whose silent failure produces a parallel abstraction | `exp-prior-art` (floor) |

`/architecture_brainstorm` Step 3 still owns the *full* abstraction-inventory procedure; `exp-prior-art` at §1 time is what surfaces an obvious owner early enough to reshape the topic. `/idea_brainstorm` keeps both floor lenses; prior-art records existing ownership without constraining idea generation.

**Dispatch:** `/explore` with `TOPIC` = the brainstorm topic. It reads the budget band, evaluates triggers, and reports UNCOVERED dimensions explicitly — an empty result from a lens that never ran is flagged rather than read as "nothing exists".

Two asks stay inline, because neither earns a lens:

1. **Skim recent commits** (`git log --oneline -10`) for related work in flight — a Bash one-liner, not a synthesis call.
2. **If the dossier names a relevant existing doc:** redirect the user — *"`<path>` (status: `<status>`) covers this. Want me to read it and resume from there?"* — and STOP. This is a decision, and decisions stay with the orchestrator.

### §1.1 If user accepts "resume from there"

Resume procedure depends on the doc's `status:` frontmatter:

| Status | Resume action |
|---|---|
| `ideation-active` | Resume mid-procedure in `/idea_brainstorm`. Read the doc's existing cluster surveys; pick up at the next cluster or at user-flagged open questions. |
| `ideation-complete` | Idea bank is settled. User probably wants to convert ideas into architecture — hand off to `/architecture_brainstorm`. |
| `active-brainstorming` | Resume mid-procedure in `/architecture_brainstorm`. Re-read any open Parts on roadmap.md with `*-pending` / `*-rework` state; treat their Triggers as Socratic seeds. Don't restart from scratch. |
| `brainstorming-complete` | Doc is approved; check the topic-folder `roadmap.md` for Parts. If `plan-pending` Parts exist, user likely asking for IMPLEMENTATION — redirect to the drive command that owns plan production ([`/part_drive`](../../commands/part_drive.md) — `--plan-only` for plan-to-approval, unflagged for plan-through-ship) or the relevant authoring skill. If all Parts are `*-pending` / `*-rework`, the next session is the corresponding brainstorm phase per State (`arch-pending` → `/architecture_brainstorm`; `idea-pending` / `idea-rework` → `/idea_brainstorm`). If the roadmap predates the current skill state, route via §1.2 (stale-roadmap remediation) instead. |

**The frontier outranks the status.** A topic folder whose `decisions.md` has a non-empty `## Frontier` resumes from those entries whatever the doc's `status:` — the status row above then picks the procedure, not the starting point. Read `decisions.md` before the design doc (§8).

**Status match strips any `-vN.M` revision suffix.** A doc with `status: brainstorming-complete-v1.2` matches the `brainstorming-complete` row. The suffix records authoring iteration (per §5 *Revising a saved doc*), not a separate lifecycle state.

Doc-revision discipline still applies (in-place rewrite, not v1.1 footers — see each skill's save step).

### §1.2 Stale-roadmap remediation

A `brainstorming-complete` doc whose `roadmap.md` predates the current skill state — e.g., `plan-pending` Parts with empty / boilerplate Trigger, `last_revised` older than the calling SKILL's last-modified date, or Part names following an older naming convention — is not safe to resume from directly. Re-run `architecture_brainstorm` Step 5 against the existing design body before any new Parts land.

**Procedure:**

1. Read the existing design doc (treat as approved input — Steps 1–4 of the arch SKILL are skipped).
2. Apply the full Step 5 sequence to the design body: PR-grouping guard → arch-pending consolidation litmus → user-owned question → 5-criterion readiness gate → plan-pending cohesion litmus → downstream-of-fork guard → Trigger format per §6.10 → Integration touch points.
3. Hand the resulting fresh Part list to `/update_roadmap` (it replaces the stale Parts table in a single batch diff).

**Why this exists:** Parts authored under an older sizing axis (pre-consolidation-litmus, pre-downstream-of-fork guard, etc.) inherit the wrong shape silently. Re-importing them into the current skill state without re-gating bakes the prior axis into the new roadmap.

**Forbidden in §1:**

- ❌ `Grep("<topic>", path=<vault dir>)` followed by `Read(path)` chains
- ❌ Multiple `mcp__plugin_semantic-search_semantic-search__search` calls across keyword variants
- ❌ `Read(path)` on Obsidian docs to "see what's there before bundling" (synthesis-shaped path; routes through `read_files`)
- ❌ `read_files(paths=["<directory>/"])` — directory paths return nothing, silently; enumerate concrete files first (`feedback_read_files_enumerate_first.md`)
- ❌ Asking the worker for Memory gotchas or code-abstraction inventory — those stores aren't in the vault paths it reads; each ask goes to the lens that owns it per the table above
- ❌ Running the sweep inline instead of dispatching `/explore` — the engine's evidence contract and UNCOVERED-dimension reporting are required.
